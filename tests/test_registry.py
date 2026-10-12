from pathlib import Path

import pytest
import yaml
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from marketplace_scanner.registry.models import MARKETPLACE_TYPES, Base, Marketplace
from marketplace_scanner.registry.verification import decide_collection_status, parse_robots

UA = "MarketplaceScannerBot/0.1"
SEED = Path(__file__).parents[1] / "src/marketplace_scanner/registry/seed/nordics.yaml"


def make_session():
    engine = create_engine("sqlite://")  # throwaway in-memory database
    Base.metadata.create_all(engine)
    return Session(engine)


def test_marketplace_roundtrip_defaults():
    with make_session() as s:
        s.add(Marketplace(slug="x-se", name="X", type="general", country="SE"))
        s.commit()
        m = s.query(Marketplace).one()
        assert m.status == "candidate"
        assert m.collection_status == "pending"  # new sites are never fetched by default


def test_invalid_type_rejected():
    with make_session() as s:
        s.add(Marketplace(slug="bad", name="Bad", type="classifieds"))
        with pytest.raises(IntegrityError):
            s.commit()


def test_robots_disallow_all():
    r = parse_robots("User-agent: *\nDisallow: /", "https://e.com/robots.txt", UA)
    assert r.allows is False


def test_robots_partial_and_crawl_delay():
    txt = "User-agent: *\nDisallow: /admin\nCrawl-delay: 10"
    r = parse_robots(txt, "https://e.com/robots.txt", UA, paths=("/", "/listings/1", "/admin"))
    assert r.per_path == {"/": True, "/listings/1": True, "/admin": False}
    assert r.crawl_delay == 10
    assert r.allows is False  # one required path is blocked


@pytest.mark.parametrize(
    "robots,tos,expected",
    [
        (True, "permitted", "enabled"),
        (True, "no_prohibition", "enabled"),
        (True, "prohibited", "blocked_by_tos"),
        (False, "permitted", "blocked_by_robots"),
        (True, "unclear", "pending"),
        (True, "unreviewed", "pending"),
        (None, "permitted", "pending"),
    ],
)
def test_decide_collection_status(robots, tos, expected):
    assert decide_collection_status(robots, tos) == expected


def test_seed_entries_are_valid():
    rows = yaml.safe_load(SEED.read_text(encoding="utf-8"))["marketplaces"]
    slugs = [r["slug"] for r in rows]
    assert len(slugs) == len(set(slugs)), "duplicate slug in seed"
    for r in rows:
        assert r["type"] in MARKETPLACE_TYPES
        # unverified candidates must never be pre-enabled for collection
        assert r.get("collection_status", "pending") == "pending"

# --- seed loader ------------------------------------------------------------
from marketplace_scanner.storage.repository import insert_missing_marketplaces


def test_seed_insert_is_idempotent():
    rows = yaml.safe_load(SEED.read_text(encoding="utf-8"))["marketplaces"]
    with make_session() as s:
        assert insert_missing_marketplaces(s, rows) == {"inserted": len(rows), "skipped": 0}
        assert insert_missing_marketplaces(s, rows) == {"inserted": 0, "skipped": len(rows)}
        assert s.query(Marketplace).count() == len(rows)


def test_seed_does_not_overwrite_existing_rows():
    with make_session() as s:
        s.add(Marketplace(slug="bolagsplatsen-se", name="Bolagsplatsen.se", type="general",
                          owner_name="Verified Owner AB"))
        s.commit()
        insert_missing_marketplaces(s, [{"slug": "bolagsplatsen-se", "name": "Changed", "type": "general"}])
        assert s.query(Marketplace).one().owner_name == "Verified Owner AB"


def test_seed_rejects_unknown_fields():
    with make_session() as s:
        with pytest.raises(ValueError):
            insert_missing_marketplaces(s, [{"slug": "x", "name": "X", "type": "general", "bogus": 1}])


def test_seed_codes_are_strings():
    # YAML reads an unquoted `no` as boolean false, so codes like NO must be quoted.
    rows = yaml.safe_load(SEED.read_text(encoding="utf-8"))["marketplaces"]
    for r in rows:
        assert isinstance(r["country"], str) and len(r["country"]) == 2, r["slug"]
        assert all(isinstance(lang, str) for lang in r["languages"]), r["slug"]


# --- compliance reviews and manual edits ------------------------------------
from marketplace_scanner.registry.models import ComplianceReview
from marketplace_scanner.registry.verification import RobotsResult
from marketplace_scanner.storage.repository import record_review, update_marketplace


def _robots(allows, delay=None):
    return RobotsResult("https://e.com/robots.txt", 200, allows, crawl_delay=delay)


def _session_with_site(url="https://e.com"):
    s = make_session()
    s.add(Marketplace(slug="e-se", name="E", type="general", url=url))
    s.commit()
    return s


def test_review_enables_only_when_robots_and_tos_allow():
    with _session_with_site() as s:
        status = record_review(s, "e-se", reviewer="Tester", robots=_robots(True, 5), tos_decision="permitted")
        assert status == "enabled"
        review = s.query(ComplianceReview).one()
        assert review.robots_allows is True and review.crawl_delay == 5


def test_review_blocks_when_terms_prohibit_even_if_robots_allow():
    with _session_with_site() as s:
        assert record_review(s, "e-se", reviewer="T", robots=_robots(True), tos_decision="prohibited") == "blocked_by_tos"


def test_review_blocks_when_robots_disallow():
    with _session_with_site() as s:
        assert record_review(s, "e-se", reviewer="T", robots=_robots(False), tos_decision="permitted") == "blocked_by_robots"


def test_reviews_are_kept_as_history():
    with _session_with_site() as s:
        record_review(s, "e-se", reviewer="T", robots=_robots(True), tos_decision="unclear")
        record_review(s, "e-se", reviewer="T", robots=_robots(True), tos_decision="permitted")
        assert s.query(ComplianceReview).count() == 2
        assert s.query(Marketplace).one().collection_status == "enabled"


def test_review_needs_a_url():
    with _session_with_site(url=None) as s:
        with pytest.raises(ValueError):
            record_review(s, "e-se", reviewer="T", robots=_robots(True), tos_decision="permitted")


def test_update_edits_fields_but_never_collection_status():
    with _session_with_site() as s:
        update_marketplace(s, "e-se", owner_name="E Oy", est_listings=120)
        m = s.query(Marketplace).one()
        assert (m.owner_name, m.est_listings) == ("E Oy", 120)
        with pytest.raises(ValueError):
            update_marketplace(s, "e-se", collection_status="enabled")
