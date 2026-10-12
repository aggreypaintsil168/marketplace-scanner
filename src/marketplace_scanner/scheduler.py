"""One collection run for one marketplace, and a runner for all enabled ones.

Order of events in collect_source:
  discover listings -> fetch only new/changed listings' pages -> scrub personal data
  -> record observations/events -> mark removals (complete runs only)
  -> store run statistics -> health check.

Run daily with the operating system's scheduler (see docs/architecture.md); give
each source its own time slot so the load is spread over the day.
"""
from __future__ import annotations

import logging
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from marketplace_scanner.collectors.base import (
    PoliteFetcher,
    SourceAdapter,
    scrub_fields,
)
from marketplace_scanner.health.checks import run_health_check
from marketplace_scanner.history.events import (
    ingest_observation,
    latest_observation,
    mark_removed,
    normalise_fields,
    observation_fields,
)
from marketplace_scanner.registry.models import Marketplace
from marketplace_scanner.storage.models import FetchRun, Listing
from marketplace_scanner.storage.repository import save_raw_page

log = logging.getLogger(__name__)


def _known_fields(session: Session, marketplace_id: int, source_listing_id: str) -> dict | None:
    """Latest stored fields of a listing, or None if the listing is new to us."""
    listing = session.scalar(
        select(Listing).where(Listing.marketplace_id == marketplace_id,
                              Listing.source_listing_id == source_listing_id)
    )
    if listing is None:
        return None
    observation = latest_observation(session, listing.id)
    return observation_fields(observation) if observation else None


def collect_source(
    session: Session,
    marketplace: Marketplace,
    adapter: SourceAdapter,
    fetcher: PoliteFetcher,
    *,
    raw_dir: Path | str,
    today: date | None = None,
) -> FetchRun:
    if marketplace.collection_status != "enabled":
        raise PermissionError(
            f"{marketplace.slug} has collection_status '{marketplace.collection_status}'; "
            "only reviewed, enabled sources may be fetched"
        )
    today = today or date.today()
    now = datetime.now(timezone.utc)
    run = FetchRun(marketplace_id=marketplace.id, started_at=now, parser_version=adapter.parser_version,
                   status="running", complete=False)
    session.add(run)
    session.flush()

    def keep_raw_copy(url: str, status: int, text: str) -> int:
        page = save_raw_page(session, marketplace_id=marketplace.id, slug=marketplace.slug, url=url,
                             status=status, text=text, fetched_at=datetime.now(timezone.utc),
                             raw_dir=raw_dir)
        return page.id

    fetcher.on_page = keep_raw_copy
    seen: set[str] = set()
    outcomes: Counter = Counter()
    readable: Counter = Counter()   # per required field: listings where it could be read
    parsed = 0
    complete = False
    error: str | None = None

    try:
        for found in adapter.discover(fetcher, marketplace.url):
            if found.source_listing_id in seen:
                continue
            seen.add(found.source_listing_id)
            known = _known_fields(session, marketplace.id, found.source_listing_id)
            listing_fields = normalise_fields(found.fields)
            raw_page_id = None

            unchanged_on_overview = known is not None and all(
                known.get(name) == listing_fields[name] for name in found.fields if name in listing_fields
            )
            if unchanged_on_overview:
                fields = known                       # nothing new: no need to fetch its page
            elif adapter.detail_pages:
                page = fetcher.get(found.url)
                parsed_fields = adapter.parse_listing(page.text, found.url) if page.status == 200 else None
                if parsed_fields is None:
                    if known is None:
                        log.warning("could not read new listing %s; will retry next run", found.url)
                        outcomes["unreadable"] += 1
                        continue
                    fields = known                   # keep what we had; still counts as seen
                else:
                    fields = {**found.fields, **parsed_fields}
                    raw_page_id = page.raw_page_id
            else:
                fields = found.fields

            fields = scrub_fields(fields)
            parsed += 1
            for name in adapter.required_fields:
                if fields.get(name) not in (None, ""):
                    readable[name] += 1
            outcome = ingest_observation(
                session, marketplace_id=marketplace.id, source_listing_id=found.source_listing_id,
                url=found.url, fields=fields, today=today, observed_at=datetime.now(timezone.utc),
                run_id=run.id, raw_page_id=raw_page_id, parser_version=adapter.parser_version,
            )
            outcomes[outcome] += 1
        complete = True
    except Exception as exc:  # stop signals, layout crashes, network trouble: record and carry on
        error = f"{type(exc).__name__}: {exc}"
        log.exception("collection for %s stopped early", marketplace.slug)

    removed = mark_removed(session, marketplace.id, seen, today, run_id=run.id) if complete else 0

    run.finished_at = datetime.now(timezone.utc)
    run.complete = complete
    run.status = "ok" if complete else ("partial" if seen else "failed")
    run.error = error
    run.pages_fetched = fetcher.requests
    run.http_errors = fetcher.errors
    run.avg_response_ms = fetcher.avg_response_ms
    run.listings_seen = len(seen)
    run.listings_new = outcomes["new"]
    run.listings_changed = outcomes["changed"] + outcomes["reappeared"]
    run.listings_removed = removed
    run.field_fill_rate = {name: round(readable[name] / parsed, 3) for name in adapter.required_fields} if parsed else {}
    run_health_check(session, run, today=today)
    session.commit()
    return run


def run_all(session: Session, adapters: dict[str, SourceAdapter], user_agent: str,
            raw_dir: Path | str, only_slug: str | None = None) -> list[FetchRun]:
    """Collect from every enabled marketplace that has an adapter."""
    query = select(Marketplace).where(Marketplace.collection_status == "enabled").order_by(Marketplace.slug)
    if only_slug:
        query = query.where(Marketplace.slug == only_slug)
    runs = []
    for marketplace in session.scalars(query).all():
        adapter = adapters.get(marketplace.slug)
        if adapter is None:
            log.info("no adapter for %s yet, skipping", marketplace.slug)
            continue
        fetcher = PoliteFetcher(user_agent, min_delay=adapter.min_delay, max_requests=adapter.max_requests)
        runs.append(collect_source(session, marketplace, adapter, fetcher, raw_dir=raw_dir))
    return runs
