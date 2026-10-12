"""Collection building blocks: the polite fetcher, the source-adapter interface,
and personal-data scrubbing.

Gentle by construction:
- one request at a time, with a minimum delay between requests to the same host
  (never shorter than the site's Crawl-delay);
- robots.txt is checked before every URL, and failing to read it means "no";
- a hard cap on requests per run;
- the run stops at once if the site answers 429 or 503 (we must never be a burden);
- the bot identifies itself with the user agent from .env.
"""
from __future__ import annotations

import importlib
import logging
import pkgutil
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Callable, Iterator
from urllib.parse import urlsplit

import httpx

from marketplace_scanner.collectors.robots import RobotsGate

log = logging.getLogger(__name__)


class RobotsBlocked(Exception):
    """robots.txt does not allow this URL."""


class SourceOverloaded(Exception):
    """The site answered 429/503; stop the run immediately."""


class RequestLimitReached(Exception):
    """The per-run request cap was reached."""


class DiscoveryError(Exception):
    """A listing overview page could not be read, so the run cannot be called complete."""


@dataclass
class FetchResult:
    url: str
    status: int | None          # None = network error
    text: str
    elapsed_ms: int
    raw_page_id: int | None = None


class PoliteFetcher:
    def __init__(
        self,
        user_agent: str,
        *,
        min_delay: float = 5.0,
        max_requests: int = 300,
        timeout: float = 15.0,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        on_page: Callable[[str, int, str], int | None] | None = None,
    ) -> None:
        self.client = client or httpx.Client(
            headers={"User-Agent": user_agent}, timeout=timeout, follow_redirects=True
        )
        self.min_delay = min_delay
        self.max_requests = max_requests
        self.on_page = on_page  # called for every successful page; may return a raw_page id
        self._sleep = sleep
        self._clock = clock
        self._last_request: dict[str, float] = {}
        self.gate = RobotsGate(user_agent, self.client, on_request=self._touch)
        self.requests = 0
        self.errors = 0
        self._total_ms = 0

    # -- pacing -------------------------------------------------------------
    def _touch(self, host: str) -> None:
        self._last_request[host] = self._clock()

    def _pace(self, host: str, delay: float) -> None:
        last = self._last_request.get(host)
        if last is not None:
            wait = delay - (self._clock() - last)
            if wait > 0:
                self._sleep(wait)

    # -- stats --------------------------------------------------------------
    @property
    def avg_response_ms(self) -> int | None:
        return round(self._total_ms / self.requests) if self.requests else None

    # -- fetching -----------------------------------------------------------
    def get(self, url: str) -> FetchResult:
        if self.requests >= self.max_requests:
            raise RequestLimitReached(f"request cap of {self.max_requests} reached")
        host = urlsplit(url).netloc
        self._pace(host, self.min_delay)
        if not self.gate.allowed(url):
            raise RobotsBlocked(url)
        self._pace(host, max(self.min_delay, self.gate.crawl_delay(url) or 0))

        started = self._clock()
        try:
            response = self.client.get(url)
        except httpx.HTTPError as exc:
            self.requests += 1
            self.errors += 1
            self._touch(host)
            log.warning("request failed: %s (%r)", url, exc)
            return FetchResult(url, None, "", round((self._clock() - started) * 1000))
        elapsed = round((self._clock() - started) * 1000)
        self.requests += 1
        self._total_ms += elapsed
        self._touch(host)

        if response.status_code in (429, 503):
            self.errors += 1
            raise SourceOverloaded(f"{url} answered {response.status_code}")
        if response.status_code >= 400:
            self.errors += 1
            return FetchResult(url, response.status_code, "", elapsed)
        raw_page_id = self.on_page(url, response.status_code, response.text) if self.on_page else None
        return FetchResult(url, response.status_code, response.text, elapsed, raw_page_id)

    def get_ok(self, url: str) -> FetchResult:
        """Like get(), but raise DiscoveryError unless the page was fetched (HTTP 200)."""
        result = self.get(url)
        if result.status != 200:
            raise DiscoveryError(f"{url} returned {result.status}")
        return result


# -- personal data (GDPR) ---------------------------------------------------
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
# Phone numbers only: start with + or 0 so prices such as "1 200 000 kr" are left alone.
_PHONE = re.compile(r"(?<!\d)(?:\+\d{1,3}|0\d{1,3})[\s.-]?\d{2,4}(?:[\s.-]?\d{2,4}){1,3}(?!\d)")
PII_FIELDS = ("title", "description", "seller_raw")


def scrub_personal_data(text: str | None) -> str | None:
    """Best-effort removal of e-mail addresses and phone numbers from free text."""
    if not text:
        return text
    return _PHONE.sub("[phone removed]", _EMAIL.sub("[email removed]", text))


def scrub_fields(fields: dict) -> dict:
    cleaned = dict(fields)
    for name in PII_FIELDS:
        if isinstance(cleaned.get(name), str):
            cleaned[name] = scrub_personal_data(cleaned[name])
    return cleaned


# -- source adapters --------------------------------------------------------
@dataclass
class DiscoveredListing:
    """A listing found on an overview page, with whatever fields that page shows."""

    source_listing_id: str
    url: str
    fields: dict = field(default_factory=dict)


class SourceAdapter(ABC):
    """One adapter per marketplace. See docs/source-onboarding.md."""

    slug: str                       # must equal the slug in the register
    parser_version: str = "1"       # bump whenever the parsing logic changes
    min_delay: float = 5.0          # seconds between requests to the site
    max_requests: int = 300         # hard cap per run
    detail_pages: bool = True       # fetch each new/changed listing's own page
    required_fields: tuple[str, ...] = ("title", "price_raw", "category_raw")

    @abstractmethod
    def discover(self, fetcher: PoliteFetcher, base_url: str) -> Iterator[DiscoveredListing]:
        """Yield every listing on the site, reading overview pages (use fetcher.get_ok)."""

    def parse_listing(self, html: str, url: str) -> dict | None:
        """Parse a listing's own page into raw fields. Return None if it cannot be read."""
        return None


ADAPTERS: dict[str, SourceAdapter] = {}


def register(cls: type[SourceAdapter]) -> type[SourceAdapter]:
    ADAPTERS[cls.slug] = cls()
    return cls


def load_adapters() -> dict[str, SourceAdapter]:
    """Import every module in collectors/sources so their adapters register themselves."""
    from marketplace_scanner.collectors import sources

    for module in pkgutil.iter_modules(sources.__path__):
        importlib.import_module(f"{sources.__name__}.{module.name}")
    return dict(ADAPTERS)
