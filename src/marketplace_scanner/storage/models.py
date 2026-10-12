"""Layer 2: listings, their history, collection runs and health flags.

Raw first: observations keep the text exactly as the source showed it.
Normalised values (EUR amounts, industry codes, ...) will be computed later
from these rows and can be recalculated at any time.
"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from marketplace_scanner.registry.models import Base

RUN_STATUSES = ("running", "ok", "partial", "failed")
EVENT_TYPES = ("new", "changed", "removed", "reappeared")
HEALTH_FLAGS = (
    "fetch_failed",   # run produced nothing and did not finish
    "zero_listings",  # run finished but saw no listings
    "volume_drop",    # far fewer listings than the recent average
    "empty_field",    # a required field stopped being readable
    "fetch_errors",   # many failed requests
    "slow_response",  # source much slower than usual
)

# Text fields stored exactly as shown at the source.
TEXT_FIELDS = (
    "title",
    "description",
    "price_raw",
    "revenue_raw",
    "profit_raw",
    "employees_raw",
    "category_raw",
    "location_raw",
    "deal_type_raw",
    "seller_raw",      # broker name / firm only; never private phone numbers or names
    "published_raw",
    "status_raw",
)
OBSERVED_FIELDS = TEXT_FIELDS + ("image_count", "has_prospectus")


class RawPage(Base):
    """Pointer to a saved copy of a fetched page (the file lives under data/raw/)."""

    __tablename__ = "raw_pages"

    id: Mapped[int] = mapped_column(primary_key=True)
    marketplace_id: Mapped[int] = mapped_column(ForeignKey("marketplaces.id"), index=True)
    url: Mapped[str] = mapped_column(String(1000))
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    http_status: Mapped[int | None] = mapped_column(Integer)
    storage_path: Mapped[str | None] = mapped_column(String(500))  # relative to the raw directory
    sha256: Mapped[str | None] = mapped_column(String(64))
    size_bytes: Mapped[int | None] = mapped_column(Integer)


class FetchRun(Base):
    """One collection run for one marketplace."""

    __tablename__ = "fetch_runs"
    __table_args__ = (CheckConstraint(f"status IN {RUN_STATUSES}", name="ck_run_status"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    marketplace_id: Mapped[int] = mapped_column(ForeignKey("marketplaces.id"), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    parser_version: Mapped[str | None] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(10), default="running")
    complete: Mapped[bool] = mapped_column(Boolean, default=False)  # full sweep finished cleanly
    pages_fetched: Mapped[int] = mapped_column(Integer, default=0)
    http_errors: Mapped[int] = mapped_column(Integer, default=0)
    avg_response_ms: Mapped[int | None] = mapped_column(Integer)
    listings_seen: Mapped[int] = mapped_column(Integer, default=0)
    listings_new: Mapped[int] = mapped_column(Integer, default=0)
    listings_changed: Mapped[int] = mapped_column(Integer, default=0)
    listings_removed: Mapped[int] = mapped_column(Integer, default=0)
    field_fill_rate: Mapped[dict | None] = mapped_column(JSON)  # {"title": 1.0, "price_raw": 0.6}
    error: Mapped[str | None] = mapped_column(Text)


class Listing(Base):
    """One advertised business. First/last seen live here; changes are in events."""

    __tablename__ = "listings"
    __table_args__ = (UniqueConstraint("marketplace_id", "source_listing_id", name="uq_listing_source"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    marketplace_id: Mapped[int] = mapped_column(ForeignKey("marketplaces.id"), index=True)
    source_listing_id: Mapped[str] = mapped_column(String(200))
    url: Mapped[str] = mapped_column(String(1000))
    first_seen: Mapped[date] = mapped_column(Date)
    last_seen: Mapped[date] = mapped_column(Date)
    removed_on: Mapped[date | None] = mapped_column(Date)


class ListingObservation(Base):
    """Append-only snapshot of a listing's fields, written when it is new or has changed."""

    __tablename__ = "listing_observations"

    id: Mapped[int] = mapped_column(primary_key=True)
    listing_id: Mapped[int] = mapped_column(ForeignKey("listings.id"), index=True)
    run_id: Mapped[int | None] = mapped_column(ForeignKey("fetch_runs.id"))
    raw_page_id: Mapped[int | None] = mapped_column(ForeignKey("raw_pages.id"))
    observed_on: Mapped[date] = mapped_column(Date)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    parser_version: Mapped[str | None] = mapped_column(String(40))
    content_hash: Mapped[str] = mapped_column(String(64))

    title: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    price_raw: Mapped[str | None] = mapped_column(String(200))
    revenue_raw: Mapped[str | None] = mapped_column(String(200))
    profit_raw: Mapped[str | None] = mapped_column(String(200))
    employees_raw: Mapped[str | None] = mapped_column(String(200))
    category_raw: Mapped[str | None] = mapped_column(String(300))
    location_raw: Mapped[str | None] = mapped_column(String(300))
    deal_type_raw: Mapped[str | None] = mapped_column(String(200))
    seller_raw: Mapped[str | None] = mapped_column(String(300))
    published_raw: Mapped[str | None] = mapped_column(String(100))
    status_raw: Mapped[str | None] = mapped_column(String(100))
    image_count: Mapped[int | None] = mapped_column(Integer)
    has_prospectus: Mapped[bool | None] = mapped_column(Boolean)


class ListingEvent(Base):
    """new / changed / removed / reappeared, each with a date."""

    __tablename__ = "listing_events"
    __table_args__ = (CheckConstraint(f"event_type IN {EVENT_TYPES}", name="ck_event_type"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    listing_id: Mapped[int] = mapped_column(ForeignKey("listings.id"), index=True)
    run_id: Mapped[int | None] = mapped_column(ForeignKey("fetch_runs.id"))
    event_date: Mapped[date] = mapped_column(Date, index=True)
    event_type: Mapped[str] = mapped_column(String(12))
    field: Mapped[str | None] = mapped_column(String(40))
    old_value: Mapped[str | None] = mapped_column(Text)
    new_value: Mapped[str | None] = mapped_column(Text)


class HealthCheck(Base):
    """A flag raised for a marketplace on a given day."""

    __tablename__ = "health_checks"
    __table_args__ = (CheckConstraint(f"flag IN {HEALTH_FLAGS}", name="ck_health_flag"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    marketplace_id: Mapped[int] = mapped_column(ForeignKey("marketplaces.id"), index=True)
    run_id: Mapped[int | None] = mapped_column(ForeignKey("fetch_runs.id"))
    checked_on: Mapped[date] = mapped_column(Date, index=True)
    flag: Mapped[str] = mapped_column(String(20))
    detail: Mapped[str | None] = mapped_column(Text)
