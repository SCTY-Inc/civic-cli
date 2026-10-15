# civic-cli-tools

One search across official and open policy sources: Congress bills (GovInfo),
Federal Register, Regulations.gov, CourtListener, Open States, LegiScan,
Census, Semantic Scholar, and Exa web search. Every record says where it came
from: adapter, endpoint, parameters, HTTP status, time, terms, and a content
hash.

## Install

```bash
uv tool install git+https://github.com/SCTY-Inc/civic-cli
cp .env.example .env   # all keys optional; see SOURCES.md
civic doctor           # which sources can run
```

## Use

```bash
civic search "paid family caregiver" --scope policy --since 2025-01-01
civic search "caregiver tax credit" --scope state:CA,NY --format jsonl
civic search "respite care" --sources federal_register,courtlistener --raw-dir ./raw
```

Scopes: `all` (default), `federal`, `policy`, `news`, `state:XX,YY`.

```python
from civic import search
envelope = search("paid family caregiver", scope="policy", since="2025-01-01")
```

Output schema, adapters, and rules: [AGENTS.md](AGENTS.md).
Credits, terms, and rate limits: [SOURCES.md](SOURCES.md).

## License

MIT. Source data stays under each provider's terms.
