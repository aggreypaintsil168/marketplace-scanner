"""Runtime robots.txt guard used by the fetcher.

Unlike registry/verification.py (a one-off check when a site is reviewed), this
runs during every collection: robots.txt is fetched once per host per run and
every URL is checked against it before it is requested.

Fail closed: if robots.txt cannot be read reliably, nothing is fetched.
"""
from __future__ import annotations

import logging
from typing import Callable
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx

log = logging.getLogger(__name__)

_ALLOW_ALL = "allow_all"
_DENY_ALL = "deny_all"


def _origin(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


class RobotsGate:
    def __init__(self, user_agent: str, client: httpx.Client,
                 on_request: Callable[[str], None] | None = None) -> None:
        self.user_agent = user_agent
        self.client = client
        self.on_request = on_request  # told when a robots.txt request was made (for pacing)
        self._rules: dict[str, object] = {}

    def _load(self, origin: str) -> object:
        try:
            response = self.client.get(origin + "/robots.txt")
        except httpx.HTTPError as exc:
            log.warning("robots.txt unreachable for %s (%r): treating as disallowed", origin, exc)
            return _DENY_ALL
        finally:
            if self.on_request:
                self.on_request(urlsplit(origin).netloc)
        if response.status_code == 200:
            parser = RobotFileParser()
            parser.parse(response.text.splitlines())
            return parser
        if response.status_code == 404:
            return _ALLOW_ALL  # no robots.txt: nothing is disallowed (terms still apply)
        log.warning("robots.txt for %s returned %s: treating as disallowed", origin, response.status_code)
        return _DENY_ALL

    def _rule(self, url: str) -> object:
        origin = _origin(url)
        if origin not in self._rules:
            self._rules[origin] = self._load(origin)
        return self._rules[origin]

    def allowed(self, url: str) -> bool:
        rule = self._rule(url)
        if rule == _ALLOW_ALL:
            return True
        if rule == _DENY_ALL:
            return False
        return rule.can_fetch(self.user_agent, url)

    def crawl_delay(self, url: str) -> float | None:
        rule = self._rule(url)
        if isinstance(rule, RobotFileParser):
            delay = rule.crawl_delay(self.user_agent)
            return float(delay) if delay else None
        return None
