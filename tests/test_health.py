from datetime import date, datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from marketplace_scanner.health.checks import RunStats, evaluate_run, run_health_check
from marketplace_scanner.registry.models import Base, Marketplace
from marketplace_scanner.storage.models import FetchRun, HealthCheck


def flags(current, history=()):
    return {flag for flag, _ in evaluate_run(current, list(history))}


GOOD = RunStats(listings_seen=100, fill_rate={"title": 1.0, "price_raw": 0.9}, pages_fetched=50,
                avg_response_ms=400)


def test_healthy_run_has_no_flags():
    assert flags(GOOD, [GOOD, GOOD, GOOD]) == set()


def test_zero_listings_on_a_finished_run():
    assert flags(RunStats(listings_seen=0, complete=True)) == {"zero_listings"}


def test_run_that_stopped_before_reading_anything():
    assert flags(RunStats(listings_seen=0, complete=False)) == {"fetch_failed"}


def test_volume_drop_against_recent_average():
    assert "volume_drop" in flags(RunStats(listings_seen=30, fill_rate=GOOD.fill_rate), [GOOD] * 3)
    assert "volume_drop" not in flags(RunStats(listings_seen=80, fill_rate=GOOD.fill_rate), [GOOD] * 3)


def test_field_that_stops_being_readable():
    broken = RunStats(listings_seen=100, fill_rate={"title": 1.0, "price_raw": 0.0})
    assert flags(broken) == {"empty_field"}                      # needs no history
    dropped = RunStats(listings_seen=100, fill_rate={"title": 1.0, "price_raw": 0.4})
    assert "empty_field" in flags(dropped, [GOOD] * 3)           # 90% -> 40%


def test_many_failed_requests_and_slow_responses():
    shaky = RunStats(listings_seen=100, fill_rate=GOOD.fill_rate, pages_fetched=50, http_errors=10,
                     avg_response_ms=3000)
    assert flags(shaky, [GOOD] * 3) == {"fetch_errors", "slow_response"}


def test_run_health_check_stores_flags_in_the_database():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    now = datetime(2026, 10, 12, tzinfo=timezone.utc)
    with Session(engine) as s:
        m = Marketplace(slug="x", name="X", type="general", url="https://x.test", collection_status="enabled")
        s.add(m)
        s.flush()
        for _ in range(3):
            s.add(FetchRun(marketplace_id=m.id, started_at=now, status="ok", complete=True,
                           listings_seen=100, field_fill_rate={"title": 1.0}, pages_fetched=10))
        today_run = FetchRun(marketplace_id=m.id, started_at=now, status="ok", complete=True,
                             listings_seen=10, field_fill_rate={"title": 1.0}, pages_fetched=10)
        s.add(today_run)
        s.flush()
        result = run_health_check(s, today_run, today=date(2026, 10, 12))
        s.commit()
        assert [flag for flag, _ in result] == ["volume_drop"]
        assert [(h.flag, h.checked_on) for h in s.query(HealthCheck)] == [("volume_drop", date(2026, 10, 12))]
