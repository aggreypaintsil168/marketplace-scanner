"""History, collection and the end-to-end pipeline (against a fake site, no real network)."""
from datetime import date, datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from marketplace_scanner.collectors.base import (
    PoliteFetcher,
    RequestLimitReached,
    RobotsBlocked,
    SourceOverloaded,
    load_adapters,
    scrub_personal_data,
)
from marketplace_scanner.history.events import ingest_observation, mark_removed
from marketplace_scanner.registry.models import Base, Marketplace
from marketplace_scanner.scheduler import collect_source
from marketplace_scanner.storage.models import (
    HealthCheck,
    Listing,
    ListingEvent,
    ListingObservation,
    RawPage,
)
from marketplace_scanner.storage.repository import purge_raw_pages

UA = "TestBot/0.1 (+https://example.org; contact: test@example.org)"
NOW = datetime(2026, 10, 12, 8, 0, tzinfo=timezone.utc)
D1, D2, D3 = date(2026, 10, 12), date(2026, 10, 13), date(2026, 10, 14)


def make_session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return Session(engine)


def add_marketplace(session, status="enabled", slug="example-site", url="https://site.test"):
    m = Marketplace(slug=slug, name="Example", type="general", url=url, collection_status=status)
    session.add(m)
    session.commit()
    return m


def ingest(session, m, listing_id, fields, today, url="https://site.test/l/x"):
    return ingest_observation(session, marketplace_id=m.id, source_listing_id=listing_id, url=url,
                              fields=fields, today=today, observed_at=NOW)


# --- history ------------------------------------------------------------------
def test_new_listing_gets_observation_and_new_event():
    with make_session() as s:
        m = add_marketplace(s)
        assert ingest(s, m, "1", {"title": "Cafe", "price_raw": "100 kr"}, D1) == "new"
        s.commit()
        listing = s.scalar(select(Listing))
        assert (listing.first_seen, listing.last_seen, listing.removed_on) == (D1, D1, None)
        assert s.query(ListingObservation).count() == 1
        assert [e.event_type for e in s.query(ListingEvent)] == ["new"]


def test_unchanged_listing_only_moves_last_seen():
    with make_session() as s:
        m = add_marketplace(s)
        ingest(s, m, "1", {"title": "Cafe", "price_raw": "100 kr"}, D1)
        assert ingest(s, m, "1", {"title": "Cafe", "price_raw": "100 kr"}, D2) == "unchanged"
        s.commit()
        assert s.scalar(select(Listing)).last_seen == D2
        assert s.query(ListingObservation).count() == 1
        assert s.query(ListingEvent).count() == 1


def test_price_change_logs_old_and_new_value():
    with make_session() as s:
        m = add_marketplace(s)
        ingest(s, m, "1", {"title": "Cafe", "price_raw": "100 kr"}, D1)
        assert ingest(s, m, "1", {"title": "Cafe", "price_raw": "90 kr"}, D2) == "changed"
        s.commit()
        event = s.scalar(select(ListingEvent).where(ListingEvent.event_type == "changed"))
        assert (event.field, event.old_value, event.new_value, event.event_date) == ("price_raw", "100 kr", "90 kr", D2)
        assert s.query(ListingObservation).count() == 2


def test_removed_then_reappeared():
    with make_session() as s:
        m = add_marketplace(s)
        ingest(s, m, "1", {"title": "A"}, D1)
        ingest(s, m, "2", {"title": "B"}, D1)
        assert mark_removed(s, m.id, {"2"}, D2) == 1
        s.commit()
        assert s.scalar(select(Listing).where(Listing.source_listing_id == "1")).removed_on == D2
        assert ingest(s, m, "1", {"title": "A"}, D3) == "reappeared"
        s.commit()
        assert s.scalar(select(Listing).where(Listing.source_listing_id == "1")).removed_on is None


