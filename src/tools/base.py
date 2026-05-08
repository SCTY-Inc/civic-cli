import hashlib
import json
import sqlite3
import time
from abc import ABC, abstractmethod
from collections.abc import Mapping
from contextlib import closing
from pathlib import Path

import httpx

from .models import Finding, ToolResult

JsonValue = None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]

RESULTS_LIMIT = 25  # module-level default; override via set_results_limit()
TIMEOUT = 30
MAX_RETRIES = 3
CACHE_TTL = 86400  # 24 hours
CACHE_DIR = Path.home() / ".cache" / "civic"
_DB_PATH = CACHE_DIR / "cache.db"


def set_results_limit(n: int) -> None:
    global RESULTS_LIMIT
    if n is not None and n > 0:
        RESULTS_LIMIT = int(n)


def _get_cache_db() -> sqlite3.Connection:
    """Open cache DB with WAL mode; creates file and table on first call."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(_DB_PATH))
    db.execute("PRAGMA journal_mode=WAL")
    db.execute(
        "CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, value TEXT, ts REAL)"
    )
    return db


_CACHE_SKIP_PARAMS = frozenset({"api_key", "key"})


def _cache_key(url: str, params: Mapping[str, object] | None) -> str:
    clean = {k: v for k, v in (params or {}).items() if k not in _CACHE_SKIP_PARAMS}
    raw = json.dumps({"url": url, "params": clean}, sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()


def _read_cached_json(key: str) -> JsonValue | None:
    try:
        with closing(_get_cache_db()) as db:
            row = db.execute(
                "SELECT value, ts FROM cache WHERE key = ?",
                (key,),
            ).fetchone()
    except sqlite3.Error:
        return None

    if not row or (time.time() - row[1]) >= CACHE_TTL:
        return None
    return json.loads(row[0])


def _write_cached_json(key: str, value: JsonValue) -> None:
    """Best-effort write; sqlite errors must not propagate to callers."""
    try:
        with closing(_get_cache_db()) as db:
            db.execute(
                "INSERT OR REPLACE INTO cache (key, value, ts) VALUES (?, ?, ?)",
                (key, json.dumps(value), time.time()),
            )
            db.commit()
    except sqlite3.Error:
        return


def get_cache_stats() -> dict[str, object] | None:
    if not _DB_PATH.exists():
        return None
    with closing(_get_cache_db()) as db:
        count = db.execute("SELECT COUNT(*) FROM cache").fetchone()[0]
        oldest = db.execute("SELECT MIN(ts) FROM cache").fetchone()[0]
        newest = db.execute("SELECT MAX(ts) FROM cache").fetchone()[0]
    return {
        "entries": count,
        "size_kb": _DB_PATH.stat().st_size / 1024,
        "oldest": oldest,
        "newest": newest,
    }


def clear_cache() -> bool:
    if _DB_PATH.exists():
        _DB_PATH.unlink()
        return True
    return False


class BaseTool(ABC):

    SOURCE_TYPE: str = "UNKNOWN"

    def __init__(self):
        self._http_client: httpx.Client | None = None

    def __del__(self):
        if self._http_client is not None:
            self._http_client.close()

    @abstractmethod
    def execute(self, **kwargs) -> ToolResult: ...

    def _ok(self, findings: list[Finding]) -> ToolResult:
        return ToolResult(findings=findings)

    def _error(self, message: str) -> ToolResult:
        """Never fabricates findings; all errors are explicit."""
        return ToolResult(errors=[message])

    def _missing_api_key(self, name: str) -> ToolResult:
        return self._error(f"{name} not set")

    def _http_error(self, service: str, error: httpx.HTTPError) -> ToolResult:
        if isinstance(error, httpx.HTTPStatusError):
            return self._error(
                f"{service} API error ({error.response.status_code}): {error.response.reason_phrase}"
            )
        return self._error(f"{service} API error: {error}")

    def _parse_error(self, service: str, error: Exception) -> ToolResult:
        return self._error(f"Failed to parse {service} results: {error}")

    def _fetch_json(
        self,
        url: str,
        params: Mapping[str, object] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> JsonValue:
        key = _cache_key(url, params)
        cached = _read_cached_json(key)
        if cached is not None:
            return cached

        if self._http_client is None:
            self._http_client = httpx.Client(timeout=TIMEOUT)

        last_error: httpx.HTTPError | None = None
        for attempt in range(MAX_RETRIES):
            try:
                response = self._http_client.get(url, params=params, headers=headers)
                response.raise_for_status()
                data = response.json()
                _write_cached_json(key, data)
                return data
            except httpx.HTTPStatusError as error:
                if error.response.status_code in (429, 500, 502, 503, 504):
                    last_error = error
                    if attempt < MAX_RETRIES - 1:
                        time.sleep(2**attempt)
                    continue
                raise
            except (httpx.TimeoutException, httpx.ConnectError) as error:
                last_error = error
                if attempt < MAX_RETRIES - 1:
                    time.sleep(2**attempt)

        raise last_error or httpx.ConnectError("Max retries exceeded")
