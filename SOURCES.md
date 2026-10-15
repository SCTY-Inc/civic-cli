# Sources and attribution

Type: reference.

civic wraps the services below. Each record names its adapter and carries a
short terms note in `provenance.terms`. This page is the full credit. Check
the provider docs before heavy use. "Verified" means checked against the
provider page on 2026-10-15. Other limits come from provider docs and were
not checked again.

| Adapter | Service | Docs | License / terms | Credential | Rate limit |
|---|---|---|---|---|---|
| `govinfo` | GovInfo search API (U.S. Government Publishing Office), BILLS collection. Records link to Congress.gov bill pages. | https://api.govinfo.gov/docs/ | U.S. government work, public domain (17 U.S.C. 105) | `GOVINFO_API_KEY` (api.data.gov) | 36,000/hour, 1,200/minute, 40/second (verified, github.com/usgpo/api) |
| `federal_register` | Federal Register API v1 (Office of the Federal Register, GPO) | https://www.federalregister.gov/developers/documentation/api/v1 | U.S. government work, public domain | none | No published key limit; use reasonable request rates |
| `regulations` | Regulations.gov API v4 (GSA eRulemaking Program) | https://open.gsa.gov/api/regulationsgov/ | U.S. government data. Public comments and attachments can carry third-party rights. | `REGULATIONS_GOV_API_KEY` (api.data.gov) | api.data.gov default 1,000/hour (verified) |
| `courtlistener` | CourtListener REST API v4 search (Free Law Project) | https://www.courtlistener.com/help/api/rest/ | Court opinions are public domain. CourtListener terms of use apply to the service. | none (anonymous) | Authenticated: 5/minute, 50/hour, 125/day (verified). Anonymous limits are not published. |
| `openstates` | Open States API v3 (Plural) | https://docs.openstates.org/api-v3/ | Public domain dedication unless noted; attribution requested (verified, open.pluralpolicy.com/data) | `OPENSTATES_API_KEY` | Per account tier; see docs |
| `legiscan` | LegiScan API `getSearch` | https://legiscan.com/legiscan | CC BY 4.0; credit LegiScan | `LEGISCAN_API_KEY` | Free public tier: 30,000 queries/month |
| `census` | U.S. Census Bureau Data API, ACS 5-year 2022 | https://www.census.gov/data/developers/guidance/api-user-guide.html | Public domain. Required notice: "This product uses the Census Bureau Data API but is not endorsed or certified by the Census Bureau." | `CENSUS_API_KEY` optional | 500 queries/day per IP without a key |
| `semantic_scholar` | Semantic Scholar Academic Graph API (Allen Institute for AI) | https://api.semanticscholar.org/api-docs/graph | Semantic Scholar API License Agreement. Abstracts belong to their publishers. | none (optional key not used) | Unauthenticated: 1,000/second shared by all users (verified) |
| `exa` | Exa search API | https://docs.exa.ai/reference/search | Exa terms of service. Page content belongs to its publishers. | `EXA_API_KEY` | Paid per request; see account |

Not wrapped: the Congress.gov API. Its bill endpoint has no keyword filter, so
topic search uses GovInfo and links each bill to Congress.gov.