def test_removal_guards():
    with make_session() as s:
        m = add_marketplace(s)
        for i in range(12):
            ingest(s, m, str(i), {"title": f"L{i}"}, D1)
        assert mark_removed(s, m.id, set(), D2) == 0                       # nothing seen: no removals
        assert mark_removed(s, m.id, {"0", "1", "2"}, D2) == 0             # 75% missing: looks like a break
        assert mark_removed(s, m.id, {str(i) for i in range(10)}, D2) == 2  # ordinary removals pass


# --- personal data ------------------------------------------------------------
def test_scrub_removes_phones_and_emails_but_keeps_prices():
    cleaned = scrub_personal_data("Ring +46 70 123 45 67 / 070-123 45 67 eller a.b@firma.se. Pris 1 200 000 kr")
    assert "46 70" not in cleaned and "070-123" not in cleaned and "@" not in cleaned
    assert "1 200 000 kr" in cleaned


# --- fetcher ------------------------------------------------------------------
class FakeClock:
    def __init__(self):
        self.now = 1000.0
        self.sleeps = []

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def fetcher_for(handler, **kwargs):
    clock = FakeClock()
    client = httpx.Client(transport=httpx.MockTransport(handler), headers={"User-Agent": UA})
    return PoliteFetcher(UA, client=client, sleep=clock.sleep, clock=clock.clock, **kwargs), clock


def test_fetcher_respects_robots_disallow():
    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /private")
        return httpx.Response(200, text="ok")

    fetcher, _ = fetcher_for(handler)
    assert fetcher.get("https://site.test/open").status == 200
    with pytest.raises(RobotsBlocked):
        fetcher.get("https://site.test/private/1")


def test_fetcher_fails_closed_when_robots_unreadable():
    fetcher, _ = fetcher_for(lambda request: httpx.Response(500, text="boom"))
    with pytest.raises(RobotsBlocked):
        fetcher.get("https://site.test/page")


def test_fetcher_allows_when_no_robots_file():
    def handler(request):
        return httpx.Response(404 if request.url.path == "/robots.txt" else 200, text="page")

    fetcher, _ = fetcher_for(handler)
    assert fetcher.get("https://site.test/page").text == "page"


def test_fetcher_waits_between_requests_and_honours_crawl_delay():
    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nCrawl-delay: 10")
        return httpx.Response(200, text="ok")

    fetcher, clock = fetcher_for(handler, min_delay=2)
    fetcher.get("https://site.test/a")
    fetcher.get("https://site.test/b")
    assert max(clock.sleeps) >= 10 - 0.001   # never faster than the site's Crawl-delay


def test_fetcher_stops_on_429_and_on_request_cap():
    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(429 if request.url.path == "/busy" else 200, text="ok")

    fetcher, _ = fetcher_for(handler, max_requests=2)
    with pytest.raises(SourceOverloaded):
        fetcher.get("https://site.test/busy")
    fetcher.get("https://site.test/a")
    with pytest.raises(RequestLimitReached):
        fetcher.get("https://site.test/b")


# --- end to end against a fake site -------------------------------------------
class FakeSite:
    """Serves overview pages, listing pages and robots.txt; counts listing-page requests."""

    def __init__(self):
        self.items = {}         # id -> (title, price)
        self.detail_hits = []
        self.overview_status = 200

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /admin")
        if path == "/listings":
            if self.overview_status != 200:
                return httpx.Response(self.overview_status, text="error")
            page = request.url.params.get("page", "1")
            cards = "".join(
                f'<div class="listing" data-id="{i}"><a href="/l/{i}">{t}</a><span class="price">{p}</span></div>'
                for i, (t, p) in self.items.items()
            ) if page == "1" else ""
            return httpx.Response(200, text=f"<html><body>{cards}</body></html>")
        if path.startswith("/l/"):
            listing_id = path.split("/")[-1]
            self.detail_hits.append(listing_id)
            title, price = self.items[listing_id]
            return httpx.Response(200, text=(
                f'<html><body><h1>{title}</h1><span class="price">{price}</span>'
                '<div class="category">Retail</div><div class="location">Stockholm</div>'
                '<div class="description">Call 070-123 45 67 or mail owner@firm.se</div></body></html>'
            ))
        return httpx.Response(404)


