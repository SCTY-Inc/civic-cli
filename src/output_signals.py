"""Atomic per-finding signal output.

Used by `civic signals` for downstream consumers (web-pulse, etc.) that want
individual policy items rather than a Gemini-synthesized markdown brief.
Each signal is a stable JSON object. For document-like sources the ID is URL-keyed;
for bill-like sources the ID is keyed to the policy movement, so later status changes
can surface as distinct signals while identical movements still dedupe idempotently.

Schema (v1):

{
  "schema_version": 1,
  "topic": "...",
  "preset": "caregiver-federal" | null,
  "scope": "federal" | "federal + state" | "state:CA" | "compare:...",
  "fetched_at": "2026-04-22T09:15:00Z",
  "counts": { "signals": 12, "by_source_type": { "CONGRESS": 4, ... } },
  "signals": [
    {
      "id": "congress-...-slug",
      "source_tool": "congress_search",
      "source_type": "CONGRESS",
      "source_system": "congress.gov",
      "title": "...",
      "url": "...",
      "summary": "...",
      "published_at": "2024-04-12",
      "identifier": "HR 2406",          # optional, per source type
      "status": "...",                  # optional, per source type
      "signal_kind": "bill_referred",   # optional, movement classification
      "pending": true,                   # optional, whether the item is still active
      "congress": "118",                # optional, Congress only
      "jurisdiction": "federal" | "state:CA",  # optional
      "citations": 0                    # ACADEMIC only
    }
  ]
}
"""

import json
import re
from collections.abc import Callable
from datetime import datetime, timezone

from tools import Finding, ResearchResults

SCHEMA_VERSION = 1

SOURCE_SYSTEMS = {
    "WEB": "web",
    "ACADEMIC": "semanticscholar.org",
    "CONGRESS": "congress.gov",
    "FED_REGISTER": "federalregister.gov",
    "REGULATIONS": "regulations.gov",
    "COURT": "courtlistener.com",
    "CENSUS": "data.census.gov",
    "STATE_LEG": "openstates.org",
    "LEGISCAN": "legiscan.com",
}

SOURCE_TO_TOOL = {
    "WEB": "web_search",
    "ACADEMIC": "academic_search",
    "CONGRESS": "congress_search",
    "FED_REGISTER": "federal_register_search",
    "REGULATIONS": "regulations_search",
    "COURT": "court_search",
    "CENSUS": "census_search",
    "STATE_LEG": "state_legislation_search",
    "LEGISCAN": "state_legislation_search",
}


def _extract_congress(title: str, snippet: str) -> dict[str, str]:
    """CongressSearch formats: title='HR2406: Act name', snippet='Congress N | Status: X'."""
    out: dict[str, str] = {"jurisdiction": "federal"}
    m = re.match(r"^([A-Z]+)\s*(\d+):\s*(.*)$", title or "")
    if m:
        out["identifier"] = f"{m.group(1)} {m.group(2)}"
        out["title"] = m.group(3).strip() or title
    status_m = re.search(r"Status:\s*(.+?)(?:\s*\||$)", snippet or "")
    if status_m:
        out["status"] = status_m.group(1).strip()
    congress_m = re.search(r"Congress\s+(\d+)", snippet or "")
    if congress_m:
        out["congress"] = congress_m.group(1)
    return out


def _extract_field(snippet: str, label: str) -> str:
    match = re.search(rf"{re.escape(label)}:\s*(.+?)(?:\s*\||$)", snippet or "", re.IGNORECASE)
    return match.group(1).strip() if match else ""


def _extract_title_identifier(title: str) -> str:
    match = re.match(r"^([^:]+):", title or "")
    if not match:
        return ""
    return re.sub(r"[^A-Za-z0-9]+", "", match.group(1))


def _extract_fed_register(url: str, snippet: str) -> dict[str, str]:
    """FR URLs: /documents/YYYY/MM/DD/DOCUMENT-ID/slug."""
    out: dict[str, str] = {"jurisdiction": "federal"}
    m = re.search(r"/documents/\d{4}/\d{2}/\d{2}/([^/]+)", url or "")
    if m:
        out["identifier"] = m.group(1)
    if status := _extract_field(snippet, "Type"):
        out["status"] = status
    return out


def _extract_regulations(url: str, snippet: str) -> dict[str, str]:
    out: dict[str, str] = {"jurisdiction": "federal"}
    m = re.search(r"/document/([^/?#]+)", url or "")
    if m:
        out["identifier"] = m.group(1)
    if status := _extract_field(snippet, "Type"):
        out["status"] = status
    return out


def _extract_state_bill(title: str, snippet: str, url: str, *, url_pattern: str) -> dict[str, str]:
    out: dict[str, str] = {}
    m = re.search(url_pattern, url or "")
    if m:
        out["jurisdiction"] = f"state:{m.group(1)}"
        out["identifier"] = m.group(2)
    else:
        out["jurisdiction"] = "state"
        if identifier := _extract_title_identifier(title):
            out["identifier"] = identifier
    if status := _extract_field(snippet, "Latest"):
        out["status"] = status
    return out


def _extract_state_leg(title: str, snippet: str, url: str) -> dict[str, str]:
    """OpenStates URLs: /{ST}/bills/{session}/{bill-id}/."""
    return _extract_state_bill(title, snippet, url, url_pattern=r"/([A-Z]{2})/bills?/[^/]+/([^/?#]+)")


