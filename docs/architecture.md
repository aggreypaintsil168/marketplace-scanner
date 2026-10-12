# Architecture

Marketplace Scanner collects listings from business-for-sale marketplaces every day,
keeps them as raw data with full history, and shows them in a searchable dashboard.
This page explains how the pieces fit together. Table details are in
[data-dictionary.md](data-dictionary.md); adding a site is in
[source-onboarding.md](source-onboarding.md).

## Data flow

```text
 registry/seed/nordics.yaml
          |  seed
          v
 +---------------------+    review (robots.txt + terms)    +--------------------+
 | marketplaces        | --------------------------------> | compliance_reviews |
 | collection_status:  |   only 'enabled' may be fetched   +--------------------+
 | pending -> enabled  |
 +----------+----------+
            | collect (daily, one source at a time)
            v
   PoliteFetcher  --robots.txt check, delay, request cap, stop on 429/503-->  source site
            |
            +--> raw_pages + data/raw/*.html.gz      (saved copy, internal only)
            v
   adapter.discover / parse_listing      (one small file per site)
            |
            v  scrub personal data (e-mails, phone numbers)
   history.events.ingest_observation
            |--> listings            (first_seen, last_seen, removed_on)
            |--> listing_observations (only when new or changed)
            '--> listing_events       (new / changed / removed / reappeared)
            |
            v
   health.checks  --> health_checks   (zero listings, volume drop, empty field, ...)
            |
            v
   dashboard/app.py   (Register, Listing search, Data health)
```

## Where things live

| Folder / file | Responsibility |
|---|---|
| `registry/models.py` | Register tables: marketplaces, compliance reviews, pricing snapshots |
| `registry/verification.py` | One-off robots.txt check and the rule that turns a review into a collection status |
| `registry/seed/nordics.yaml` | Starting entries for the register (all unverified until reviewed) |
| `collectors/base.py` | Polite fetcher, adapter interface, personal-data scrubbing |
| `collectors/robots.py` | Runtime robots.txt guard (fails closed) |
| `collectors/sources/` | One adapter file per marketplace; `example_site.py` is the template |
| `storage/models.py` | Listing tables: runs, listings, observations, events, raw pages, health |
| `storage/repository.py` | Database helpers: seeding, reviews, raw copies, retention purge |
| `history/events.py` | Turns daily observations into first/last seen and change events |
| `health/checks.py` | Decides whether a source is still delivering |
| `scheduler.py` | One collection run per source; runs all enabled sources |
| `main.py` | Command line (see below) |
| `dashboard/app.py` | Streamlit dashboard |
| `migrations/` | Alembic: every table change is a numbered script |
| `config/` | Settings from `.env`; logging configuration |

## Commands

Run everything from the repository root.

```bash
python -m marketplace_scanner.main seed                       # load the register seed
python -m marketplace_scanner.main list                       # show the register
python -m marketplace_scanner.main show <slug>                # one marketplace and its reviews
python -m marketplace_scanner.main update <slug> --url ...    # edit register fields
python -m marketplace_scanner.main review <slug> --reviewer "Name" --tos permitted
python -m marketplace_scanner.main collect [--source <slug>]  # daily collection
python -m marketplace_scanner.main health [--days 7]          # recent health flags
python -m marketplace_scanner.main purge-raw --days 365       # retention: delete old page copies
streamlit run dashboard/app.py                                # dashboard
python -m alembic upgrade head                                # apply database migrations
```

## Daily schedule

Use the operating system's scheduler, one task per source, at different times so the load
is spread over the day. Example for Windows Task Scheduler (daily at 03:15):

```text
schtasks /Create /SC DAILY /ST 03:15 /TN "scanner-bolagsplatsen-se" /TR "cmd /c cd /d C:\path\to\marketplace-scanner && python -m marketplace_scanner.main collect --source bolagsplatsen-se"
```

On Linux, use a cron line with the same command. Logs go to `data/scanner.log`.

## How the principles are met

| Principle | How |
|---|---|
| Raw first | Observations store text as shown; a gzip copy of each fetched page is kept under `data/raw/` |
| History | `listings.first_seen/last_seen/removed_on`, plus every change in `listing_events` with its date |
| Gentle and legal | Source must be `enabled` after a recorded review; robots.txt checked on every URL; delay between requests (never below the site's Crawl-delay); request cap per run; stops at once on HTTP 429/503; identifying user agent |
| Incremental | Overview pages are read daily, but a listing's own page is fetched only when it is new or its overview data changed |
| Self-healing | After every run the health check raises flags; a layout break cannot cause mass "removed" events (see guards below) |
| Source in under an hour | One adapter file per site, a template, and a step-by-step guide |

### Safety guards worth knowing

- **Fail closed.** If robots.txt cannot be read (error, 401/403/5xx), the site is not fetched.
- **No false removals.** A listing is only marked removed after a *complete* run. Nothing is removed if the run saw zero listings, and (from 10 active listings) nothing is removed if more than half would disappear at once, since that looks like a broken layout, not real sales.
- **Overview page failures** raise `DiscoveryError`, so the run is recorded as incomplete.

## Legal, privacy and ethics

- **Terms and robots.txt.** Read both before enabling a source. Record the result with the
  `review` command. If the terms prohibit automated collection (`--tos prohibited`), the
  source becomes `blocked_by_tos` and is never fetched.
- **If a source complains:** stop collecting from it immediately (record a review with
  `--tos prohibited`) and tell Jonas. Do not contact the site yourself.
- **Personal data (GDPR).** Listings can contain names, phone numbers and e-mail addresses.
  Stored text fields are scrubbed of e-mail addresses and phone numbers on a best-effort basis
  (`collectors/base.py`). `seller_raw` is meant for a broker's name and firm only. Legal basis:
  legitimate interest (market analysis). The saved page copies under `data/raw/` are
  *unscrubbed* evidence, so they are internal only and covered by the retention rule below.
- **Retention.** Saved page copies are kept for at most 12 months: run
  `purge-raw --days 365` regularly. Observation text stays; only the page copies go.
- **Deletion on request.** Not automated yet. Procedure for now: delete the matching
  rows from `listing_observations`/`listing_events` by listing URL, then run `purge-raw`
  for the affected period, and note the request in `docs/`.
- **Storage location.** Keep the database and `data/raw/` inside the EU (local disk, or an EU
  region if hosted).
- **Copyright.** Ad texts and images belong to the site or advertiser. Raw copies are for
  internal analysis only; never publish or republish them. Prefer aggregates in anything shared.
- **Secrets.** `.env` and `data/` are git-ignored. Never commit them.

## Not built yet

- Normalisation layer: EUR/SEK amounts, price mode, industry codes, country/region, broker IDs.
- Re-parsing a past period from saved page copies (the copies exist; the command does not).
- Real adapters for Bolagsplatsen.se and Yrityspörssi.fi (each needs its review first).
- Saved searches with daily notification, the map view, weekly summary by e-mail/Slack.
- Traffic estimates, price-list snapshots, monthly running-cost report.
