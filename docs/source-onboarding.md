# Adding a marketplace (target: under one hour)

Work through the steps in order. **Never skip step 1**: a source can only be collected
after a recorded review, and the system enforces that.

## 1. Check legality (about 10 minutes)

1. Open the site's **terms of service** and look for any rule on automated access,
   scraping or copying content.
2. Open `https://<site>/robots.txt`.
3. Look for an **official API, RSS feed or sitemap**. Use these before parsing HTML.
4. Do **not** contact the site. Questions go to Jonas.

## 2. Put it in the register

Add an entry to `src/marketplace_scanner/registry/seed/nordics.yaml` (it is the only seed
file for now), quoting two-letter codes such as `"NO"` and `["no"]` (unquoted, YAML reads `no`
as false):

```yaml
  - slug: example-se
    name: Example.se
    country: SE
    languages: [sv]
    type: general
    status: candidate
```

Then load it and fill in what you know:

```bash
python -m marketplace_scanner.main seed
python -m marketplace_scanner.main update example-se --url https://www.example.se --owner-name "Example AB" --est-listings 800
```

`seed` only adds missing entries, so it never overwrites verified data.

## 3. Record the review

```bash
python -m marketplace_scanner.main review example-se --reviewer "Your Name" --tos permitted \
    --tos-url https://www.example.se/terms --path / --path /listings --notes "No clause on automated access"
```

`--tos` is your reading of the terms: `permitted`, `no_prohibition`, `unclear`,
`prohibited` or `unreviewed`. The command checks robots.txt once and sets the status:

- `enabled`: robots.txt allows it and the terms do not prohibit it.
- `blocked_by_tos` / `blocked_by_robots`: stop here. The site is marked and never fetched.
- `pending`: something is unclear. Ask Jonas.

Check the result with `python -m marketplace_scanner.main show example-se`.

## 4. Write the adapter (about 30 minutes)

1. Copy `collectors/sources/example_site.py` to `collectors/sources/example_se.py`.
2. Change `slug` to exactly the register slug (`example-se`) and rename the class.
3. Implement `discover(fetcher, base_url)`: read the overview pages with `fetcher.get_ok(url)`
   and `yield DiscoveredListing(source_listing_id, url, fields)` for every listing. Put in
   `fields` whatever the overview shows (usually title and price).
4. Implement `parse_listing(html, url)`: return a dict of raw fields from a listing's own page,
   or `None` if the layout is not recognised. Field names: `title`, `description`, `price_raw`,
   `revenue_raw`, `profit_raw`, `employees_raw`, `category_raw`, `location_raw`,
   `deal_type_raw`, `seller_raw`, `published_raw`, `status_raw`, `image_count`, `has_prospectus`.
5. Set the knobs: `min_delay` (seconds between requests; be generous), `max_requests`,
   `required_fields` (the fields the health check must see), `detail_pages = False` if
   overview pages already show everything.

Rules for adapters:

- Save fields **as shown**. Do not convert prices or translate.
- Put only a broker's name and firm in `seller_raw`. No private phone numbers or names.
- Always read overview pages with `get_ok`, so a failed page makes the run incomplete
  instead of looking like removed listings.
- Bump `parser_version` whenever the parsing logic changes.

## 5. Test it

Copy the `FakeSite` pattern from `tests/test_history.py`: serve a small saved copy of the
site's HTML through `httpx.MockTransport` and assert that your adapter finds the right listings
and fields. Run `python -m pytest -q`.

## 6. First real run

```bash
python -m marketplace_scanner.main collect --source example-se
python -m marketplace_scanner.main health --days 1
```

Then compare the active listing count in the dashboard or `list` with the number the site
publishes. For the Nordic reference sites the target is a difference below 5%.
Look at `data/scanner.log` if anything looks wrong.

## 7. Schedule it

Add a daily task at a time no other source uses (see `docs/architecture.md`).

## When a source breaks

1. `python -m marketplace_scanner.main health` shows the flag (for example `empty_field`).
2. Open a saved copy under `data/raw/<slug>/<date>/` (gzip) or the live page and see what changed.
3. Fix the selectors in the adapter, bump `parser_version`, run `collect --source <slug>`.
4. If the site asks you to stop or blocks you: record `review ... --tos prohibited`
   and tell Jonas. Do not contact the site.
