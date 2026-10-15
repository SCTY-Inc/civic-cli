"""Tests for research-scope helpers."""

from agents import _scope_context


def test_scope_context_for_news():
    prompt, label = _scope_context({"type": "news", "states": []})
    assert "current news" in prompt.lower()
    assert label == "news"


def test_scope_context_for_policy():
    prompt, label = _scope_context({"type": "policy", "states": []})
    assert "legislation" in prompt.lower()
    assert label == "policy"


def test_direct_research_collects_errors_from_every_adapter(monkeypatch):
    from agents import direct_research
    from tools.models import Finding, ToolResult

    class FakeRegistry:
        def __init__(self, scope):
            pass

        def run(self, name, args):
            if name == "congress_search":
                return ToolResult(findings=[Finding("T", "S", "https://x.gov/b", "2026-01-01", "CONGRESS")])
            return ToolResult(errors=[f"{name} failed"])

    monkeypatch.setattr("agents.ToolRegistry", FakeRegistry)
    monkeypatch.setattr(
        "agents.get_available_tool_names",
        lambda scope: ["congress_search", "state_legislation_search", "court_search"],
    )
    out = direct_research("topic", scope={"type": "policy", "states": []})

    assert [f.source_type for f in out.results.findings] == ["CONGRESS"]
    assert sorted(out.results.errors, key=lambda e: e["source_tool"]) == [
        {"source_tool": "court_search", "error": "court_search failed"},
        {"source_tool": "state_legislation_search", "error": "state_legislation_search failed"},
    ]
