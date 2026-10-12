"""Daily health check: is a source still delivering?

evaluate_run() is a pure function (easy to test). run_health_check() loads the
recent history from the database, evaluates the run and stores the flags.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from statistics import mean

from sqlalchemy import select
from sqlalchemy.orm import Session

from marketplace_scanner.storage.models import FetchRun, HealthCheck

HISTORY_RUNS = 7
VOLUME_DROP_SHARE = 0.5   # flag when listings seen < 50% of the recent average
FILL_DROP = 0.2           # flag when a required field's fill rate drops by 20 points
ERROR_SHARE = 0.1         # flag when more than 10% of requests failed
SLOW_FACTOR = 3.0         # flag when responses are 3x slower than usual ...
SLOW_MIN_MS = 2000        # ... and slower than 2 seconds


@dataclass
class RunStats:
    listings_seen: int
    complete: bool = True
    fill_rate: dict = field(default_factory=dict)
    pages_fetched: int = 0
    http_errors: int = 0
    avg_response_ms: int | None = None


def stats_from_run(run: FetchRun) -> RunStats:
    return RunStats(
        listings_seen=run.listings_seen or 0,
        complete=bool(run.complete),
        fill_rate=run.field_fill_rate or {},
        pages_fetched=run.pages_fetched or 0,
        http_errors=run.http_errors or 0,
        avg_response_ms=run.avg_response_ms,
    )


def evaluate_run(current: RunStats, history: list[RunStats]) -> list[tuple[str, str]]:
    """Return (flag, detail) pairs. history = earlier successful runs, newest first."""
    flags: list[tuple[str, str]] = []
    recent = history[:HISTORY_RUNS]

    if current.listings_seen == 0:
        if not current.complete:
            flags.append(("fetch_failed", "run stopped before any listing was read"))
        else:
            flags.append(("zero_listings", "run finished but saw no listings"))
    elif recent:
        average = mean(h.listings_seen for h in recent)
        if average > 0 and current.listings_seen < VOLUME_DROP_SHARE * average:
            flags.append(("volume_drop",
                          f"{current.listings_seen} listings seen vs recent average {average:.0f}"))

    if current.listings_seen > 0:
        for name, rate in sorted(current.fill_rate.items()):
            past = [h.fill_rate[name] for h in recent if name in h.fill_rate]
            if rate == 0:
                flags.append(("empty_field", f"'{name}' could not be read for any listing"))
            elif past and rate < mean(past) - FILL_DROP:
                flags.append(("empty_field",
                              f"'{name}' readable for {rate:.0%} of listings, usually {mean(past):.0%}"))

    if current.pages_fetched and current.http_errors / current.pages_fetched > ERROR_SHARE:
        flags.append(("fetch_errors", f"{current.http_errors} of {current.pages_fetched} requests failed"))

    past_ms = [h.avg_response_ms for h in recent if h.avg_response_ms]
    if current.avg_response_ms and past_ms:
        usual = mean(past_ms)
        if current.avg_response_ms > SLOW_MIN_MS and current.avg_response_ms > SLOW_FACTOR * usual:
            flags.append(("slow_response",
                          f"{current.avg_response_ms} ms per request vs usual {usual:.0f} ms"))
    return flags


def run_health_check(session: Session, run: FetchRun, today: date | None = None) -> list[tuple[str, str]]:
    """Evaluate a finished run against the marketplace's recent history and store the flags."""
    today = today or date.today()
    earlier = session.scalars(
        select(FetchRun)
        .where(FetchRun.marketplace_id == run.marketplace_id, FetchRun.id != run.id,
               FetchRun.status == "ok")
        .order_by(FetchRun.id.desc())
        .limit(HISTORY_RUNS)
    ).all()
    flags = evaluate_run(stats_from_run(run), [stats_from_run(r) for r in earlier])
    for flag, detail in flags:
        session.add(HealthCheck(marketplace_id=run.marketplace_id, run_id=run.id,
                                checked_on=today, flag=flag, detail=detail))
    return flags
