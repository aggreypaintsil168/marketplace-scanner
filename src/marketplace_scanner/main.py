"""Command-line entry point. Run from the repository root:

    python -m marketplace_scanner.main seed                 # load the Nordic seed
    python -m marketplace_scanner.main list                 # show the register
    python -m marketplace_scanner.main show <slug>          # one marketplace + its reviews
    python -m marketplace_scanner.main update <slug> --url https://...   # edit register fields
    python -m marketplace_scanner.main review <slug> --reviewer "Your Name" --tos permitted
    python -m marketplace_scanner.main collect [--source <slug>]   # daily collection (enabled sources only)
    python -m marketplace_scanner.main health [--days 7]           # recent health flags
    python -m marketplace_scanner.main purge-raw --days 365        # delete old saved page copies
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import yaml
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from marketplace_scanner.registry.models import TOS_DECISIONS, ComplianceReview, Marketplace
from marketplace_scanner.collectors.base import load_adapters
from marketplace_scanner.registry.verification import check_robots
from marketplace_scanner.scheduler import run_all
from marketplace_scanner.storage.models import HealthCheck
from marketplace_scanner.storage.repository import (
    get_engine,
    insert_missing_marketplaces,
    purge_raw_pages,
    record_review,
    update_marketplace,
)

SEED_FILE = Path(__file__).parent / "registry" / "seed" / "nordics.yaml"

# Register fields that can be edited by hand, and how to read them from the command line.
UPDATE_FIELDS = {
    "url": str,
    "country": str,
    "type": str,
    "owner_name": str,
    "owner_reg_no": str,
    "founded_year": int,
    "est_listings": int,
    "business_model": str,
    "status": str,
    "status_note": str,
}


def cmd_seed(_args: argparse.Namespace) -> None:
    rows = yaml.safe_load(SEED_FILE.read_text(encoding="utf-8"))["marketplaces"]
    with Session(get_engine()) as session:
        result = insert_missing_marketplaces(session, rows)
    print(f"Seed: {result['inserted']} inserted, {result['skipped']} already present.")


def cmd_list(_args: argparse.Namespace) -> None:
    query = select(
        Marketplace.slug,
        Marketplace.country,
        Marketplace.status,
        Marketplace.collection_status,
        Marketplace.url,
    ).order_by(Marketplace.country, Marketplace.slug)
    with Session(get_engine()) as session:
        rows = session.execute(query).all()
    print(f"{'slug':<22}{'country':<9}{'status':<11}{'collection':<18}url known")
    for slug, country, status, collection, url in rows:
        print(f"{slug:<22}{country or '-':<9}{status:<11}{collection:<18}{'yes' if url else 'no'}")
    print(f"{len(rows)} marketplace(s) in the register.")


def cmd_show(args: argparse.Namespace) -> None:
    with Session(get_engine()) as session:
        m = session.scalar(select(Marketplace).where(Marketplace.slug == args.slug))
        if m is None:
            raise LookupError(f"no marketplace with slug '{args.slug}'")
        for column in Marketplace.__table__.columns:
            print(f"{column.name:<18}{getattr(m, column.name)}")
        reviews = session.scalars(
            select(ComplianceReview)
            .where(ComplianceReview.marketplace_id == m.id)
            .order_by(ComplianceReview.reviewed_on, ComplianceReview.id)
        ).all()
        print(f"\n{len(reviews)} compliance review(s):")
        for r in reviews:
            print(
                f"  {r.reviewed_on}  by {r.reviewer}: robots_allows={r.robots_allows}, "
                f"tos={r.tos_decision}, crawl_delay={r.crawl_delay}"
            )


def cmd_update(args: argparse.Namespace) -> None:
    fields = {name: getattr(args, name) for name in UPDATE_FIELDS if getattr(args, name) is not None}
    if not fields:
        raise ValueError("nothing to update; pass at least one --option")
    with Session(get_engine()) as session:
        update_marketplace(session, args.slug, **fields)
    print(f"Updated {args.slug}: {', '.join(sorted(fields))}")


def cmd_review(args: argparse.Namespace) -> None:
    user_agent = _require_real_user_agent()
    with Session(get_engine()) as session:
        m = session.scalar(select(Marketplace).where(Marketplace.slug == args.slug))
        if m is None:
            raise LookupError(f"no marketplace with slug '{args.slug}'")
        if not m.url:
            raise ValueError(f"'{args.slug}' has no URL yet; run the update command with --url first")
        paths = tuple(args.path) if args.path else ("/",)
        print(f"Fetching {m.url.rstrip('/')}/robots.txt once ...")
        robots = check_robots(m.url, user_agent=user_agent, paths=paths)
        print(f"  http status: {robots.http_status}   allows: {robots.allows}   "
              f"crawl-delay: {robots.crawl_delay}   {robots.note}")
        for path, ok in robots.per_path.items():
            print(f"  {path:<30} {'allowed' if ok else 'DISALLOWED'}")
        status = record_review(
            session,
            args.slug,
            reviewer=args.reviewer,
            robots=robots,
            tos_decision=args.tos,
            tos_url=args.tos_url,
            api_or_feed_url=args.feed_url,
            notes=args.notes,
        )
    print(f"Review recorded. {args.slug} collection_status is now: {status}")


def _require_real_user_agent() -> str:
    from config.settings import SCANNER_USER_AGENT

    if "SET-ME" in SCANNER_USER_AGENT:
        raise ValueError(
            "SCANNER_USER_AGENT in .env still has the SET-ME placeholder. "
            "Put your real contact email in it first (the brief requires an identifiable bot)."
        )
    return SCANNER_USER_AGENT


def cmd_collect(args: argparse.Namespace) -> None:
    from config.settings import RAW_DIR

    user_agent = _require_real_user_agent()
    adapters = load_adapters()
    with Session(get_engine()) as session:
        if args.source:
            m = session.scalar(select(Marketplace).where(Marketplace.slug == args.source))
            if m is None:
                raise LookupError(f"no marketplace with slug '{args.source}'")
            if m.collection_status != "enabled":
                raise ValueError(
                    f"{args.source} is '{m.collection_status}', not 'enabled'. "
                    "Run a compliance review first (the review command)."
                )
            if args.source not in adapters:
                raise LookupError(f"no adapter for '{args.source}' in collectors/sources/")
        runs = run_all(session, adapters, user_agent, RAW_DIR, only_slug=args.source)
        names = {m.id: m.slug for m in session.scalars(select(Marketplace))}
        if not runs:
            print("Nothing to collect: no enabled marketplace has an adapter yet.")
        for run in runs:
            print(f"{names[run.marketplace_id]}: {run.status}  seen={run.listings_seen} "
                  f"new={run.listings_new} changed={run.listings_changed} removed={run.listings_removed} "
                  f"requests={run.pages_fetched} errors={run.http_errors}")
            if run.error:
                print(f"  stopped early: {run.error}")


def cmd_health(args: argparse.Namespace) -> None:
    since = date.today() - timedelta(days=args.days)
    query = (
        select(HealthCheck.checked_on, Marketplace.slug, HealthCheck.flag, HealthCheck.detail)
        .join(Marketplace, Marketplace.id == HealthCheck.marketplace_id)
        .where(HealthCheck.checked_on >= since)
        .order_by(HealthCheck.checked_on.desc(), Marketplace.slug)
    )
    with Session(get_engine()) as session:
        rows = session.execute(query).all()
    if not rows:
        print(f"No health flags in the last {args.days} day(s).")
    for checked_on, slug, flag, detail in rows:
        print(f"{checked_on}  {slug:<22}{flag:<15}{detail or ''}")


def cmd_purge_raw(args: argparse.Namespace) -> None:
    from config.settings import RAW_DIR

    cutoff = datetime.now(timezone.utc) - timedelta(days=args.days)
    with Session(get_engine()) as session:
        removed = purge_raw_pages(session, RAW_DIR, cutoff)
    print(f"Purged {removed} saved page copy(ies) older than {args.days} days.")


def main(argv: list[str] | None = None) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # safe output on Windows consoles
    parser = argparse.ArgumentParser(prog="marketplace_scanner")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("seed", help="load the Nordic seed file").set_defaults(func=cmd_seed)
    sub.add_parser("list", help="list the register").set_defaults(func=cmd_list)

    p = sub.add_parser("show", help="show one marketplace and its reviews")
    p.add_argument("slug")
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("update", help="edit register fields by hand")
    p.add_argument("slug")
    for name, kind in UPDATE_FIELDS.items():
        p.add_argument(f"--{name.replace('_', '-')}", dest=name, type=kind)
    p.set_defaults(func=cmd_update)

    p = sub.add_parser("review", help="check robots.txt and record a compliance review")
    p.add_argument("slug")
    p.add_argument("--reviewer", required=True, help="your name")
    p.add_argument("--tos", required=True, choices=TOS_DECISIONS,
                   help="your reading of the site's terms of service")
    p.add_argument("--tos-url")
    p.add_argument("--feed-url", help="official API / RSS / sitemap, if one exists")
    p.add_argument("--notes")
    p.add_argument("--path", action="append",
                   help="path to test against robots.txt (repeatable); default is /")
    p.set_defaults(func=cmd_review)

    p = sub.add_parser("collect", help="collect listings from enabled marketplaces")
    p.add_argument("--source", help="only this marketplace slug")
    p.set_defaults(func=cmd_collect)

    p = sub.add_parser("health", help="show recent health flags")
    p.add_argument("--days", type=int, default=7)
    p.set_defaults(func=cmd_health)

    p = sub.add_parser("purge-raw", help="delete saved page copies older than the retention period")
    p.add_argument("--days", type=int, required=True, help="keep copies newer than this many days")
    p.set_defaults(func=cmd_purge_raw)

    args = parser.parse_args(argv)
    from config.settings import configure_logging

    configure_logging()
    try:
        args.func(args)
    except (ValueError, LookupError, IntegrityError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
