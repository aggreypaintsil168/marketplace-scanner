"""Layer 1: the register of marketplaces.

One row per site that lists businesses for sale, plus a dated review log
recording robots.txt / terms-of-service checks (the legal audit trail) and
dated pricing-page snapshots.
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
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

MARKETPLACE_TYPES = ("general", "niche", "broker_portal", "broker_chain")
STATUSES = ("candidate", "active", "dead", "acquired")
COLLECTION_STATUSES = (
    "pending",            # not yet reviewed: never fetched
    "enabled",            # reviewed, permitted: fetched daily
    "blocked_by_tos",     # terms prohibit automated collection
    "blocked_by_robots",  # robots.txt disallows
    "manual_only",        # register entry only, no automated fetch
)
TOS_DECISIONS = ("unreviewed", "permitted", "no_prohibition", "unclear", "prohibited")


class Base(DeclarativeBase):
    pass


class Marketplace(Base):
    __tablename__ = "marketplaces"
    __table_args__ = (
        CheckConstraint(f"type IN {MARKETPLACE_TYPES}", name="ck_marketplace_type"),
        CheckConstraint(f"status IN {STATUSES}", name="ck_marketplace_status"),
        CheckConstraint(
            f"collection_status IN {COLLECTION_STATUSES}", name="ck_marketplace_collection"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(80), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    url: Mapped[str | None] = mapped_column(String(500))  # NULL allowed for unverified candidates
    country: Mapped[str | None] = mapped_column(String(2))  # ISO 3166-1 alpha-2; NULL = global
    languages: Mapped[list[str] | None] = mapped_column(JSON)
    type: Mapped[str] = mapped_column(String(20))
    niche: Mapped[str | None] = mapped_column(String(80))

    owner_name: Mapped[str | None] = mapped_column(String(200))
    owner_reg_no: Mapped[str | None] = mapped_column(String(50))
    founded_year: Mapped[int | None] = mapped_column(Integer)
    business_model: Mapped[str | None] = mapped_column(Text)
    est_listings: Mapped[int | None] = mapped_column(Integer)  # manual estimate at registration

    status: Mapped[str] = mapped_column(String(20), default="candidate")
    status_note: Mapped[str | None] = mapped_column(Text)

    collection_status: Mapped[str] = mapped_column(String(20), default="pending")
    access_method: Mapped[str | None] = mapped_column(String(20))  # api | rss | sitemap | html

    verified_by: Mapped[str | None] = mapped_column(String(100))
    verified_at: Mapped[date | None] = mapped_column(Date)
    on_watchlist: Mapped[bool] = mapped_column(Boolean, default=False)
    watch_notes: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    reviews: Mapped[list["ComplianceReview"]] = relationship(
        back_populates="marketplace", order_by="ComplianceReview.reviewed_on"
    )
    pricing_snapshots: Mapped[list["PricingSnapshot"]] = relationship(back_populates="marketplace")


class ComplianceReview(Base):
    """Dated record of a robots.txt + terms-of-service review. Append-only."""

    __tablename__ = "compliance_reviews"
    __table_args__ = (
        CheckConstraint(f"tos_decision IN {TOS_DECISIONS}", name="ck_review_tos_decision"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    marketplace_id: Mapped[int] = mapped_column(ForeignKey("marketplaces.id"))
    reviewed_on: Mapped[date] = mapped_column(Date)
    reviewer: Mapped[str] = mapped_column(String(100))
    robots_url: Mapped[str | None] = mapped_column(String(500))
    robots_allows: Mapped[bool | None] = mapped_column(Boolean)  # None = could not be determined
    crawl_delay: Mapped[int | None] = mapped_column(Integer)
    tos_url: Mapped[str | None] = mapped_column(String(500))
    tos_decision: Mapped[str] = mapped_column(String(20), default="unreviewed")
    api_or_feed_url: Mapped[str | None] = mapped_column(String(500))
    notes: Mapped[str | None] = mapped_column(Text)

    marketplace: Mapped[Marketplace] = relationship(back_populates="reviews")


class PricingSnapshot(Base):
    """Dated capture of a site's price list (screenshot path kept outside git)."""

    __tablename__ = "pricing_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    marketplace_id: Mapped[int] = mapped_column(ForeignKey("marketplaces.id"))
    captured_on: Mapped[date] = mapped_column(Date)
    source_url: Mapped[str | None] = mapped_column(String(500))
    screenshot_path: Mapped[str | None] = mapped_column(String(500))
    notes: Mapped[str | None] = mapped_column(Text)

    marketplace: Mapped[Marketplace] = relationship(back_populates="pricing_snapshots")