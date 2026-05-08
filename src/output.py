import json
from datetime import datetime, timezone
from pathlib import Path


def save_report(content: str, path: Path) -> None:
    header = f"""---
generated: {datetime.now(timezone.utc).isoformat()}
tool: civic
---

"""
    path.write_text(header + content)


def format_json(topic: str, scope: str, results_dict: dict[str, object]) -> str:
    output = {
        "topic": topic,
        "scope": scope,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        **results_dict,
    }
    return json.dumps(output, indent=2)
