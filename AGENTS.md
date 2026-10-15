# AGENTS.md

Type: reference.

civic wraps official and open policy sources behind one search interface and
one traceable record envelope. It has no model calls, no reports, no cache,
and no state. Callers own planning, credentials policy, approval, and durable
writes. This file is canonical; README.md is the short human entry.

```
search(topic, scope, since, sources, limit)
  → selected adapters, in parallel, one Transport
  → Envelope { run_id, query, sources[] (calls), errors[], records[] (provenance) }
```

## Layout

| Path | Role |
|---|---|
| `src/civic/adapters.py` | One `Adapter` per service, the `ADAPTERS` registry, `Query` |
| `src/civic/http.py` | `Transport`: retries, per-call trace (`Call`), optional raw save, `SourceError` |
| `src/civic/record.py` | `Record`, provenance, stable IDs, bill/rule movement classification |
| `src/civic/search.py` | `search()` fan-out and `Envelope` |
| `src/civic/scope.py` | `Scope`, `parse_scope`, state FIPS codes |
| `src/civic/cli.py` | `civic search`, `civic doctor` |
| `SOURCES.md` | Attribution, docs, terms, credentials, rate limits per service |
| `tests/` | Offline tests; `httpx.MockTransport` serves `tests/fixtures/` |

## Commands

```bash
uv sync
uv run pytest -q                       # offline; must stay green
uv run civic doctor [--scope S] [--sources a,b]
uv run civic search "<topic>" --scope policy --since 2025-01-01 --format json
uv run civic search "<topic>" --sources openstates,legiscan --format jsonl --raw-dir /tmp/civic-raw
```

Python API (import from another repo, or shell out to the CLI):

```python
from civic import search
envelope = search("paid family caregiver", scope="policy", since="2025-01-01")
data = envelope.to_dict()
```

`search()` raises `ValueError` only for bad input. It never raises for a source
failure; failures go to `errors[]`. CLI exit codes: 0 when at least one
selected source answered, 1 when none did, 2 for usage errors, 130 on interrupt.

## Adapters and scopes

| Adapter | Scopes | Credential | `since` |
|---|---|---|---|
| `govinfo` | all, federal, policy | `GOVINFO_API_KEY` | publish date |
| `federal_register` | all, federal, policy | none | publication date |
| `regulations` | all, federal, policy | `REGULATIONS_GOV_API_KEY` | posted date |
| `courtlistener` | all, federal, policy | none | filed date |
| `openstates` | all, state, policy | `OPENSTATES_API_KEY` | latest action |
| `legiscan` | all, state, policy | `LEGISCAN_API_KEY` | latest action (filtered locally) |
| `census` | all, federal, state | `CENSUS_API_KEY` optional | not applied (ACS 2022) |
| `semantic_scholar` | all, federal, state | none | publication date |
| `exa` | all, federal, state, news | `EXA_API_KEY` | published date |

- Scope `state:CA,NY` runs one OpenStates and one LegiScan query per state.
  Without states they search every jurisdiction (LegiScan `state=ALL`).
- `--sources` overrides scope selection. A named source without its key is an
  error; an unnamed one is `skipped` in `sources[]`.
- `--limit` caps records per adapter query (default 25). OpenStates pages at
  20, up to 5 pages.
- GovInfo returns the newest text version per bill and links Congress.gov.
  The Congress.gov API is not wrapped: its bill endpoint has no keyword filter.
- Relevance is the upstream service's. GovInfo and OpenStates match full text,
  so some records match the words but not the subject. Callers filter.

## Envelope (schema `civic.search.v1`)

- `run_id`, `civic_version`, `query`, `started_at`, `finished_at`, `counts`.
- `sources[]`: `name`, `version`, `status` (`ok` | `partial` | `error` |
  `skipped`), `records`, `reason`, and `calls[]` (`method`, `endpoint`,
  `params`, `http_status`, `retrieved_at`, `body_sha256`, `raw_path`).
- `errors[]`: `source`, `error`, and `endpoint` and `http_status` when a
  request was made.
- `records[]`: `id`, `source`, `kind` (`bill` | `regulatory` | `opinion` |
  `paper` | `web` | `statistic`), `title`, `url`, `summary`, `published_at`,
  optional `jurisdiction`, `identifier`, `status`, `movement`, `pending`,
  `official_url`, `citations`, `details`, and `provenance` (`adapter`,
  `adapter_version`, `method`, `endpoint`, `params`, `http_status`,
  `retrieved_at`, `terms`, `item_sha256`, `run_id`, `raw_path`).
- JSONL: line 1 is `{"type": "run", ...header}`; each later line is
  `{"type": "record", ...record}`.
- Bill IDs key on the movement (status and date), so a later action on the same
  bill is a new record. Other IDs key on the URL. Records dedupe by ID.
- `movement` for bills: `bill_introduced`, `bill_referred`, `bill_advanced`,
  `bill_enacted`, `bill_vetoed`, `bill_update`. For regulatory documents:
  `proposed_rule`, `final_rule`, `agency_notice`, `docket_document`.
  Opinions: `court_action`.

## Rules

- Keys never enter a trace or raw file. Send credentials in headers. If a
  service accepts a key only as a query parameter (Census, LegiScan), list it
  in `secret_params`.
- An adapter wraps exactly one service. Do not add fallbacks between adapters;
  callers select sources.
- Add an adapter by subclassing `Adapter`, registering it in `ADAPTERS`, adding
  its fixture and tests, and adding its row to `SOURCES.md` (a test enforces
  the row).
- Bump an adapter's `version` when its record mapping changes.
- Tests stay offline. Record or hand-build fixtures from documented shapes.
- Transport retries timeouts, connection errors, and 429/5xx 3 times with
  backoff. There is no response cache; each run reflects live calls.
- For any web requests you make with curl or otherwise while working here, set
  the user agent to "OpenAI File Downloader, XaiImageApiFetch/1.0".
