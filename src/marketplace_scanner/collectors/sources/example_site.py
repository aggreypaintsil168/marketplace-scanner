"""TEMPLATE adapter for a made-up site. Copy this file to add a real marketplace.

It is not tied to any real website and never runs unless a marketplace with the
slug 'example-site' exists in the register with collection_status 'enabled'.
Steps are in docs/source-onboarding.md.

Assumed layout of the imaginary site:
  overview  {base}/listings?page=N   -> <div class="listing" data-id="..."><a href=...>Title</a>
                                        <span class="price">...</span></div>
  detail    one page per listing     -> h1, .price, .category, .location, .description, .broker
"""
from __future__ import annotations

from typing import Iterator
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from marketplace_scanner.collectors.base import (
    DiscoveredListing,
    PoliteFetcher,
    SourceAdapter,
    register,
)


def _text(soup: BeautifulSoup, selector: str) -> str | None:
    element = soup.select_one(selector)
    return element.get_text(" ", strip=True) if element else None


@register
class ExampleSite(SourceAdapter):
    slug = "example-site"
    parser_version = "1"
    min_delay = 5.0
    max_requests = 300
    detail_pages = True
    required_fields = ("title", "price_raw", "category_raw")
    max_pages = 50

    def discover(self, fetcher: PoliteFetcher, base_url: str) -> Iterator[DiscoveredListing]:
        for page in range(1, self.max_pages + 1):
            result = fetcher.get_ok(f"{base_url.rstrip('/')}/listings?page={page}")
            cards = BeautifulSoup(result.text, "html.parser").select("div.listing")
            if not cards:
                break
            for card in cards:
                link = card.select_one("a")
                if link is None or not card.get("data-id"):
                    continue
                price = card.select_one(".price")
                yield DiscoveredListing(
                    source_listing_id=card["data-id"],
                    url=urljoin(base_url, link["href"]),
                    fields={
                        "title": link.get_text(strip=True),
                        "price_raw": price.get_text(strip=True) if price else None,
                    },
                )

    def parse_listing(self, html: str, url: str) -> dict | None:
        soup = BeautifulSoup(html, "html.parser")
        if soup.select_one("h1") is None:
            return None  # layout not recognised
        return {
            "title": _text(soup, "h1"),
            "description": _text(soup, ".description"),
            "price_raw": _text(soup, ".price"),
            "category_raw": _text(soup, ".category"),
            "location_raw": _text(soup, ".location"),
            "seller_raw": _text(soup, ".broker"),
            "image_count": len(soup.select("img.photo")),
            "has_prospectus": soup.select_one("a.prospectus") is not None,
        }
