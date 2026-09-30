# civic-cli-tools

Policy-source adapters and an optional brief-writing CLI for eight government,
web, and academic sources.

```text
civic signals --direct → source adapters → normalized signals JSON
civic <topic>           → Gemini research → write/review → brief or JSON
```

Automation should prefer `signals --direct`: it performs one bounded fan-out
across the source adapters without model orchestration or repository writes.
Workflow owners such as Hound supply planning, approvals, and durable state.

## Install

```bash
uv tool install git+https://github.com/SCTY-Inc/civic-cli  # installs the `civic` command
```

For development:

```bash
uv sync
cp .env.example .env  # add API keys
```

## API Keys

| Key | When Needed | Get It | Cost |
|-----|-------------|--------|------|
| GOOGLE_API_KEY | Brief/research mode only; not used by `signals --direct` | [aistudio.google.com/apikey](https://aistudio.google.com/apikey) | Free |
| EXA_API_KEY | When scope includes web search (`all`, `federal`, `state:XX`, `news`) | [dashboard.exa.ai/api-keys](https://dashboard.exa.ai/api-keys) | Free tier |
| CONGRESS_GOV_API_KEY | Optional — unlocks Congress.gov source | [api.congress.gov/sign-up](https://api.congress.gov/sign-up) | Free |
| OPENSTATES_API_KEY | Optional — unlocks OpenStates source | [openstates.org/accounts/register](https://openstates.org/accounts/register/) | Free |
| LEGISCAN_API_KEY | Optional — situational fallback for single-state legislation searches | LegiScan account | Free public tier |
| REGULATIONS_GOV_API_KEY | Optional — unlocks Regulations.gov source | [open.gsa.gov/api/regulationsgov/](https://open.gsa.gov/api/regulationsgov/) | Free |
| CENSUS_API_KEY | Optional — improves Census rate limits | [api.census.gov/data/key_signup](https://api.census.gov/data/key_signup.html) | Free |

No key needed: Semantic Scholar, Federal Register, CourtListener.

## Usage

```bash
civic "Solar energy policy"                    # all sources
civic "AI regulation" -s federal               # federal only
civic "Rent control" -s state:CA,NY            # specific states
civic "Housing" -q "Impact of zoning reform?"  # with questions
civic "Climate policy" -v                      # verbose
civic "Healthcare" --sources                   # show confidence + source audit
civic "AI regulation" --limit 10               # cap per-tool results (default 25)
echo "Paid leave" | civic -                    # read topic from stdin
```

### Environment Check

```bash
civic doctor                                   # validate required + optional API keys
```

Brief/research mode requires `GOOGLE_API_KEY`. Direct signals mode does not.
`EXA_API_KEY` is required when the selected scope includes web search. Optional
source keys unlock or improve Congress.gov, OpenStates/LegiScan,
Regulations.gov, and Census without blocking keyless sources.

### Fetch URL

```bash
civic get https://example.com/bill.pdf         # raw body to stdout
civic get https://example.com -f json          # JSON envelope (status, headers, content)
```

### Signals output

```bash
civic signals --direct --topic "family caregiver policy" -s federal --limit 2
civic signals pulse-policy-weekly # model-selected legacy/preset mode
```

`--direct` runs every available adapter for the selected scope once, in
parallel, and emits the stable atomic signals envelope. It does not call Gemini,
write a report, or review results. This is the supported integration primitive
for Hound and other workflow owners.

Without `--direct`, signals mode retains the model-selected research loop for
interactive use. Direct mode accepts one preset or `--topic`, plus `--scope`,
`--limit`, `--since`, and `--verbose`; compare mode remains model-owned.

Bill-like signals include movement metadata when available:
- `status` — latest bill/rule action text
- `signal_kind` — normalized movement such as `bill_referred`, `bill_advanced`, `proposed_rule`
- `pending` — whether the item still represents an active/pending policy movement
- movement-aware `id` values — later bill actions can surface as distinct signals instead of collapsing to one URL

### JSON Output (for agents)

```bash
civic "AI regulation" -s federal -f json       # structured JSON to stdout
civic "Caregiver policy" -f json | jq '.findings | length'
```

JSON schema:
```json
{
  "topic": "...",
  "scope": "...",
  "timestamp": "ISO8601",
  "confidence": { "level": "HIGH", "detail": "..." },
  "findings": [{ "title", "snippet", "url", "date", "source_type", "citations" }],
  "tool_usage": { "congress_search": 10, ... }
}
```

### Compare Mode

```bash
civic "Paid leave" --compare CA,NY             # state vs state
civic "Healthcare" --compare federal,CA        # federal vs state
civic "Immigration" --compare policy,news      # legislation vs media
civic "Cannabis" --compare CA,CO,NY            # multi-state
```

### Topic Presets

```bash
civic topics                                   # list presets
civic run caregiver-federal                    # run named preset
civic run ai-regulation-federal -f json        # preset with JSON output
```

## Options

```
-s, --scope SCOPE      federal | state:XX | all | news | policy (default)
-c, --compare A,B      compare targets: CA,NY or federal,CA or policy,news
-f, --format FMT       markdown (file) | json (stdout)
-o, --output FILE      default: outputs/report.md
-q, --questions Q      research questions
-v, --verbose          show tool calls
--sources              show confidence score + source usage
--no-appendix          exclude source appendix from output
--no-review            skip the reviewer pass (faster; lower polish)
--since YYYY-MM-DD     filter results to items published on/after date
--limit N              per-tool results cap (default: 25)
-V, --version
```

Pass `-` as the topic to read it from stdin. Rich output respects `NO_COLOR` and auto-disables when stdout is not a TTY.

Subcommands:
- `civic run <preset>`      run a named preset from `topics.toml`
- `civic signals <preset>`  emit atomic per-finding JSON for web-pulse / agents
- `civic topics`            list packaged or local presets
- `civic doctor`            env/API-key preflight
- `civic get <url>`         fetch raw body or JSON envelope
- `civic cache stats|clear` inspect or clear the SQLite cache

### Cache Management

```bash
civic cache stats                              # show cache size + entries
civic cache clear                              # purge all cached responses
```

## Output Features

- **Confidence scoring**: HIGH/MEDIUM/LOW based on source diversity, recency, citations
- **Source appendix**: Raw findings with dates and URLs for verification
- **Atomic signals JSON**: Stable per-finding envelope for web-pulse ingestion and other downstream consumers
- **Policy movement metadata**: bill/rule signals include status, normalized movement kind, pending state, and movement-aware IDs
- **Comparison matrix**: Side-by-side analysis for --compare mode
- **Response caching**: 24h SQLite cache at `~/.cache/civic/` — repeat queries are instant; cache keyed on URL + params with API keys stripped so it survives key rotation

## Tools

| Tool | Source | Data |
|------|--------|------|
| web_search | Exa | news, articles |
| academic_search | Semantic Scholar | 200M+ papers |
| census_search | US Census | demographics, income, housing (17 variables) |
| congress_search | Congress.gov | federal bills |
| federal_register_search | Federal Register | rules, notices |
| regulations_search | Regulations.gov | dockets, comments, rulemaking |
| court_search | CourtListener | federal case law |
| state_legislation_search | OpenStates + LegiScan fallback | 50 state bills |

## Configuration

| Env Var | Default | Purpose |
|---------|---------|---------|
| CIVIC_MODEL | gemini-3.1-flash-lite-preview | Gemini model for all phases |
| CIVIC_MAX_ITERATIONS | 15 | Max tool calls per research phase |

## Structure

```
src/
├── cli.py               # entry, scope/compare parsing, run/signals/doctor/get
├── _agent_cli.py        # minimal doctor helpers shared by the CLI
├── agents.py            # gemini orchestration, parallel tool execution
├── scopes.py            # shared scope parsing + labeling helpers
├── prompts.py           # RESEARCHER, WRITER, REVIEWER, COMPARATOR
├── output.py            # markdown + JSON output (brief mode)
├── output_signals.py    # per-finding atomic JSON (schema v1 for web-pulse)
└── tools/
    ├── models.py        # Finding, ToolResult, ResearchResults
    ├── declarations.py  # Gemini function specs
    ├── implementations.py  # 8 tool classes
    ├── registry.py      # ToolRegistry + ToolResult formatting
    └── base.py          # BaseTool, ToolResult helpers, retry, caching, set_results_limit
tests/
├── test_agents.py       # scope labeling helpers
├── test_cli.py          # scope parsing, env checks, JSON-mode regressions
├── test_models.py       # Finding, ResearchResults, confidence
├── test_output_signals.py # atomic signal schema + extractor behavior
├── test_scopes.py       # shared scope parsing + labels
└── test_tools.py        # tools, provider errors, limits, registry filtering with mocked HTTP
```

## Changelog

| Date | Change |
|------|--------|
| **2026-07-21** | **v0.6.1** — deterministic `signals --direct` source fan-out for Hound and other workflow owners; no Gemini required |
| **2026-05-07** | **code quality + features** — `--since` date filter (web, congress, fed-register, regulations, court), `--no-review` flag, URL-based finding dedup (bill sources exempt), dynamic RESEARCHER prompt scoped to available tools, cache key strips API keys, tool declarations cached by scope |
| **2026-04-22** | **post-audit fixes** — Rich JSON-mode compatibility, Exa SDK update, signals docs for web-pulse, env-aware source gating, packaged presets, CI, LegiScan single-state fallback |
| **2026-04-09** | **v0.6** — 8th source (Regulations.gov), `--format json`, parallel execution, retry/caching, 18 Census variables, 41 tests |
| **2026-01-28** | **v0.5** — topics.toml presets, `run`/`topics` subcommands, gemini-2.0-flash |
| **2026-01-25** | **v0.4** — Compare mode (`--compare CA,NY`), confidence scoring, source appendix, refactored tools/ package |
| **2026-01-25** | **v0.3** — 7 API tools: Exa, Semantic Scholar, Congress.gov, Federal Register, CourtListener, Census, OpenStates |
| **2026-01-25** | **v0.2** — Rewrite: CrewAI → vanilla Python + Gemini. Streamlit UI → CLI. 50+ deps → 5 deps |
| **2023-12-27** | **v0.1** — Initial commit: CrewAI trip planning demo |

**Key metrics:**
- Dependencies: 50+ → 5
- Install size: 918MB → ~50MB
- Data sources: 1 (web) → 8 (gov + academic APIs)
- Tests: 0 → 74

## License

MIT
