"""robots.txt checking and the rule that turns a review into a collection status.

Policy (from the brief): a source is only fetched automatically when robots.txt
allows it AND the terms of service do not prohibit it. Anything unresolved stays
'pending' and is never fetched.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from urllib.robotparser import RobotFileParser

import httpx

# Set SCANNER_USER_AGENT in .env with your real contact address.
DEFAULT_USER_AGENT = os.environ.get(
    "SCANNER_USER_AGENT",
    "MarketplaceScannerBot/0.1 (+https://github.com/aggreypaintsil168/marketplace-scanner; contact: SET-ME)",
)


@dataclass
class RobotsResult:
    robots_url: str
    http_status: int | None
    allows: bool | None            # None = undetermined, needs manual review
    crawl_delay: int | None = None
    per_path: dict[str, bool] = field(default_factory=dict)
    note: str = ""


def parse_robots(
    text: str, robots_url: str, user_agent: str, paths: tuple[str, ...] = ("/",)
) -> RobotsResult:
    """Pure function: evaluate robots.txt text for our user agent."""
    rp = RobotFileParser()
    rp.parse(text.splitlines())
    per_path = {p: rp.can_fetch(user_agent, p) for p in paths}
    delay = rp.crawl_delay(user_agent)
    return RobotsResult(
        robots_url=robots_url,
        http_status=200,
        allows=all(per_path.values()),
        crawl_delay=int(delay) if delay else None,
        per_path=per_path,
    )


def check_robots(
    base_url: str,
    user_agent: str = DEFAULT_USER_AGENT,
    paths: tuple[str, ...] = ("/",),
    timeout: float = 10.0,
) -> RobotsResult:
    """Fetch robots.txt once (a single polite request) and evaluate it."""
    robots_url = base_url.rstrip("/") + "/robots.txt"
    try:
        resp = httpx.get(
            robots_url, headers={"User-Agent": user_agent}, timeout=timeout, follow_redirects=True
        )
    except httpx.HTTPError as exc:
        return RobotsResult(robots_url, None, None, note=f"fetch failed: {exc!r}")

    if resp.status_code == 200:
        return parse_robots(resp.text, robots_url, user_agent, paths)
    if resp.status_code == 404:
        # No robots.txt: nothing is disallowed (RFC 9309), but ToS still decides.
        return RobotsResult(robots_url, 404, True, per_path={p: True for p in paths},
                            note="no robots.txt")
    # 401/403/5xx etc.: be conservative, require a human decision.
    return RobotsResult(robots_url, resp.status_code, None,
                        note=f"unexpected status {resp.status_code}; review manually")


def decide_collection_status(robots_allows: bool | None, tos_decision: str) -> str:
    """Map a review to the register's collection_status."""
    if tos_decision == "prohibited":
        return "blocked_by_tos"
    if robots_allows is False:
        return "blocked_by_robots"
    if robots_allows is True and tos_decision in ("permitted", "no_prohibition"):
        return "enabled"
    return "pending"  # unreviewed, unclear or undetermined: do not fetch