def run_day(session, site, today, raw_dir):
    adapter = load_adapters()["example-site"]
    marketplace = session.scalar(select(Marketplace).where(Marketplace.slug == "example-site"))
    client = httpx.Client(transport=httpx.MockTransport(site.handler), headers={"User-Agent": UA})
    fetcher = PoliteFetcher(UA, min_delay=0, client=client, sleep=lambda s: None)
    return collect_source(session, marketplace, adapter, fetcher, raw_dir=raw_dir, today=today)


def test_two_day_pipeline_new_changed_removed_and_incremental(tmp_path):
    site = FakeSite()
    with make_session() as s:
        add_marketplace(s)
        site.items = {"1": ("Cafe", "100 kr"), "2": ("Shop", "200 kr"), "3": ("Gym", "300 kr")}
        day1 = run_day(s, site, D1, tmp_path)
        assert (day1.status, day1.listings_seen, day1.listings_new) == ("ok", 3, 3)
        assert sorted(site.detail_hits) == ["1", "2", "3"]

        site.detail_hits.clear()
        site.items = {"1": ("Cafe", "100 kr"), "2": ("Shop", "180 kr"), "4": ("Bakery", "400 kr")}
        day2 = run_day(s, site, D2, tmp_path)
        assert (day2.listings_new, day2.listings_changed, day2.listings_removed) == (1, 1, 1)
        assert sorted(site.detail_hits) == ["2", "4"]          # listing 1 unchanged: page not re-fetched

        events = {(e.event_type, e.field) for e in s.query(ListingEvent)}
        assert ("changed", "price_raw") in events and ("removed", None) in events
        gym = s.scalar(select(Listing).where(Listing.source_listing_id == "3"))
        assert gym.removed_on == D2
        assert s.scalar(select(Listing).where(Listing.source_listing_id == "1")).last_seen == D2

        descriptions = [o.description for o in s.query(ListingObservation)]
        assert all("070" not in d and "@" not in d for d in descriptions)   # personal data scrubbed

        pages = s.query(RawPage).all()
        assert pages and all((tmp_path / p.storage_path).exists() for p in pages)


def test_broken_overview_page_means_incomplete_run_and_no_removals(tmp_path):
    site = FakeSite()
    with make_session() as s:
        add_marketplace(s)
        site.items = {"1": ("Cafe", "100 kr")}
        run_day(s, site, D1, tmp_path)
        site.overview_status = 500
        broken = run_day(s, site, D2, tmp_path)
        assert broken.complete is False and broken.status == "failed"
        assert s.scalar(select(Listing)).removed_on is None
        assert "fetch_failed" in {h.flag for h in s.query(HealthCheck)}


def test_only_enabled_sources_can_be_collected(tmp_path):
    with make_session() as s:
        add_marketplace(s, status="pending")
        with pytest.raises(PermissionError):
            run_day(s, FakeSite(), D1, tmp_path)


def test_purge_removes_old_raw_copies(tmp_path):
    site = FakeSite()
    with make_session() as s:
        add_marketplace(s)
        site.items = {"1": ("Cafe", "100 kr")}
        run_day(s, site, D1, tmp_path)
        files_before = list(tmp_path.rglob("*.html.gz"))
        assert files_before
        removed = purge_raw_pages(s, tmp_path, datetime.now(timezone.utc) + timedelta(days=1))
        assert removed > 0 and s.query(RawPage).count() == 0
        assert not list(tmp_path.rglob("*.html.gz"))
        assert s.query(ListingObservation).count() == 1      # the data stays, only the page copy goes
