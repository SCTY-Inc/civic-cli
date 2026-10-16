import json

import httpx
import pytest

from civic import SCHEMA, search

from .conftest import KEYS

PROVENANCE = {"adapter", "adapter_version", "method", "endpoint", "params", "http_status",
              "retrieved_at", "terms", "item_sha256", "run_id"}


def test_policy_envelope_is_traceable(transport):
    envelope = search("paid family caregiver", scope="policy", since="2025-01-01",
                      env=KEYS, transport=transport())
    out = envelope.to_dict()
    assert out["schema"] == SCHEMA
    assert out["query"]["sources"] == ["govinfo", "federal_register", "regulations",
                                       "courtlistener", "openstates", "legiscan"]
    assert out["errors"] == []
    assert set(out["counts"]["by_source"]) == set(out["query"]["sources"])
    assert out["counts"]["records"] == len(out["records"])
    for record in out["records"]:
        assert PROVENANCE <= set(record["provenance"])
        assert record["provenance"]["run_id"] == out["run_id"]
        assert record["provenance"]["http_status"] == 200
        assert record["provenance"]["item_sha256"].startswith("sha256:")
    for source in out["sources"]:
        assert source["status"] == "ok"
        assert source["calls"] and all(c["http_status"] == 200 for c in source["calls"])
    assert not any(secret in json.dumps(out) for secret in KEYS.values())


def test_scope_selects_and_missing_keys_skip(transport):
    envelope = search("caregiver", scope="federal", env={}, transport=transport())
    status = {s["name"]: s["status"] for s in envelope.sources}
    assert status == {"govinfo": "skipped", "federal_register": "ok", "regulations": "skipped",
                      "courtlistener": "ok", "census": "ok", "semantic_scholar": "ok",
                      "exa": "skipped"}
    assert envelope.errors == []


def test_named_source_without_key_is_an_error(transport):
    envelope = search("caregiver", sources=["exa", "federal_register"], env={},
                      transport=transport())
    assert envelope.errors == [{"source": "exa", "error": "EXA_API_KEY not set"}]
    assert envelope.ok


def test_one_failing_source_does_not_hide_others(recorder, transport):
    recorder.overrides["www.courtlistener.com"] = lambda _r: httpx.Response(503)
    envelope = search("caregiver", sources=["courtlistener", "federal_register"], env=KEYS,
                      transport=transport())
    assert envelope.errors == [{
        "source": "courtlistener", "error": "HTTP 503 Service Unavailable",
        "endpoint": "https://www.courtlistener.com/api/rest/v4/search/", "http_status": 503,
    }]
    assert len(recorder.to("www.courtlistener.com")) == 3  # retried
    assert {r.source for r in envelope.records} == {"federal_register"}


def test_adapter_bug_is_reported_not_raised(recorder, transport):
    recorder.overrides["api.exa.ai"] = lambda _r: {"results": "not-a-list"}
    recorder.overrides["api.semanticscholar.org"] = lambda _r: [1, 2]
    envelope = search("x", sources=["semantic_scholar"], env=KEYS, transport=transport())
    assert envelope.errors[0]["error"] == "unexpected response shape: list"
    assert not envelope.ok


def test_all_failed_partial_status(recorder, transport):
    recorder.overrides["v3.openstates.org"] = lambda r: (
        {"results": [{"identifier": "SB 1", "title": "T"}], "pagination": {"max_page": 2}}
        if r.url.params["page"] == "1" else httpx.Response(500)
    )
    envelope = search("x", sources=["openstates"], limit=40, env=KEYS, transport=transport())
    assert envelope.sources[0]["status"] == "partial"
    assert envelope.sources[0]["records"] == 1


def test_records_dedupe_by_id(recorder, transport):
    doc = {"title": "T", "html_url": "https://www.federalregister.gov/d/1",
           "publication_date": "2025-01-01", "type": "Notice"}
    recorder.overrides["www.federalregister.gov"] = lambda _r: {"results": [doc, doc]}
    envelope = search("x", sources=["federal_register"], env=KEYS, transport=transport())
    assert len(envelope.records) == 1


def test_raw_dir_saves_responses_without_keys(tmp_path, transport):
    envelope = search("caregiver", sources=["legiscan", "census"], env=KEYS,
                      raw_dir=tmp_path, transport=transport(tmp_path / "run"))
    files = sorted((tmp_path / "run").iterdir())
    assert len(files) == 4  # legiscan getSearch + 2 getBill, census
    saved = [json.loads(f.read_text()) for f in files]
    assert {s["call"]["source"] for s in saved} == {"legiscan", "census"}
    assert not any(secret in f.read_text() for f in files for secret in KEYS.values())
    raw_paths = {r.provenance["raw_path"] for r in envelope.records}
    assert raw_paths <= {str(f) for f in files}
    assert len(raw_paths) == 2  # getSearch and census; getBill is traced in sources[].calls


def test_raw_dir_is_namespaced_by_run_id(tmp_path, monkeypatch, recorder):
    import sys

    module = sys.modules["civic.search"]

    def fake_transport(raw_dir=None):
        from civic.http import Transport
        return Transport(raw_dir=raw_dir, client=httpx.Client(transport=httpx.MockTransport(recorder)))

    monkeypatch.setattr(module, "Transport", fake_transport)
    envelope = search("x", sources=["federal_register"], env=KEYS, raw_dir=tmp_path)
    assert (tmp_path / envelope.run_id).is_dir()


@pytest.mark.parametrize(("kwargs", "message"), [
    ({"topic": " "}, "topic is required"),
    ({"topic": "x", "since": "2025/01/01"}, "since must be YYYY-MM-DD"),
    ({"topic": "x", "limit": 0}, "limit must be positive"),
    ({"topic": "x", "sources": ["nope"]}, "Unknown sources: nope"),
    ({"topic": "x", "scope": "state:ZZ"}, "Invalid state codes: ZZ"),
])
def test_rejects_bad_input(kwargs, message):
    with pytest.raises(ValueError, match=message):
        search(**kwargs)
