"""History: turn each day's observations into first/last seen plus
new / changed / removed / reappeared events.

A new observation row is written only when a listing is new or has changed,
so storage stays small; last_seen on the listing records that it was still there.
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from marketplace_scanner.storage.models import (
    OBSERVED_FIELDS,
    Listing,
    ListingEvent,
    ListingObservation,
)

log = logging.getLogger(__name__)

MAX_EVENT_VALUE = 500          # event old/new values are cut to this many characters
REMOVAL_GUARD_MIN_ACTIVE = 10  # the mass-removal guard only applies from this many listings


@dataclass(frozen=True)
class FieldChange:
    field: str
    old: object
    new: object


def _clean(value):
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value


def normalise_fields(fields: dict) -> dict:
    """Keep exactly the observed fields; blank text becomes None."""
    return {name: _clean(fields.get(name)) for name in OBSERVED_FIELDS}


def content_hash(fields: dict) -> str:
    payload = json.dumps(normalise_fields(fields), sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def diff_fields(old: dict, new: dict) -> list[FieldChange]:
    old_n, new_n = normalise_fields(old), normalise_fields(new)
    return [FieldChange(n, old_n[n], new_n[n]) for n in OBSERVED_FIELDS if old_n[n] != new_n[n]]


def observation_fields(observation: ListingObservation) -> dict:
    return {name: getattr(observation, name) for name in OBSERVED_FIELDS}


def latest_observation(session: Session, listing_id: int) -> ListingObservation | None:
    return session.scalar(
        select(ListingObservation)
        .where(ListingObservation.listing_id == listing_id)
        .order_by(ListingObservation.id.desc())
        .limit(1)
    )


def _event_text(value) -> str | None:
    if value is None:
        return None
    return str(value)[:MAX_EVENT_VALUE]


def _add_observation(session, listing, fields, *, run_id, raw_page_id, today, observed_at, parser_version):
    session.add(
        ListingObservation(
            listing_id=listing.id,
            run_id=run_id,
            raw_page_id=raw_page_id,
            observed_on=today,
            observed_at=observed_at,
            parser_version=parser_version,
            content_hash=content_hash(fields),
            **fields,
        )
    )


def ingest_observation(
    session: Session,
    *,
    marketplace_id: int,
    source_listing_id: str,
    url: str,
    fields: dict,
    today: date,
    observed_at: datetime,
    run_id: int | None = None,
    raw_page_id: int | None = None,
    parser_version: str | None = None,
) -> str:
    """Record that a listing was seen today. Returns new | changed | reappeared | unchanged."""
    fields = normalise_fields(fields)
    extra = dict(run_id=run_id, raw_page_id=raw_page_id, today=today,
                 observed_at=observed_at, parser_version=parser_version)
    listing = session.scalar(
        select(Listing).where(
            Listing.marketplace_id == marketplace_id,
            Listing.source_listing_id == source_listing_id,
        )
    )
    if listing is None:
        listing = Listing(marketplace_id=marketplace_id, source_listing_id=source_listing_id,
                          url=url, first_seen=today, last_seen=today)
        session.add(listing)
        session.flush()
        _add_observation(session, listing, fields, **extra)
        session.add(ListingEvent(listing_id=listing.id, run_id=run_id, event_date=today, event_type="new"))
        return "new"

    outcome = "unchanged"
    listing.last_seen = today
    listing.url = url
    if listing.removed_on is not None:
        listing.removed_on = None
        session.add(ListingEvent(listing_id=listing.id, run_id=run_id, event_date=today,
                                 event_type="reappeared"))
        outcome = "reappeared"

    previous = latest_observation(session, listing.id)
    changes = diff_fields(observation_fields(previous), fields) if previous else []
    if previous is None or changes:
        _add_observation(session, listing, fields, **extra)
        for change in changes:
            session.add(ListingEvent(
                listing_id=listing.id, run_id=run_id, event_date=today, event_type="changed",
                field=change.field, old_value=_event_text(change.old), new_value=_event_text(change.new),
            ))
        if changes and outcome == "unchanged":
            outcome = "changed"
    return outcome


def mark_removed(
    session: Session,
    marketplace_id: int,
    seen_ids: set[str],
    today: date,
    run_id: int | None = None,
    max_share: float = 0.5,
) -> int:
    """Mark active listings that were not seen in a COMPLETE run as removed.

    Safety guards (a broken page layout must not look like mass removals):
    - nothing is removed when no listings were seen at all;
    - with at least REMOVAL_GUARD_MIN_ACTIVE active listings, nothing is removed
      if that would remove more than max_share of them.
    """
    if not seen_ids:
        log.warning("marketplace %s: no listings seen, skipping removals", marketplace_id)
        return 0
    active = session.scalars(
        select(Listing).where(Listing.marketplace_id == marketplace_id, Listing.removed_on.is_(None))
    ).all()
    missing = [item for item in active if item.source_listing_id not in seen_ids]
    if len(active) >= REMOVAL_GUARD_MIN_ACTIVE and len(missing) / len(active) > max_share:
        log.warning("marketplace %s: %d of %d listings missing, skipping removals (looks like a break)",
                    marketplace_id, len(missing), len(active))
        return 0
    for item in missing:
        item.removed_on = today
        session.add(ListingEvent(listing_id=item.id, run_id=run_id, event_date=today, event_type="removed"))
    return len(missing)
