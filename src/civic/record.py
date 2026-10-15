"""The normalized record every adapter emits, and its movement metadata."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .http import Call, canonical_json, sha256

BILL_KINDS = frozenset({"bill"})
DOCUMENT_KINDS = frozenset({"regulatory"})


@dataclass
class Record:
    """One item from one source, with the trace of the call that produced it.

    `kind`: bill | regulatory | opinion | paper | web | statistic.
    `movement` and `pending` classify bill and rule status text when present.
    `details` holds source-specific fields (agency, congress, court, authors).
    """

    source: str
    kind: str
    title: str
    url: str
    summary: str = ""
    published_at: str = ""
    jurisdiction: str = ""
    identifier: str = ""
    status: str = ""
    official_url: str = ""
    citations: int | None = None
    details: dict[str, object] = field(default_factory=dict)
    provenance: dict[str, object] = field(default_factory=dict)
    id: str = ""

    @property
    def movement(self) -> tuple[str, bool] | tuple[None, None]:
        if self.kind in BILL_KINDS:
            return classify_bill_status(self.status)
        if self.kind in DOCUMENT_KINDS:
            return classify_document_status(self.status)
        if self.kind == "opinion":
            return "court_action", None  # type: ignore[return-value]
        return None, None

    def to_dict(self) -> dict[str, object]:
        movement, pending = self.movement
        out: dict[str, object] = {
            "id": self.id,
            "source": self.source,
            "kind": self.kind,
            "title": self.title,
            "url": self.url,
            "summary": self.summary,
            "published_at": self.published_at,
        }
        optional = {
            "jurisdiction": self.jurisdiction,
            "identifier": self.identifier,
            "status": self.status,
            "movement": movement,
            "pending": pending,
            "official_url": self.official_url,
            "citations": self.citations,
            "details": self.details,
        }
        out.update({key: value for key, value in optional.items() if value not in ("", None, {})})
        out["provenance"] = self.provenance
        return out


def trace(record: Record, *, adapter: str, version: str, terms: str, call: Call, item: object) -> Record:
    """Attach provenance from the producing call and hash the raw item."""
    record.provenance = {
        "adapter": adapter,
        "adapter_version": version,
        "method": call.method,
        "endpoint": call.endpoint,
        "params": call.params,
        "http_status": call.http_status,
        "retrieved_at": call.retrieved_at,
        "terms": terms,
        "item_sha256": sha256(canonical_json(item)),
    }
    if call.raw_path:
        record.provenance["raw_path"] = call.raw_path
    record.id = record_id(record)
    return record


def record_id(record: Record) -> str:
    """Stable ID. Bills key on the movement, so a later action is a new record."""
    if record.kind in BILL_KINDS:
        movement, _ = record.movement
        parts = [record.source, record.jurisdiction, record.identifier, movement or "",
                 record.status, record.published_at]
    else:
        parts = [record.source, record.url, record.identifier, record.published_at, record.status]
    return stable_id(*parts)


def stable_id(*parts: str) -> str:
    normalized = []
    for part in parts:
        value = re.sub(r"^https?://(www\.)?", "", (part or "").lower())
        value = re.sub(r"[^a-z0-9]+", "-", value).strip("-")
        if value:
            normalized.append(value)
    return "-".join(normalized)[:150]


_ENACTED = (
    "became law", "became public law", "signed by", "approved by the governor",
    "approved by governor", "chaptered", "chapter no", "enacted", "effective", "public act",
)


def classify_bill_status(status: str) -> tuple[str, bool] | tuple[None, None]:
    text = status.lower()
    if not text:
        return None, None
    if "veto" in text:
        return "bill_vetoed", False
    if re.match(r"^act\s+\d", text) or any(term in text for term in _ENACTED):
        return "bill_enacted", False
    if any(term in text for term in ("introduced", "filed", "prefiled")):
        return "bill_introduced", True
    if "referred" in text:
        return "bill_referred", True
    if any(term in text for term in ("passed", "approved", "adopted", "engrossed", "enrolled")):
        return "bill_advanced", True
    return "bill_update", True


def classify_document_status(status: str) -> tuple[str, bool] | tuple[None, None]:
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
