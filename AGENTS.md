# AGENTS.md

Four-phase pipeline, 8 research tools, compare mode, JSON output, and atomic signals for web-pulse.

```
research (available tools, parallel) → write → review → report.md
                           ↓                       → stdout (JSON)
                     [compare mode]
                           ↓
              research A ↘
              research B → (parallel, ≤4) → compare → report.md

research (available tools, parallel) → signals envelope → stdout (JSON)
```

Signals mode reuses the normal research phase and skips only the writer/reviewer steps.
Inputs are either a preset name from `topics.toml` or an ad-hoc `--topic` plus the usual `--scope`, `--compare`, `--questions`, `--limit`, `--since`, and `--verbose` flags.
For Pulse, `pulse-policy-weekly` now runs with `scope = "policy"` so the feed favors concrete policy movement from Congress, Federal Register, Regulations.gov, courts, and state legislation.

## 1. Researcher

Tools by scope:

| Scope | Tools |
|-------|-------|
| federal | web, academic, census, congress, federal_register, regulations, court |
| state:XX | web, academic, census, state_legislation |
| all | all 8 |
| news | web only |
| policy | congress, federal_register, regulations, court, state_legislation |

Behavior:
- MUST use ALL available tools (enforced via prompt; prompt is built dynamically from actual available tools so Gemini never sees or attempts unavailable ones)
- Tools gated by optional API keys are omitted from Gemini's tool list when the key is missing
- ToolRegistry only executes tools that are available for the requested scope; undeclared/out-of-scope tool calls are rejected
- Parallel tool execution via ThreadPoolExecutor
- Tool adapters return `ToolResult(findings, errors)`; only successful findings are added to `ResearchResults`
- Findings are deduplicated by URL within `ResearchResults.add()` — bill sources (CONGRESS, STATE_LEG, LEGISCAN) are exempt since the same URL can carry distinct status events
- `--since YYYY-MM-DD` filters results at the adapter level for: web_search, congress_search, federal_register_search, regulations_search, court_search
- Returns `ResearchOutput` with findings + metadata
- Tracks tool usage for --sources audit
- Max iterations configurable via `CIVIC_MAX_ITERATIONS` (default: 15)

Output: `ResearchOutput(text, results, scope_label)`

## 2. Writer

Structures into policy brief:
- Executive summary
- Background
- Key findings
- Policy options
- Recommendations
- Sources
- Appendix (optional)

## 3. Reviewer

Checks clarity, evidence, balance, structure.
Returns polished version.

## 4. Comparator (--compare mode)

Generates side-by-side analysis:
- Comparison matrix
- Jurisdiction-specific findings
- Key differences
- Common ground
- Cross-jurisdictional recommendations

## Data Structures

```python
@dataclass
class Finding:
    title: str
    snippet: str
    url: str
    date: str         # YYYY-MM-DD or YYYY
    source_type: str  # WEB, ACADEMIC, CONGRESS, REGULATIONS, etc
    citations: int    # for academic papers

    def to_dict() -> dict  # for JSON output

@dataclass
class ToolResult:
    findings: list[Finding]
    errors: list[str]

@dataclass
class ResearchResults:
    findings: list[Finding]
    tool_usage: dict[str, int]
    # _seen_urls deduplicates by URL on add(); bill sources exempt

    def confidence_score() -> (level, explanation)
    def to_dict() -> dict      # for JSON output
    def to_appendix() -> str   # for output
```

## Confidence Scoring

```
Score = (diversity × 0.4) + (recency × 0.3) + (citations × 0.3)

●●●●● HIGH   — 5+ source types, recent, cited
●●●○○ MEDIUM — 3-4 sources, some dated
●○○○○ LOW    — 1-2 sources, old data
```

## Tools

Per-tool result cap defaults to `RESULTS_LIMIT = 25` (set in `src/tools/base.py`).
Override at runtime with `--limit N` on `civic <topic>`, `civic run <preset>`, or
`civic signals <preset>`; this calls `set_results_limit()` before research kicks off.
Census is still capped to up to 5 rows by the adapter.

| Tool | API | Key | Results |
|------|-----|-----|---------|
| web_search | Exa | `EXA_API_KEY` required when scope includes web search | RESULTS_LIMIT |
| academic_search | Semantic Scholar | None | RESULTS_LIMIT |
| census_search | US Census | `CENSUS_API_KEY` optional (better limits) | up to 5 |
| congress_search | Congress.gov | `CONGRESS_GOV_API_KEY` optional; enables source | RESULTS_LIMIT |
| federal_register_search | Federal Register | None | RESULTS_LIMIT |
| regulations_search | Regulations.gov | `REGULATIONS_GOV_API_KEY` optional; enables source | RESULTS_LIMIT |
| court_search | CourtListener | None | RESULTS_LIMIT |
| state_legislation_search | OpenStates + LegiScan fallback | `OPENSTATES_API_KEY` optional; `LEGISCAN_API_KEY` enables single-state fallback | RESULTS_LIMIT |

## Infrastructure

- **Retry**: tool HTTP fetches retry 3 times with exponential backoff on timeouts, connection errors, and 429/5xx; Gemini generation retries are controlled separately via `CIVIC_MAX_RETRIES` (default: 4, 429 only)
- **Cache**: SQLite at `~/.cache/civic/cache.db`, 24h TTL, keyed on URL + params (API key params stripped before hashing so cache survives key rotation)
- **Parallel execution**: ThreadPoolExecutor; up to 8 concurrent tool calls per research iteration, up to 4 concurrent scopes in compare mode
- **Input validation**: Empty queries return `ToolResult(errors=[...])` and do not hit upstream APIs

## Prompts

`src/prompts.py`:
- `build_researcher_prompt(tool_declarations)` — dynamically builds RESEARCHER from `RESEARCHER_HEADER` + available tool list + `RESEARCHER_FOOTER`
- `WRITER` — brief structure
- `REVIEWER` — quality checks (skipped when `--no-review`)
- `COMPARATOR` — comparison analysis

## Agent Integration

For programmatic use by other agents:

```bash
uv run civic doctor                            # preflight: validate required + optional API keys
uv run civic "topic" -s federal -f json        # run brief pipeline, JSON to stdout
uv run civic "topic" -f json --limit 50        # widen per-tool results
echo "topic" | uv run civic - -f json          # read topic from stdin
uv run civic signals pulse-policy-weekly       # atomic per-finding JSON for web-pulse
uv run civic get <url> -f json                 # fetch arbitrary URL as JSON envelope
```

`civic -f json` returns structured JSON with findings, confidence, and tool usage.
`civic signals ...` returns a stable signals envelope (`schema_version = 1`) for downstream ingestion.
Bill-like signals now carry movement metadata (`status`, `signal_kind`, `pending`) and use movement-aware IDs so later actions on the same bill can surface as distinct signals.
Exit code 0 on success, 1 on error, 130 on interrupt.
Rich formatting auto-disables when stdout is not a TTY or when `NO_COLOR` is set.
