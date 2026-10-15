"""Tests for tool implementations with mocked HTTP."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest

from tools.implementations import (
    AcademicSearch,
    CensusSearch,
    CongressSearch,
    CourtSearch,
    FederalRegisterSearch,
    RegulationsSearch,
    StateLegislationSearch,
    WebSearch,
)
from tools.registry import ToolRegistry
from tools.models import ToolResult


@pytest.fixture
def mock_fetch():
    """Patch BaseTool._fetch_json to return controlled data."""
    with patch("tools.implementations.BaseTool._fetch_json") as mock:
        yield mock


class TestWebSearch:
    def test_uses_current_search_api(self, monkeypatch):
        monkeypatch.setenv("EXA_API_KEY", "test-key")
        mock_client = MagicMock()
        mock_client.search.return_value = MagicMock(
            results=[
                MagicMock(
                    title="Policy Update",
                    summary="Summary text",
                    text="Full text",
                    url="http://example.com",
                    published_date="2025-01-15T00:00:00Z",
                )
            ]
        )

        tool = WebSearch()
        tool._client = mock_client

        result = tool.execute(query="caregiver policy")

        assert len(result.findings) == 1
        _, kwargs = mock_client.search.call_args
        assert kwargs["num_results"] == 25
        assert kwargs["contents"] == {"text": {"max_characters": 1500}}
        assert result.findings[0].title == "Policy Update"

    def test_missing_api_key(self, monkeypatch):
        monkeypatch.delenv("EXA_API_KEY", raising=False)
        result = WebSearch().execute(query="caregiver policy")
        assert result.findings == []
        assert result.errors == ["EXA_API_KEY not set"]

    def test_value_error_surfaces_as_provider_error(self, monkeypatch):
        monkeypatch.setenv("EXA_API_KEY", "test-key")
        mock_client = MagicMock()
        mock_client.search.side_effect = ValueError("status code 429")
        tool = WebSearch()
        tool._client = mock_client
        result = tool.execute(query="caregiver policy")
        assert result.findings == []
        assert result.errors == ["Exa API error: status code 429"]


class TestAcademicSearch:
    def test_returns_findings(self, mock_fetch):
        mock_fetch.return_value = {
            "data": [
                {
                    "title": "Paper Title",
                    "abstract": "Abstract text",
                    "year": 2025,
                    "citationCount": 42,
                    "url": "http://paper",
                    "authors": [{"name": "Author A"}, {"name": "Author B"}],
                }
            ]
        }
        result = AcademicSearch().execute(query="test")
        assert len(result.findings) == 1
        assert result.findings[0].title == "Paper Title"
        assert result.findings[0].citations == 42
        assert result.findings[0].source_type == "ACADEMIC"

    def test_empty_query(self):
        result = AcademicSearch().execute(query="")
        assert result.findings == []
        assert result.errors == ["No query provided"]

    def test_api_error(self, mock_fetch):
        mock_fetch.side_effect = httpx.ConnectError("fail")
        result = AcademicSearch().execute(query="test")
        assert result.findings == []
        assert "api error" in result.errors[0].lower()

    def test_handles_null_abstract(self, mock_fetch):
        mock_fetch.return_value = {
            "data": [
                {
                    "title": "Paper Title",
                    "abstract": None,
                    "year": 2025,
                    "citationCount": 1,
                    "url": "http://paper",
                    "authors": [{"name": "Author A"}],
                }
            ]
        }
        result = AcademicSearch().execute(query="test")
        assert len(result.findings) == 1
        assert result.findings[0].title == "Paper Title"
        assert "Authors:" in result.findings[0].snippet


def _fixture(name: str):
    return json.loads((Path(__file__).parent / "fixtures" / name).read_text())


class TestCongressSearch:
    @pytest.fixture
    def mock_search(self):
        with patch("tools.implementations.CongressSearch._search") as mock:
            mock.return_value = _fixture("govinfo_bills.json")
            yield mock

    def test_searches_bill_text_with_all_terms(self, mock_search, monkeypatch):
        monkeypatch.setenv("GOVINFO_API_KEY", "test-key")
        CongressSearch().execute(query="artificial intelligence", since="2025-01-01")
        body = mock_search.call_args.args[0]
        assert body["query"] == (
            "collection:BILLS AND publishdate:range(2025-01-01,) AND artificial AND intelligence"
        )

    def test_returns_congress_gov_urls_and_status(self, mock_search, monkeypatch):
        monkeypatch.setenv("GOVINFO_API_KEY", "test-key")
        result = CongressSearch().execute(query="test")
        assert result.errors == []
        by_title = {f.title.split(":")[0]: f for f in result.findings}
        sres = by_title["SRES918"]
        assert sres.url == "https://www.congress.gov/bill/119th-congress/senate-resolution/918"
        assert sres.snippet == "Congress 119 | Status: Introduced in Senate"
        assert sres.date == "2026-09-29"
        assert by_title["S891"].url == "https://www.congress.gov/bill/119th-congress/senate-bill/891"
        assert all(f.source_type == "CONGRESS" for f in result.findings)

    def test_keeps_newest_text_version_per_bill(self, monkeypatch):
        monkeypatch.setenv("GOVINFO_API_KEY", "test-key")
        rows = [
            {"packageId": "BILLS-119hr10024ih", "dateIssued": "2026-08-03", "title": "Act"},
            {"packageId": "BILLS-119hr10024eh", "dateIssued": "2026-09-03", "title": "Act"},
        ]
        with patch("tools.implementations.CongressSearch._search", return_value={"results": rows}):
            result = CongressSearch().execute(query="test")
        assert [f.snippet for f in result.findings] == ["Congress 119 | Status: Engrossed in House"]

    def test_enforces_since_cutoff(self, monkeypatch):
        monkeypatch.setenv("GOVINFO_API_KEY", "test-key")
        rows = [
            {"packageId": "BILLS-110hconres2ih", "dateIssued": "2008-06-24", "title": "Stale"},
            {"packageId": "BILLS-119hr1234ih", "dateIssued": "2026-02-01", "title": "Current"},
        ]
        with patch("tools.implementations.CongressSearch._search", return_value={"results": rows}):
            result = CongressSearch().execute(query="x", since="2026-01-01")
        assert [f.title for f in result.findings] == ["HR1234: Current"]

    def test_missing_api_key(self, monkeypatch):
        monkeypatch.delenv("GOVINFO_API_KEY", raising=False)
        result = CongressSearch().execute(query="test")
        assert result.findings == []
        assert result.errors == ["GOVINFO_API_KEY not set"]

    def test_http_error_is_reported(self, monkeypatch):
        monkeypatch.setenv("GOVINFO_API_KEY", "test-key")
        response = httpx.Response(403, request=httpx.Request("POST", "https://api.govinfo.gov/search"))
        error = httpx.HTTPStatusError("denied", request=response.request, response=response)
        with patch("tools.implementations.CongressSearch._search", side_effect=error):
            result = CongressSearch().execute(query="test")
        assert result.findings == []
        assert result.errors == ["GovInfo API error (403): Forbidden"]


class TestFederalRegisterSearch:
    def test_returns_findings(self, mock_fetch):
        mock_fetch.return_value = {
            "results": [
                {
                    "title": "Rule Title",
                    "type": "Rule",
                    "agencies": [{"name": "EPA"}],
                    "abstract": "Abstract",
                    "html_url": "http://rule",
                    "publication_date": "2025-03-01",
                }
            ]
        }
        result = FederalRegisterSearch().execute(query="test")
        assert len(result.findings) == 1
        assert result.findings[0].source_type == "FED_REGISTER"

    def test_respects_results_limit_even_if_api_ignores_page_size(self, mock_fetch, monkeypatch):
        monkeypatch.setattr("tools.implementations._base.RESULTS_LIMIT", 1)
        mock_fetch.return_value = {
            "results": [
                {
                    "title": "Rule A",
                    "type": "Rule",
                    "agencies": [{"name": "EPA"}],
                    "abstract": "Abstract",
                    "html_url": "http://rule-a",
                    "publication_date": "2025-03-01",
                },
                {
                    "title": "Rule B",
                    "type": "Rule",
                    "agencies": [{"name": "EPA"}],
                    "abstract": "Abstract",
                    "html_url": "http://rule-b",
                    "publication_date": "2025-03-02",
                },
            ]
        }
        result = FederalRegisterSearch().execute(query="test")
        assert len(result.findings) == 1
        assert result.findings[0].title == "Rule A"


class TestRegulationsSearch:
    def test_returns_findings(self, mock_fetch, monkeypatch):
        monkeypatch.setenv("REGULATIONS_GOV_API_KEY", "test-key")
        mock_fetch.return_value = {
            "data": [
                {
                    "id": "DOC-123",
                    "attributes": {
                        "title": "Proposed Rule",
                        "documentType": "Proposed Rule",
                        "agencyId": "EPA",
                        "summary": "Summary text",
                        "postedDate": "2025-06-01T00:00:00Z",
                    },
                }
            ]
        }
        result = RegulationsSearch().execute(query="test")
        assert len(result.findings) == 1
        assert result.findings[0].source_type == "REGULATIONS"
        assert "DOC-123" in result.findings[0].url

    def test_respects_minimum_page_size(self, mock_fetch, monkeypatch):
        monkeypatch.setenv("REGULATIONS_GOV_API_KEY", "test-key")
        monkeypatch.setattr("tools.implementations._base.RESULTS_LIMIT", 1)
        mock_fetch.return_value = {"data": []}
        RegulationsSearch().execute(query="test")
        assert mock_fetch.call_args.kwargs["params"]["page[size]"] == 5

    def test_missing_api_key(self, monkeypatch):
        monkeypatch.delenv("REGULATIONS_GOV_API_KEY", raising=False)
        result = RegulationsSearch().execute(query="test")
        assert result.findings == []
        assert result.errors == ["REGULATIONS_GOV_API_KEY not set"]


class TestCourtSearch:
    def test_returns_findings(self, mock_fetch):
        mock_fetch.return_value = {
            "results": [
                {
                    "caseName": "Doe v. Smith",
                    "court": "Supreme Court",
                    "snippet": "The <mark>court</mark> held",
                    "absolute_url": "/opinion/123/",
                    "dateFiled": "2024-11-15",
                }
            ]
        }
        result = CourtSearch().execute(query="test")
        assert len(result.findings) == 1
        assert result.findings[0].title == "Doe v. Smith"
        assert "<mark>" not in result.findings[0].snippet
        assert "**court**" in result.findings[0].snippet


class TestCensusSearch:
    def test_returns_findings(self, mock_fetch):
        mock_fetch.return_value = [
            ["NAME", "B01003_001E", "B19013_001E", "B17001_002E"],
            ["California", "39538223", "78672", "4633830"],
            ["Texas", "29145505", "63826", "3938700"],
        ]
        result = CensusSearch().execute(topic="demographics")
        assert len(result.findings) == 2
        assert result.findings[0].title == "California"
        assert result.findings[0].source_type == "CENSUS"

    def test_specific_variable_match(self, mock_fetch):
        mock_fetch.return_value = [
            ["NAME", "B23025_005E"],
            ["United States", "6100000"],
        ]
        result = CensusSearch().execute(topic="unemployment rates")
        assert len(result.findings) == 1

    def test_geography_state_fips(self):
        assert CensusSearch()._parse_geography("state:CA") == {"for": "state:06"}

    def test_geography_us(self):
        tool = CensusSearch()
        assert tool._parse_geography("us") == {"for": "us:*"}
        assert tool._parse_geography("") == {"for": "us:*"}

    def test_invalid_api_key_is_reported_clearly(self, mock_fetch):
        request = httpx.Request("GET", "https://api.census.gov/data/2022/acs/acs5")
        response = httpx.Response(
            302,
            request=request,
            headers={"location": "https://api.census.gov/data/invalid_key.html"},
        )
        mock_fetch.side_effect = httpx.HTTPStatusError("redirect", request=request, response=response)
        result = CensusSearch().execute(topic="income")
        assert result.findings == []
        assert result.errors == ["CENSUS_API_KEY invalid"]


class TestRegistry:
    def test_returns_error_text_without_fake_findings(self):
        registry = ToolRegistry({"type": "all", "states": []})
        registry._tools["web_search"] = MagicMock(
            execute=MagicMock(return_value=ToolResult(errors=["boom"]))
        )

        findings, formatted = registry.execute("web_search", {"query": "test"})

        assert findings == []
        assert formatted == "Tool error: boom"

    def test_policy_scope_rejects_undeclared_tool_calls(self, monkeypatch):
        monkeypatch.setenv("EXA_API_KEY", "test-key")
        registry = ToolRegistry({"type": "policy", "states": []})
        findings, formatted = registry.execute("web_search", {"query": "test"})
        assert findings == []
        assert formatted == "Unknown tool: web_search"

    def test_unexpected_tool_exception_is_not_hidden(self):
        registry = ToolRegistry({"type": "all", "states": []})
        registry._tools["web_search"] = MagicMock(execute=MagicMock(side_effect=RuntimeError("boom")))
        with pytest.raises(RuntimeError, match="boom"):
            registry.execute("web_search", {"query": "test"})


class TestStateLegislationSearch:
    def test_returns_findings(self, mock_fetch, monkeypatch):
        monkeypatch.setenv("OPENSTATES_API_KEY", "test-key")
        mock_fetch.return_value = {
            "results": [
                {
                    "identifier": "SB-123",
                    "title": "Test Bill",
                    "jurisdiction": {"name": "California"},
                    "session": "2025-2026",
                    "latest_action_description": "Passed assembly",
                    "latest_action_date": "2025-04-01",
                    "openstates_url": "http://bill",
                }
            ]
        }
        result = StateLegislationSearch(default_states=["CA"]).execute(query="test")
        assert len(result.findings) == 1
        assert "SB-123" in result.findings[0].title
        assert result.findings[0].source_type == "STATE_LEG"

    def test_uses_legiscan_when_openstates_is_unavailable(self, mock_fetch, monkeypatch):
        monkeypatch.delenv("OPENSTATES_API_KEY", raising=False)
        monkeypatch.setenv("LEGISCAN_API_KEY", "test-key")
        mock_fetch.return_value = {
            "status": "OK",
            "searchresult": {
                "summary": {"count": 1},
                "0": {
                    "state": "CA",
                    "bill_number": "AB2324",
                    "title": "Vocational education: youth caregivers.",
                    "last_action_date": "2026-04-21",
                    "last_action": "Re-referred to Com. on APPR.",
                    "url": "https://legiscan.com/CA/bill/AB2324/2025",
                },
            },
        }
        result = StateLegislationSearch(default_states=["CA"]).execute(query="caregiver")
        assert len(result.findings) == 1
        assert result.findings[0].title.startswith("AB2324:")
        assert result.findings[0].source_type == "LEGISCAN"

    def test_falls_back_to_legiscan_when_openstates_returns_no_results(self, mock_fetch, monkeypatch):
        monkeypatch.setenv("OPENSTATES_API_KEY", "openstates-key")
        monkeypatch.setenv("LEGISCAN_API_KEY", "legiscan-key")
        mock_fetch.side_effect = [
            {"results": []},
            {
                "status": "OK",
                "searchresult": {
                    "summary": {"count": 1},
                    "0": {
                        "state": "CA",
                        "bill_number": "AB2324",
                        "title": "Vocational education: youth caregivers.",
                        "last_action_date": "2026-04-21",
                        "last_action": "Re-referred to Com. on APPR.",
                        "url": "https://legiscan.com/CA/bill/AB2324/2025",
                    },
                },
            },
        ]
        result = StateLegislationSearch(default_states=["CA"]).execute(query="caregiver")
        assert len(result.findings) == 1
        assert result.findings[0].source_type == "LEGISCAN"
        assert mock_fetch.call_count == 2

    def test_missing_api_key(self, monkeypatch):
        monkeypatch.delenv("OPENSTATES_API_KEY", raising=False)
        monkeypatch.delenv("LEGISCAN_API_KEY", raising=False)
        result = StateLegislationSearch().execute(query="test")
        assert result.findings == []
        assert result.errors == ["OPENSTATES_API_KEY or LEGISCAN_API_KEY not set"]

    def test_handles_missing_jurisdiction_object(self, mock_fetch, monkeypatch):
        monkeypatch.setenv("OPENSTATES_API_KEY", "test-key")
        mock_fetch.return_value = {
            "results": [
                {
                    "identifier": "SB-123",
                    "title": "Test Bill",
                    "jurisdiction": None,
                    "session": "2025-2026",
                    "latest_action_description": "Passed assembly",
                    "latest_action_date": "2025-04-01",
                    "openstates_url": "http://bill",
                }
            ]
        }
        result = StateLegislationSearch(default_states=["CA"]).execute(query="test")
        assert len(result.findings) == 1
        assert "State: N/A" in result.findings[0].snippet


class TestOpenStatesPaging:
    def test_pages_at_twenty_and_sets_state_code_and_official_url(self, mock_fetch, monkeypatch):
        monkeypatch.setenv("OPENSTATES_API_KEY", "test-key")
        monkeypatch.setattr("tools.base.RESULTS_LIMIT", 40)
        mock_fetch.side_effect = [_fixture("openstates_page1.json"), _fixture("openstates_page2.json")]
        result = StateLegislationSearch().execute(query="paid family caregiver", since="2025-01-01")

        assert result.errors == []
        assert len(result.findings) == 8
        pages = [call.kwargs["params"] for call in mock_fetch.call_args_list]
        assert [p["page"] for p in pages] == [1, 2]
        assert all(p["per_page"] == 20 for p in pages)
        assert all(p["action_since"] == "2025-01-01" for p in pages)
        first = result.findings[0]
        assert first.jurisdiction == "state:OH"
        assert first.url.startswith("https://openstates.org/oh/")
        assert first.official_url.startswith("https://www.legislature.ohio.gov/")
        assert first.date == first.date[:10]

    def test_stops_at_last_page(self, mock_fetch, monkeypatch):
        monkeypatch.setenv("OPENSTATES_API_KEY", "test-key")
        monkeypatch.setattr("tools.base.RESULTS_LIMIT", 100)
        page = _fixture("openstates_page1.json")
        page["pagination"]["max_page"] = 1
        mock_fetch.return_value = page
        StateLegislationSearch().execute(query="x")
        assert mock_fetch.call_count == 1

    def test_page_limit_is_bounded(self, mock_fetch, monkeypatch):
        monkeypatch.setenv("OPENSTATES_API_KEY", "test-key")
        monkeypatch.setattr("tools.base.RESULTS_LIMIT", 1000)
        page = _fixture("openstates_page1.json")
        page["pagination"]["max_page"] = 99
        mock_fetch.return_value = page
        StateLegislationSearch().execute(query="x")
        assert mock_fetch.call_count == 5

    def test_later_page_error_keeps_findings_and_reports_error(self, mock_fetch, monkeypatch):
        monkeypatch.setenv("OPENSTATES_API_KEY", "test-key")
        monkeypatch.setattr("tools.base.RESULTS_LIMIT", 40)
        response = httpx.Response(400, request=httpx.Request("GET", "https://v3.openstates.org/bills"))
        error = httpx.HTTPStatusError("bad", request=response.request, response=response)
        mock_fetch.side_effect = [_fixture("openstates_page1.json"), error]
        result = StateLegislationSearch().execute(query="x")
        assert len(result.findings) == 4
        assert result.errors == ["OpenStates (page 2) API error (400): Bad Request"]

    def test_legiscan_fallback_keeps_openstates_error(self, mock_fetch, monkeypatch):
        monkeypatch.setenv("OPENSTATES_API_KEY", "os-key")
        monkeypatch.setenv("LEGISCAN_API_KEY", "ls-key")
        response = httpx.Response(400, request=httpx.Request("GET", "https://v3.openstates.org/bills"))
        error = httpx.HTTPStatusError("bad", request=response.request, response=response)
        legiscan = {
            "status": "OK",
            "searchresult": {
                "0": {
                    "bill_number": "AB1",
                    "title": "T",
                    "last_action": "Signed",
                    "last_action_date": "2026-01-01",
                    "url": "https://legiscan.com/CA/bill/AB1/2025",
                    "state_link": "https://leginfo.legislature.ca.gov/x",
                }
            },
        }
        mock_fetch.side_effect = [error, legiscan]
        result = StateLegislationSearch(default_states=["CA"]).execute(query="x")
        assert len(result.findings) == 1
        assert result.findings[0].jurisdiction == "state:CA"
        assert result.findings[0].official_url == "https://leginfo.legislature.ca.gov/x"
        assert result.errors == ["OpenStates (page 1) API error (400): Bad Request"]
