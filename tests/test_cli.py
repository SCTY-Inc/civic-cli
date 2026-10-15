"""Tests for CLI scope parsing and validation."""

import json
from types import SimpleNamespace

import cli
import pytest
from agents import ResearchOutput
from cli import check_env, load_topics, parse_compare, parse_scope, run_pipeline
from tools.declarations import get_tool_declarations
from tools.models import Finding, ResearchResults


class TestParseScope:
    def test_federal(self):
        assert parse_scope("federal") == {"type": "federal", "states": []}

    def test_all(self):
        assert parse_scope("all") == {"type": "all", "states": []}

    def test_policy(self):
        assert parse_scope("policy") == {"type": "policy", "states": []}

    def test_single_state(self):
        assert parse_scope("state:CA") == {"type": "state", "states": ["CA"]}

    def test_multiple_states(self):
        result = parse_scope("state:CA,NY,TX")
        assert result["type"] == "state"
        assert set(result["states"]) == {"CA", "NY", "TX"}

    def test_lowercase_states(self):
        result = parse_scope("state:ca,ny")
        assert result["states"] == ["CA", "NY"]

    def test_invalid_state(self):
        with pytest.raises(ValueError, match="Invalid state codes"):
            parse_scope("state:ZZ")

    def test_invalid_scope(self):
        with pytest.raises(ValueError, match="Invalid scope"):
            parse_scope("bogus")

    def test_dc_and_pr(self):
        assert parse_scope("state:DC")["states"] == ["DC"]
        assert parse_scope("state:PR")["states"] == ["PR"]


class TestParseCompare:
    def test_federal_and_states(self):
        assert parse_compare("federal,CA,NY") == ["federal", "CA", "NY"]

    def test_special_targets(self):
        assert parse_compare("federal,news,policy") == ["federal", "news", "policy"]

    def test_invalid_target(self):
        with pytest.raises(ValueError, match="Invalid compare target"):
            parse_compare("federal,INVALID")

    def test_states_only(self):
        assert parse_compare("CA,NY,TX") == ["CA", "NY", "TX"]


class TestCheckEnv:
    def test_all_scope_requires_google_and_exa(self, monkeypatch):
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        monkeypatch.delenv("EXA_API_KEY", raising=False)
        missing = check_env({"type": "all", "states": []})
        assert missing == ["GOOGLE_API_KEY", "EXA_API_KEY"]

    def test_federal_does_not_require_optional_keys(self, monkeypatch):
        monkeypatch.setenv("GOOGLE_API_KEY", "x")
        monkeypatch.setenv("EXA_API_KEY", "x")
        monkeypatch.delenv("GOVINFO_API_KEY", raising=False)
        missing = check_env({"type": "federal", "states": []})
        assert missing == []

    def test_policy_compare_only_requires_google(self, monkeypatch):
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        monkeypatch.delenv("EXA_API_KEY", raising=False)
        missing = check_env({"type": "all", "states": []}, ["policy"])
        assert missing == ["GOOGLE_API_KEY"]

    def test_all_keys_present(self, monkeypatch):
        for key in ["GOOGLE_API_KEY", "EXA_API_KEY"]:
            monkeypatch.setenv(key, "x")
        missing = check_env({"type": "all", "states": []})
        assert missing == []

    def test_direct_signals_do_not_require_a_model_key(self, monkeypatch):
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        monkeypatch.setenv("EXA_API_KEY", "x")

        assert check_env({"type": "federal", "states": []}, require_model=False) == []


