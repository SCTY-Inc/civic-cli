"""HTTP transport that records every call it makes.

Each call yields a `Call` trace: method, endpoint, query parameters, HTTP
status, retrieval time, and the SHA-256 of the response body. Secrets never
enter a trace: credentials travel in headers, and the few services that only
accept a key as a query parameter name it in `secret_params`, which the trace
omits. With a raw directory, each response body is saved for audit.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

import httpx

TIMEOUT = 30
MAX_ATTEMPTS = 3
RETRY_STATUS = frozenset({429, 500, 502, 503, 504})
USER_AGENT = "civic-cli (+https://github.com/SCTY-Inc/civic-cli)"


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha256(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def canonical_json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


@dataclass
class Call:
    source: str
    method: str
    endpoint: str
    params: dict[str, object]
    http_status: int | None = None
    retrieved_at: str = ""
    body_sha256: str = ""
    raw_path: str = ""

    def to_dict(self) -> dict[str, object]:
        return {key: value for key, value in asdict(self).items() if value not in ("", None)}


class SourceError(Exception):
    """A source call failed; `call` holds the trace when a request was made."""

    def __init__(self, message: str, call: Call | None = None):
        super().__init__(message)
        self.call = call


class Transport:
    def __init__(
        self,
        *,
        raw_dir: Path | None = None,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.raw_dir = raw_dir
        self.client = client or httpx.Client(timeout=TIMEOUT, follow_redirects=True)
        self.sleep = sleep
        self._lock = threading.Lock()
        self.calls: list[Call] = []

    def close(self) -> None:
        self.client.close()

    def request(
        self,
        source: str,
        method: str,
        url: str,
        *,
        params: Mapping[str, object] | None = None,
        json_body: Mapping[str, object] | None = None,
        headers: Mapping[str, str] | None = None,
        secret_params: frozenset[str] = frozenset(),
    ) -> tuple[object, Call]:
        """Return (parsed JSON, trace). Raise SourceError on transport or HTTP failure."""
        traced = {k: v for k, v in {**(params or {}), **(json_body or {})}.items() if k not in secret_params}
        call = Call(source=source, method=method, endpoint=url, params=traced)
        with self._lock:
            self.calls.append(call)
            number = len(self.calls)
        send_headers = {"User-Agent": USER_AGENT, **(headers or {})}
        response: httpx.Response | None = None
        for attempt in range(MAX_ATTEMPTS):
            call.retrieved_at = utc_now()
            try:
                response = self.client.request(
                    method, url, params=params, json=json_body, headers=send_headers
                )
            except (httpx.TimeoutException, httpx.TransportError) as error:
                if attempt == MAX_ATTEMPTS - 1:
                    raise SourceError(f"{type(error).__name__}: {error}", call) from error
                self.sleep(2**attempt)
                continue
            if response.status_code in RETRY_STATUS and attempt < MAX_ATTEMPTS - 1:
                self.sleep(2**attempt)
                continue
            break

        assert response is not None
        call.http_status = response.status_code
        call.body_sha256 = sha256(response.content)
        call.raw_path = self._save(number, call, response)
        if response.is_error:
            raise SourceError(f"HTTP {response.status_code} {response.reason_phrase}", call)
        try:
            return response.json(), call
        except ValueError as error:
            raise SourceError(f"response was not JSON: {error}", call) from error

    def _save(self, number: int, call: Call, response: httpx.Response) -> str:
        if self.raw_dir is None:
            return ""
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        path = self.raw_dir / f"{number:03d}-{call.source}.json"
        try:
            body: object = response.json()
        except ValueError:
            body = response.text
        record = {"call": call.to_dict(), "body": body}
        path.write_text(json.dumps(record, indent=2, ensure_ascii=False))
        return str(path)
