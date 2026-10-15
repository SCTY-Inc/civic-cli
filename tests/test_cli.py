import json
from pathlib import Path

import pytest

from civic import ADAPTERS, cli
from civic import search as real_search

from .conftest import KEYS


@pytest.fixture
def offline(monkeypatch, transport):
    monkeypatch.setattr(cli, "load_dotenv", lambda: None)
    monkeypatch.setattr(cli, "search", lambda topic, **kw: real_search(
        topic, env=KEYS, transport=transport(), **kw))


def test_search_json(offline, capsys):
    code = cli.main(["search", "caregiver", "--sources", "federal_register,courtlistener",
                     "--since", "2025-01-01", "--limit", "1"])
    out = json.loads(capsys.readouterr().out)
    assert code == 0
    assert out["counts"]["by_source"] == {"federal_register": 1, "courtlistener": 1}
    assert out["query"]["since"] == "2025-01-01"


def test_search_jsonl(offline, capsys):
    cli.main(["search", "caregiver", "--sources", "federal_register", "-f", "jsonl"])
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert lines[0]["type"] == "run" and "records" not in lines[0]
    assert {line["type"] for line in lines[1:]} == {"record"}
    assert all(line["provenance"]["run_id"] == lines[0]["run_id"] for line in lines[1:])


def test_bad_scope_is_a_usage_error(offline):
    with pytest.raises(SystemExit) as caught:
        cli.main(["search", "x", "--scope", "state:ZZ"])
    assert caught.value.code == 2


def test_doctor_fails_only_for_named_missing_sources(monkeypatch):
    monkeypatch.setattr(cli, "load_dotenv", lambda: None)
    for key in KEYS:
        monkeypatch.delenv(key, raising=False)
    assert cli.main(["doctor", "--scope", "federal"]) == 0
    assert cli.main(["doctor", "--sources", "exa"]) == 1


def test_sources_doc_credits_every_adapter():
    text = (Path(__file__).parent.parent / "SOURCES.md").read_text()
    for adapter in ADAPTERS.values():
        assert f"`{adapter.name}`" in text
        assert adapter.docs in text
