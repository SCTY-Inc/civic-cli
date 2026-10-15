"""Offline harness: route source hosts to recorded fixtures via httpx.MockTransport."""

import json
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from civic.http import Transport

FIXTURES = Path(__file__).parent / "fixtures"

KEYS = {
    "GOVINFO_API_KEY": "gi-secret",
    "REGULATIONS_GOV_API_KEY": "rg-secret",
    "OPENSTATES_API_KEY": "os-secret",
    "LEGISCAN_API_KEY": "ls-secret",
    "CENSUS_API_KEY": "cn-secret",
    "EXA_API_KEY": "ex-secret",
}


def fixture(name: str):
    return json.loads((FIXTURES / name).read_text())


HOST_FIXTURES = {
    "api.govinfo.gov": "govinfo_bills.json",
    "www.federalregister.gov": "federal_register.json",
    "api.regulations.gov": "regulations.json",
    "www.courtlistener.com": "courtlistener.json",
    "api.legiscan.com": "legiscan.json",
    "api.census.gov": "census.json",
    "api.semanticscholar.org": "semantic_scholar.json",
    "api.exa.ai": "exa.json",
}


class Recorder:
    """Answers requests from fixtures; `overrides[host]` may return a Response or JSON."""

    def __init__(self):
        self.requests: list[httpx.Request] = []
        self.overrides: dict[str, Callable[[httpx.Request], object]] = {}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        host = request.url.host
        if host in self.overrides:
            answer = self.overrides[host](request)
            return answer if isinstance(answer, httpx.Response) else httpx.Response(200, json=answer)
        if host == "v3.openstates.org":
            page = request.url.params.get("page", "1")
            return httpx.Response(200, json=fixture(f"openstates_page{page}.json"))
        return httpx.Response(200, json=fixture(HOST_FIXTURES[host]))

    def to(self, host: str) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.host == host]


@pytest.fixture
def recorder():
    return Recorder()


@pytest.fixture
def transport(recorder):
    def make(raw_dir: Path | None = None) -> Transport:
        return Transport(raw_dir=raw_dir, client=httpx.Client(transport=httpx.MockTransport(recorder)),
                         sleep=lambda _s: None)
    return make
