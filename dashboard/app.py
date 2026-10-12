"""Internal dashboard. Run from the repository root:

    streamlit run dashboard/app.py

Tabs: Register (all marketplaces and their active listings), Listing search
(free text + filters, CSV export) and Data health (which sources delivered today).
Raw listing content is for internal use only and must never be published.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))  # lets this script import config/ when started by Streamlit

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402
from sqlalchemy import text  # noqa: E402

from marketplace_scanner.storage.repository import get_engine  # noqa: E402

st.set_page_config(page_title="Marketplace Scanner", layout="wide")


@st.cache_resource
def engine():
    return get_engine()


def query(sql: str, params: dict | None = None) -> pd.DataFrame:
    with engine().connect() as connection:
        return pd.read_sql(text(sql), connection, params=params or {})


st.title("Marketplace Scanner")
tab_register, tab_search, tab_health = st.tabs(["Register", "Listing search", "Data health"])

# --- Register ---------------------------------------------------------------
with tab_register:
    register = query(
        """
        SELECT m.slug, m.name, m.country, m.type, m.status, m.collection_status,
               m.est_listings,
               COALESCE(SUM(CASE WHEN l.id IS NOT NULL AND l.removed_on IS NULL THEN 1 ELSE 0 END), 0)
                   AS active_listings
        FROM marketplaces m LEFT JOIN listings l ON l.marketplace_id = m.id
        GROUP BY m.id ORDER BY m.country, m.slug
        """
    )
    c1, c2, c3 = st.columns(3)
    c1.metric("Marketplaces in the register", len(register))
    c2.metric("Enabled for collection", int((register["collection_status"] == "enabled").sum()))
    c3.metric("Active listings", int(register["active_listings"].sum()))
    st.dataframe(register, hide_index=True)
    st.download_button("Download register (CSV)", register.to_csv(index=False), "register.csv", "text/csv")

# --- Listing search -----------------------------------------------------------
with tab_search:
    slugs = query("SELECT slug FROM marketplaces ORDER BY slug")["slug"].tolist()
    col_text, col_site, col_state = st.columns([3, 2, 1])
    words = col_text.text_input("Search title, description, category or location")
    sites = col_site.multiselect("Marketplace", slugs)
    state = col_state.radio("Status", ["active", "removed", "all"], horizontal=True)

    sql = """
        SELECT m.slug AS marketplace, o.title, o.price_raw, o.category_raw, o.location_raw,
               l.first_seen, l.last_seen, l.removed_on, l.url
        FROM listings l
        JOIN marketplaces m ON m.id = l.marketplace_id
        JOIN listing_observations o ON o.id = (
            SELECT MAX(id) FROM listing_observations WHERE listing_id = l.id)
        WHERE 1 = 1
    """
    params: dict = {}
    if words:
        sql += """ AND (LOWER(COALESCE(o.title, '') || ' ' || COALESCE(o.description, '') || ' '
                   || COALESCE(o.category_raw, '') || ' ' || COALESCE(o.location_raw, '')) LIKE :words)"""
        params["words"] = f"%{words.lower()}%"
    if sites:
        placeholders = ", ".join(f":site{i}" for i in range(len(sites)))
        sql += f" AND m.slug IN ({placeholders})"
        params.update({f"site{i}": slug for i, slug in enumerate(sites)})
    if state == "active":
        sql += " AND l.removed_on IS NULL"
    elif state == "removed":
        sql += " AND l.removed_on IS NOT NULL"
    sql += " ORDER BY l.first_seen DESC, l.id DESC LIMIT 1000"

    results = query(sql, params)
    st.caption(f"{len(results)} listing(s) shown (max 1000)")
    st.dataframe(results, hide_index=True)
    st.download_button("Download results (CSV)", results.to_csv(index=False), "listings.csv", "text/csv")

# --- Data health --------------------------------------------------------------
with tab_health:
    latest = query(
        """
        SELECT m.slug, r.started_at, r.status, r.listings_seen, r.listings_new, r.listings_changed,
               r.listings_removed, r.pages_fetched AS requests, r.http_errors, r.avg_response_ms
        FROM marketplaces m
        JOIN fetch_runs r ON r.id = (SELECT MAX(id) FROM fetch_runs WHERE marketplace_id = m.id)
        ORDER BY m.slug
        """
    )
    st.subheader("Latest run per marketplace")
    if latest.empty:
        st.info("No collection runs yet. Enable a marketplace and run the collect command.")
    else:
        st.dataframe(latest, hide_index=True)

    flags = query(
        """
        SELECT h.checked_on, m.slug, h.flag, h.detail
        FROM health_checks h JOIN marketplaces m ON m.id = h.marketplace_id
        ORDER BY h.checked_on DESC, m.slug LIMIT 200
        """
    )
    st.subheader("Flags")
    if flags.empty:
        st.success("No health flags raised.")
    else:
        st.warning(f"{len(flags)} flag(s) raised")
        st.dataframe(flags, hide_index=True)
