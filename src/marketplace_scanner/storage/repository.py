"""Database access helpers. Keeps database details out of the rest of the app."""
from __future__ import annotations

import gzip
import hashlib
from datetime import date, datetime
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from marketplace_scanner.registry.models import ComplianceReview, Marketplace
from marketplace_scanner.registry.verification import RobotsResult, decide_collection_status
from marketplace_scanner.storage.models import ListingObservation, RawPage

# Columns a seed row is allowed to set (everything except generated ones).
SEEDABLE = {c.name for c in Marketplace.__table__.columns} - {"id", "created_at"}
# Columns a person may edit by hand. collection_status is deliberately excluded:
# it can only change through a recorded compliance review (record_review).
EDITABLE = SEEDABLE - {"slug", "collection_status"}


def get_engine(url: str | None = None) -> Engine:
    """Create a database engine. Defaults to DATABASE_URL from config/settings.py."""
    if url is None:
        from config.settings import DATABASE_URL  # imported here so tests don't need it

        url = DATABASE_URL
    return create_engine(url)


def insert_missing_marketplaces(session: Session, rows: list[dict]) -> dict[str, int]:
    """Insert seed rows whose slug is not in the register yet.

    Existing rows are never touched, so re-running the seed cannot overwrite
    anything that has since been verified or edited.
    """
    existing = set(session.scalars(select(Marketplace.slug)))
    inserted = skipped = 0
    for row in rows:
        unknown = set(row) - SEEDABLE
        if unknown:
            raise ValueError(f"{row.get('slug')}: unknown field(s) {sorted(unknown)}")
        if row["slug"] in existing:
            skipped += 1
            continue
        session.add(Marketplace(**row))
        existing.add(row["slug"])
        inserted += 1
    session.commit()
    return {"inserted": inserted, "skipped": skipped}


def _get_marketplace(session: Session, slug: str) -> Marketplace:
    marketplace = session.scalar(select(Marketplace).where(Marketplace.slug == slug))
    if marketplace is None:
        raise LookupError(f"no marketplace with slug '{slug}'")
    return marketplace


def update_marketplace(session: Session, slug: str, **fields) -> Marketplace:
    """Edit register fields by hand (URL, owner, estimates...). Not collection_status."""
    unknown = set(fields) - EDITABLE
    if unknown:
        raise ValueError(
            f"cannot set {sorted(unknown)}; collection_status changes only via a compliance review"
        )
    marketplace = _get_marketplace(session, slug)
    for name, value in fields.items():
        setattr(marketplace, name, value)
    session.commit()
    return marketplace


def record_review(
    session: Session,
    slug: str,
    *,
    reviewer: str,
    robots: RobotsResult,
    tos_decision: str,
    tos_url: str | None = None,
    api_or_feed_url: str | None = None,
    notes: str | None = None,
    reviewed_on: date | None = None,
) -> str:
    """Store a dated robots.txt + terms review and update the collection status.

    Returns the new collection_status. The status is derived by
    decide_collection_status, so a site is only 'enabled' when robots.txt allows
    it AND the terms permit (or do not prohibit) automated collection.
    """
    marketplace = _get_marketplace(session, slug)
    if not marketplace.url:
        raise ValueError(f"'{slug}' has no URL yet; set it first with the update command")
    session.add(
        ComplianceReview(
            marketplace_id=marketplace.id,
            reviewed_on=reviewed_on or date.today(),
            reviewer=reviewer,
            robots_url=robots.robots_url,
            robots_allows=robots.allows,
            crawl_delay=robots.crawl_delay,
            tos_url=tos_url,
            tos_decision=tos_decision,
            api_or_feed_url=api_or_feed_url,
            notes=notes,
        )
    )
    marketplace.collection_status = decide_collection_status(robots.allows, tos_decision)
    session.commit()
    return marketplace.collection_status


def save_raw_page(
    session: Session,
    *,
    marketplace_id: int,
    slug: str,
    url: str,
    status: int,
    text: str,
    fetched_at: datetime,
    raw_dir: Path | str,
) -> RawPage:
    """Save a gzip copy of a fetched page under raw_dir and record where it is.

    Identical content on the same day is stored once. These files are for
    internal analysis and evidence only; they must never be published.
    """
    body = text.encode("utf-8")
    digest = hashlib.sha256(body).hexdigest()
    relative = f"{slug}/{fetched_at:%Y-%m-%d}/{digest[:16]}.html.gz"
    path = Path(raw_dir) / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_bytes(gzip.compress(body))
    page = RawPage(marketplace_id=marketplace_id, url=url, fetched_at=fetched_at, http_status=status,
                   storage_path=relative, sha256=digest, size_bytes=len(body))
    session.add(page)
    session.flush()
    return page


def purge_raw_pages(session: Session, raw_dir: Path | str, older_than: datetime) -> int:
    """Delete raw page records and files fetched before older_than (retention period).

    Observation text stays; only the saved copy of the source page goes.
    """
    doomed = session.scalars(select(RawPage).where(RawPage.fetched_at < older_than)).all()
    if not doomed:
        return 0
    ids = [page.id for page in doomed]
    paths = {page.storage_path for page in doomed if page.storage_path}
    for observation in session.scalars(
        select(ListingObservation).where(ListingObservation.raw_page_id.in_(ids))
    ):
        observation.raw_page_id = None
    for page in doomed:
        session.delete(page)
    session.flush()
    for relative in paths:
        still_used = session.scalar(select(RawPage.id).where(RawPage.storage_path == relative).limit(1))
        file = Path(raw_dir) / relative
        if still_used is None and file.exists():
            file.unlink()
    session.commit()
    return len(doomed)
