"""Tests for data models and confidence scoring."""

from tools.models import Finding, ResearchResults


class TestFinding:
    def test_defaults(self):
        f = Finding(title="T", snippet="S", url="U")
        assert f.date == ""
        assert f.source_type == ""
        assert f.citations == 0

    def test_to_dict(self):
        f = Finding(title="T", snippet="S", url="U", date="2025", source_type="WEB", citations=5)
        d = f.to_dict()
        assert d == {
            "title": "T", "snippet": "S", "url": "U",
            "date": "2025", "source_type": "WEB", "citations": 5,
            "jurisdiction": "", "official_url": "",
        }


class TestResearchResults:
    def test_add(self):
        r = ResearchResults()
        f = Finding(title="T", snippet="S", url="U", source_type="WEB")
        r.add(f, "web_search")
        assert len(r.findings) == 1
        assert r.tool_usage["web_search"] == 1

    def test_add_increments(self):
        r = ResearchResults()
        for _ in range(3):
            r.add(Finding(title="T", snippet="S", url="U", source_type="WEB"), "web_search")
        assert r.tool_usage["web_search"] == 3

    def test_confidence_no_findings(self):
        r = ResearchResults()
        level, _ = r.confidence_score()
        assert level == "LOW"

    def test_confidence_high(self):
        r = ResearchResults()
        sources = ["WEB", "ACADEMIC", "CONGRESS", "FED_REGISTER", "COURT"]
        for s in sources:
            r.add(Finding(title="T", snippet="S", url="U", date="2026", source_type=s, citations=10), s)
        level, _ = r.confidence_score()
        assert level == "HIGH"

    def test_confidence_low_single_source(self):
        r = ResearchResults()
        r.add(Finding(title="T", snippet="S", url="U", date="2010", source_type="WEB"), "web_search")
        level, _ = r.confidence_score()
        assert level == "LOW"

    def test_confidence_recent_single_source_stays_low(self):
        r = ResearchResults()
        r.add(Finding(title="T", snippet="S", url="U", date="2026", source_type="WEB"), "web_search")
        level, _ = r.confidence_score()
        assert level == "LOW"

    def test_to_dict(self):
        r = ResearchResults()
        r.add(Finding(title="T", snippet="S", url="U", source_type="WEB"), "web_search")
        d = r.to_dict()
        assert "confidence" in d
        assert "findings" in d
        assert "tool_usage" in d
        assert len(d["findings"]) == 1

    def test_to_appendix(self):
        r = ResearchResults()
        r.add(Finding(title="Title", snippet="S", url="http://x", date="2025", source_type="WEB"), "web_search")
        appendix = r.to_appendix()
        assert "Appendix" in appendix
        assert "Confidence" in appendix
        assert "http://x" in appendix