class TestTopics:
    def test_load_topics_prefers_current_directory(self, monkeypatch, tmp_path):
        (tmp_path / "topics.toml").write_text('[topics.demo]\ntopic = "Demo"\n')
        monkeypatch.chdir(tmp_path)
        topics = load_topics()
        assert topics["demo"]["topic"] == "Demo"

    def test_declarations_skip_optional_tools_without_keys(self, monkeypatch):
        monkeypatch.setenv("EXA_API_KEY", "x")
        monkeypatch.delenv("GOVINFO_API_KEY", raising=False)
        monkeypatch.delenv("OPENSTATES_API_KEY", raising=False)
        monkeypatch.delenv("LEGISCAN_API_KEY", raising=False)
        monkeypatch.delenv("REGULATIONS_GOV_API_KEY", raising=False)
        names = [d.name for d in get_tool_declarations({"type": "all", "states": []})]
        assert "web_search" in names
        assert "congress_search" not in names
        assert "regulations_search" not in names
        assert "state_legislation_search" not in names

    def test_state_scope_includes_state_legislation_with_legiscan_only(self, monkeypatch):
        monkeypatch.setenv("EXA_API_KEY", "x")
        monkeypatch.delenv("OPENSTATES_API_KEY", raising=False)
        monkeypatch.setenv("LEGISCAN_API_KEY", "x")
        names = [d.name for d in get_tool_declarations({"type": "state", "states": ["CA"]})]
        assert "state_legislation_search" in names

    def test_all_scope_skips_state_legislation_with_legiscan_only(self, monkeypatch):
        monkeypatch.setenv("EXA_API_KEY", "x")
        monkeypatch.delenv("OPENSTATES_API_KEY", raising=False)
        monkeypatch.setenv("LEGISCAN_API_KEY", "x")
        names = [d.name for d in get_tool_declarations({"type": "all", "states": []})]
        assert "state_legislation_search" not in names


class TestSignals:
    def test_direct_mode_uses_source_fanout_without_gemini(self, monkeypatch, capsys):
        results = ResearchResults(
            findings=[
                Finding(
                    title="Federal caregiver bill",
                    snippet="Referred to committee",
                    url="https://www.congress.gov/bill/119th-congress/house-bill/1",
                    source_type="CONGRESS",
                )
            ],
            tool_usage={"congress_search": 1},
        )
        seen = {}

        def direct(topic, *, scope, since=None, verbose=False):
            seen.update(topic=topic, scope=scope, since=since, verbose=verbose)
            return ResearchOutput(text="", results=results, scope_label="federal")

        monkeypatch.setattr(cli, "direct_research", direct)
        monkeypatch.setattr(cli, "check_env", lambda scope, compare=None, require_model=True: [])
        monkeypatch.setattr(cli, "set_results_limit", lambda _limit: None)
        args = SimpleNamespace(
            preset=None,
            topic="family caregiver policy",
            scope="federal",
            compare=None,
            questions=None,
            limit=1,
            since="2026-01-01",
            verbose=False,
            direct=True,
        )

        assert cli.cmd_signals(args) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["counts"]["signals"] == 1
        assert seen == {
            "topic": "family caregiver policy",
            "scope": {"type": "federal", "states": []},
            "since": "2026-01-01",
            "verbose": False,
        }


class TestRunPipeline:
    def test_json_mode_does_not_open_status_spinner(self, monkeypatch, capsys):
        class StubConsole:
            def status(self, *args, **kwargs):
                raise AssertionError("status spinner should be skipped in json mode")

            def print(self, *args, **kwargs):
                pass

            def print_exception(self, *args, **kwargs):
                raise AssertionError("unexpected exception")

        results = ResearchResults(
            findings=[Finding(title="Title", snippet="Snippet", url="http://x", source_type="WEB")],
            tool_usage={"web_search": 1},
        )

        monkeypatch.setattr(cli, "console", StubConsole())
        monkeypatch.setattr(cli, "err_console", StubConsole())
        monkeypatch.setattr(cli, "check_env", lambda scope, compare=None: [])
        monkeypatch.setattr(
            cli,
            "research",
            lambda topic, questions, scope, verbose, since=None: ResearchOutput(
                text="Research text",
                results=results,
                scope_label="federal + state",
            ),
        )

        exit_code = run_pipeline("family caregiver policy", output_format="json")
        payload = json.loads(capsys.readouterr().out)

        assert exit_code == 0
        assert payload["topic"] == "family caregiver policy"
        assert payload["findings"][0]["title"] == "Title"