def _extract_legiscan(title: str, snippet: str, url: str) -> dict[str, str]:
    return _extract_state_bill(title, snippet, url, url_pattern=r"legiscan\.com/([A-Z]{2})/bill/([^/?#]+)")


from collections.abc import Callable

EXTRACTORS: dict[str, Callable[[Finding], dict[str, str]]] = {
    "CONGRESS": lambda f: _extract_congress(f.title, f.snippet),
    "FED_REGISTER": lambda f: _extract_fed_register(f.url, f.snippet),
    "REGULATIONS": lambda f: _extract_regulations(f.url, f.snippet),
    "STATE_LEG": lambda f: _extract_state_leg(f.title, f.snippet, f.url),
    "LEGISCAN": lambda f: _extract_legiscan(f.title, f.snippet, f.url),
}


def _stable_id(*parts: str) -> str:
    normalized = []
    for part in parts:
        if not part:
            continue
        value = re.sub(r"^https?://(www\.)?", "", part.lower())
        value = re.sub(r"[^a-z0-9]+", "-", value).strip("-")
        if value:
            normalized.append(value)
    return "-".join(normalized)[:150]


def _classify_bill_status(status: str) -> tuple[str, bool] | tuple[None, None]:
    text = status.lower()
    if not text:
        return None, None
    if any(term in text for term in ("became law", "signed by", "chaptered", "enacted")):
        return "bill_enacted", False
    if any(term in text for term in ("introduced", "filed", "prefiled")):
        return "bill_introduced", True
    if "referred" in text:
        return "bill_referred", True
    if any(term in text for term in ("passed", "approved", "adopted", "engrossed", "enrolled")):
        return "bill_advanced", True
    return "bill_update", True


def _classify_document_status(status: str) -> tuple[str, bool] | tuple[None, None]:
    text = status.lower()
    if not text:
        return None, None
    if "proposed rule" in text:
        return "proposed_rule", True
    if text == "rule" or "final rule" in text:
        return "final_rule", False
    if "notice" in text:
        return "agency_notice", True
    return "docket_document", True


def _movement_metadata(source_type: str, extracted: dict[str, str]) -> tuple[str | None, bool | None]:
    if source_type in {"CONGRESS", "STATE_LEG", "LEGISCAN"}:
        return _classify_bill_status(str(extracted.get("status", "")))
    if source_type in {"FED_REGISTER", "REGULATIONS"}:
        return _classify_document_status(str(extracted.get("status", "")))
    if source_type == "COURT":
        return "court_action", None
    return None, None


def _signal_id(finding: Finding, extracted: dict[str, str], signal_kind: str | None) -> str:
    if finding.source_type in {"CONGRESS", "STATE_LEG", "LEGISCAN"}:
        return _stable_id(
            finding.source_type,
            str(extracted.get("jurisdiction", "")),
            str(extracted.get("identifier", "")),
            signal_kind or "",
            str(extracted.get("status", "")),
            finding.date,
        )
    return _stable_id(
        finding.source_type,
        finding.url,
        str(extracted.get("identifier", "")),
        finding.date,
        str(extracted.get("status", "")),
    )


def signal_from_finding(finding: Finding) -> dict[str, object]:
    extracted = EXTRACTORS.get(finding.source_type, lambda _f: {})(finding)
    signal_kind, pending = _movement_metadata(finding.source_type, extracted)
    signal: dict[str, object] = {
        "source_tool": SOURCE_TO_TOOL.get(finding.source_type, "unknown"),
        "source_type": finding.source_type,
        "source_system": SOURCE_SYSTEMS.get(
            finding.source_type, finding.source_type.lower()
        ),
        "title": extracted.get("title") or finding.title,
        "url": finding.url,
        "summary": finding.snippet,
        "published_at": finding.date,
    }
    signal["id"] = _signal_id(finding, extracted, signal_kind)
    if finding.citations:
        signal["citations"] = finding.citations
    if signal_kind:
        signal["signal_kind"] = signal_kind
    if pending is not None:
        signal["pending"] = pending
    for key in ("identifier", "status", "congress", "jurisdiction"):
        val = extracted.get(key)
        if val:
            signal[key] = val
    return signal


def emit_signals(
    topic: str,
    preset: str | None,
    scope_label: str,
    results: ResearchResults,
) -> str:
    """Build atomic signals JSON envelope. Dedupes findings by stable signal ID."""
    seen: set[str] = set()
    signals: list[dict[str, object]] = []
    for f in results.findings:
        signal = signal_from_finding(f)
        key = str(signal.get("id", ""))
        if not key or key in seen:
            continue
        seen.add(key)
        signals.append(signal)

    envelope = {
        "schema_version": SCHEMA_VERSION,
        "topic": topic,
        "preset": preset,
        "scope": scope_label,
        "fetched_at": datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
        "counts": {
            "signals": len(signals),
            "by_source_type": _counts_by_source(signals),
        },
        "signals": signals,
    }
    return json.dumps(envelope, indent=2, ensure_ascii=False)


def _counts_by_source(signals: list[dict[str, object]]) -> dict[str, int]:
    out: dict[str, int] = {}
    for s in signals:
        out[s["source_type"]] = out.get(s["source_type"], 0) + 1
    return out
