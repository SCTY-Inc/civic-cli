"""Fan one query out to the selected adapters and return one traceable envelope."""

from __future__ import annotations

import os
import re
import secrets
from collections.abc import Iterable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from . import __version__
from .adapters import ADAPTERS, Adapter, Query
from .http import SourceError, Transport, utc_now
from .record import Record
from .scope import Scope, parse_scope

SCHEMA = "civic.search.v1"
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@dataclass
class Envelope:
    run_id: str
    query: dict[str, object]
    started_at: str
    finished_at: str = ""
    sources: list[dict[str, object]] = field(default_factory=list)
    errors: list[dict[str, object]] = field(default_factory=list)
    records: list[Record] = field(default_factory=list)

    def header(self) -> dict[str, object]:
        by_source: dict[str, int] = {}
        for record in self.records:
            by_source[record.source] = by_source.get(record.source, 0) + 1
        return {
            "schema": SCHEMA,
            "run_id": self.run_id,
            "civic_version": __version__,
            "query": self.query,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "counts": {"records": len(self.records), "by_source": by_source},
            "sources": self.sources,
            "errors": self.errors,
        }

    def to_dict(self) -> dict[str, object]:
        return {**self.header(), "records": [record.to_dict() for record in self.records]}

    @property
    def ok(self) -> bool:
        """True when at least one selected source answered."""
        return any(s["status"] in ("ok", "partial") for s in self.sources)


def new_run_id() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + secrets.token_hex(3)


def select(scope: Scope, names: Iterable[str] | None) -> list[Adapter]:
    if names is None:
        return [a for a in ADAPTERS.values() if scope.kind in a.scopes]
    unknown = [n for n in names if n not in ADAPTERS]
    if unknown:
        raise ValueError(f"Unknown sources: {', '.join(unknown)}. Known: {', '.join(ADAPTERS)}")
    return [ADAPTERS[n] for n in dict.fromkeys(names)]


def search(
    topic: str,
    *,
    scope: str | Scope = "all",
    since: str | None = None,
    sources: Iterable[str] | None = None,
    limit: int = 25,
    raw_dir: str | Path | None = None,
    env: Mapping[str, str] | None = None,
    transport: Transport | None = None,
) -> Envelope:
    """Search every selected source once, in parallel. Never raises for a source failure.

    `sources=None` selects every adapter for the scope and skips those without
    credentials. Naming a source makes a missing credential an error.
    """
    topic = topic.strip()
    if not topic:
        raise ValueError("topic is required")
    if since is not None and not _DATE.match(since):
        raise ValueError("since must be YYYY-MM-DD")
    if limit < 1:
        raise ValueError("limit must be positive")
    parsed = parse_scope(scope)
    explicit = sources is not None
    names = list(sources) if explicit else None
    adapters = select(parsed, names)
    env = os.environ if env is None else env

    run_id = new_run_id()
    envelope = Envelope(
        run_id=run_id,
        query={"topic": topic, "scope": str(parsed), "since": since, "limit": limit,
               "sources": [a.name for a in adapters]},
        started_at=utc_now(),
    )
    query = Query(topic=topic, scope=parsed, since=since, limit=limit)
    raw_path = Path(raw_dir) / run_id if raw_dir else None
    http = transport or Transport(raw_dir=raw_path)

    runnable = []
    summaries: dict[str, dict[str, object]] = {}
    for adapter in adapters:
        summaries[adapter.name] = {"name": adapter.name, "version": adapter.version,
                                   "status": "ok", "records": 0}
        if adapter.available(env):
            runnable.append(adapter)
            continue
        reason = f"{adapter.env} not set"
        summaries[adapter.name].update(status="error" if explicit else "skipped", reason=reason)
        if explicit:
            envelope.errors.append({"source": adapter.name, "error": reason})

    def run(adapter: Adapter) -> tuple[Adapter, list[Record], list[SourceError]]:
        try:
            records, errors = adapter.search(query, http, env)
            return adapter, records, errors
        except SourceError as error:
            return adapter, [], [error]
        except Exception as error:  # noqa: BLE001 - an adapter bug must not hide other sources
            return adapter, [], [SourceError(f"{type(error).__name__}: {error}")]

    try:
        with ThreadPoolExecutor(max_workers=max(1, len(runnable))) as pool:
            results = list(pool.map(run, runnable))
    finally:
        if transport is None:
            http.close()

    seen: set[str] = set()
    for adapter, records, errors in results:
        summary = summaries[adapter.name]
        for record in records:
            record.provenance["run_id"] = run_id
            if record.id in seen:
                continue
            seen.add(record.id)
            envelope.records.append(record)
            summary["records"] = int(summary["records"]) + 1
        if errors:
            summary["status"] = "partial" if records else "error"
        for error in errors:
            entry: dict[str, object] = {"source": adapter.name, "error": str(error)}
            if error.call:
                entry.update(endpoint=error.call.endpoint, http_status=error.call.http_status)
            envelope.errors.append(entry)
        summary["calls"] = [c.to_dict() for c in http.calls if c.source == adapter.name]

    envelope.sources = list(summaries.values())
    envelope.finished_at = utc_now()
    return envelope
