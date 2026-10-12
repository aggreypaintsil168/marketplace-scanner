# Data dictionary

Raw columns hold text exactly as the source showed it. Anything normalised (EUR amounts,
industry codes, ...) will be computed later from these and can be recalculated.
All tables are created by Alembic migrations in `migrations/versions/`.

## Register (Layer 1)

### marketplaces
One row per site that lists businesses for sale.

| Column | Meaning |
|---|---|
| `slug` | Unique short name, e.g. `bolagsplatsen-se`. Must equal the adapter's `slug` |
| `name`, `url` | Display name and site address (`url` empty until verified) |
| `country` | ISO 3166-1 alpha-2 code (`NULL` = global) |
| `languages` | List of ISO 639-1 codes |
| `type` | `general`, `niche`, `broker_portal`, `broker_chain` |
| `niche` | What the niche is (e-commerce, SaaS, ...) |
| `owner_name`, `owner_reg_no`, `founded_year` | From the company registry or site footer |
| `business_model` | Listing fee, subscription, success fee |
| `est_listings` | Manual estimate at registration |
| `status` | `candidate`, `active`, `dead`, `acquired` |
| `collection_status` | `pending`, `enabled`, `blocked_by_tos`, `blocked_by_robots`, `manual_only`. Changes only through a compliance review |
| `access_method` | `api`, `rss`, `sitemap` or `html` |
| `verified_by`, `verified_at`, `on_watchlist`, `watch_notes` | Follow-up bookkeeping |

### compliance_reviews
Append-only log of robots.txt and terms checks (the legal audit trail).

| Column | Meaning |
|---|---|
| `reviewed_on`, `reviewer` | When and by whom |
| `robots_url`, `robots_allows`, `crawl_delay` | Result of the robots.txt check (`NULL` = undetermined) |
| `tos_url`, `tos_decision` | Your reading of the terms: `unreviewed`, `permitted`, `no_prohibition`, `unclear`, `prohibited` |
| `api_or_feed_url` | Official API/RSS/sitemap if one exists |
| `notes` | Anything worth remembering |

### pricing_snapshots
Dated captures of a site's price list (table exists; capture is not built yet).

## Listings (Layer 2)

### fetch_runs
One collection run for one marketplace.

| Column | Meaning |
|---|---|
| `started_at`, `finished_at`, `parser_version` | When it ran, with which parsing logic |
| `status` | `running`, `ok`, `partial` (stopped early, some data), `failed` |
| `complete` | True when the full sweep finished cleanly; only then may removals be recorded |
| `pages_fetched`, `http_errors`, `avg_response_ms` | Load and speed |
| `listings_seen/new/changed/removed` | Counts for the run |
| `field_fill_rate` | Share of listings where each required field could be read |
| `error` | Why a run stopped early |

### listings
One advertised business.

| Column | Meaning |
|---|---|
| `source_listing_id` | The site's own ID (unique per marketplace) |
| `url` | Address of the listing |
| `first_seen`, `last_seen` | First and latest day it was observed |
| `removed_on` | Day it disappeared (`NULL` while active) |

### listing_observations
Append-only snapshot, written when a listing is new or changed.

| Column | Meaning |
|---|---|
| `observed_on`, `observed_at`, `run_id`, `parser_version` | Provenance |
| `raw_page_id` | The saved page it came from (cleared by retention purge) |
| `content_hash` | Fingerprint for cheap change detection |
| `title`, `description` | Text in the original language (personal data scrubbed) |
| `price_raw`, `revenue_raw`, `profit_raw`, `employees_raw` | As stated, e.g. "1 200 000 kr", "Offers" |
| `category_raw`, `location_raw`, `deal_type_raw` | The site's own wording |
| `seller_raw` | Broker name/firm only |
| `published_raw`, `status_raw` | Date and status as the site shows them |
| `image_count`, `has_prospectus` | Attachments |

### listing_events
`event_type` is `new`, `changed`, `removed` or `reappeared`. For `changed`, `field`, `old_value`
and `new_value` say what changed (values cut to 500 characters). `event_date` is the day it was detected.

### raw_pages
Pointer to a gzip copy of a fetched page under `data/raw/` (`storage_path` is relative to that
folder). Internal evidence only; never publish.

### health_checks
A flag for a marketplace on a day. `flag` is one of:

| Flag | Raised when |
|---|---|
| `fetch_failed` | The run stopped before reading any listing |
| `zero_listings` | The run finished but saw nothing |
| `volume_drop` | Fewer than 50% of the recent average listings seen |
| `empty_field` | A required field is unreadable, or its readable share dropped by 20+ points |
| `fetch_errors` | More than 10% of requests failed |
| `slow_response` | Over 2 seconds and 3x slower than usual |
