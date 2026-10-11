# Marketplace Scanner

A market-intelligence platform for discovering business-for-sale marketplaces, collecting listing observations, tracking historical changes, and analysing market activity across countries.

## Project Overview

Marketplace Scanner is an individual software engineering project developed to build a searchable database and dashboard for business-for-sale marketplaces.

The system is designed to maintain a register of marketplaces, collect listing data through permitted access methods, preserve raw observations and historical changes, and provide analytical views of marketplace activity.

The initial implementation focuses on the Nordic countries, using Sweden and Finland as reference markets before expanding to other regions.

## Objectives

- Build a structured register of business-for-sale marketplaces.
- Collect listing observations through documented and permitted methods.
- Preserve raw source data separately from derived and normalised fields.
- Track listing changes, first and latest observations, and removal events.
- Monitor collection health and detect unexpected changes in source behaviour.
- Provide searchable listings, marketplace comparisons, historical metrics, and data exports.
- Make the system maintainable through modular source adapters and clear documentation.

## Planned Technology Stack

| Component | Technology |
|---|---|
| Programming language | Python |
| Database | PostgreSQL |
| Database access | SQLAlchemy |
| Database migrations | Alembic |
| Data collection | Requests, HTTPX, BeautifulSoup |
| Dashboard | Streamlit |
| Visualisation | Plotly |
| Testing | Pytest |
| Version control | Git and GitHub |
| Local infrastructure | Docker Compose |

The stack may evolve as implementation and testing reveal additional requirements.

## Architecture

The planned data flow is:

1. Marketplace discovery and verification.
2. Daily collection through source-specific adapters.
3. Storage of raw observations and source metadata.
4. Historical event tracking for listing changes and removals.
5. Derivation of normalised fields and analytical metrics.
6. Collection-health checks and failure reporting.
7. Search, dashboard visualisation, and data export.

Raw observations will be retained separately from derived data so that normalisation rules can be revised without losing the original evidence.

## Responsible Data Collection

Collection will be designed to minimise the impact on third-party websites.

- Review each marketplace's terms of service and `robots.txt` before enabling automated collection.
- Prefer official APIs, feeds, and sitemaps where available and permitted.
- Use a descriptive user agent and conservative request rates.
- Avoid logged-in or paid areas and never bypass access controls.
- Record collection permissions, access methods, and review dates in the marketplace register.
- Stop automated collection where it is prohibited or where permission remains unresolved.
- Minimise personal data and provide a documented process for deletion requests.
- Keep raw listing content for authorised internal analysis only; do not republish source content through the public repository or dashboard.

## Initial Scope

The first implementation will prioritise:

- A structured marketplace register.
- Nordic marketplace discovery and verification.
- A PostgreSQL data model for marketplaces, listings, observations, events, and collection runs.
- Daily collection from eligible sources.
- Historical tracking and source-health monitoring.
- A dashboard for marketplace metrics and data quality.

Coverage will expand incrementally as sources are verified and collection methods are established.

## Repository Structure

```text
marketplace-scanner/
├── dashboard/
├── docs/
├── src/
│   └── marketplace_scanner/
├── tests/
├── .env.example
├── .gitignore
└── README.md
```

## Development Status

**Current phase:** Project foundation and architecture documentation.

Planned milestones:

- [x] Initialise the local Git repository.
- [x] Connect the repository to GitHub.
- [ ] Document the data model and source-onboarding process.
- [ ] Implement the marketplace register.
- [ ] Configure PostgreSQL and database migrations.
- [ ] Build and test the first eligible source adapter.
- [ ] Add historical observations and collection-health checks.
- [ ] Implement the dashboard and listing search.
- [ ] Expand coverage and document deployment and handover.

## Local Development

Setup instructions, environment configuration, database migrations, test commands, and deployment steps will be added as the application becomes runnable.

## Data and Security

Secrets, credentials, local environment files, database dumps, and private raw source captures must not be committed to the public repository.

## Licence

No licence has been selected yet. The repository's licensing terms will be determined separately.