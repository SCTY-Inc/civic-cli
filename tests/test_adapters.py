"""Each adapter against recorded or documented response shapes. No network."""

import json

import httpx
import pytest

from civic.adapters import ADAPTERS, Query
from civic.http import SourceError
from civic.scope import parse_scope

from .conftest import KEYS, fixture


def run(name, transport, topic="paid family caregiver", scope="all", since=None, limit=25, env=KEYS):
    q = Query(topic=topic, scope=parse_scope(scope), since=since, limit=limit)
    return ADAPTERS[name].search(q, transport(), env)


class TestGovInfo:
    def test_searches_bill_text_with_all_terms_and_key_in_header(self, recorder, transport):
        run("govinfo", transport, topic="artificial intelligence", since="2025-01-01")
        request = recorder.to("api.govinfo.gov")[0]
        body = json.loads(request.content)
        assert body["query"] == (
            "collection:BILLS AND publishdate:range(2025-01-01,) AND artificial AND intelligence"
        )
        assert request.headers["X-Api-Key"] == "gi-secret"

    def test_links_congress_gov_and_govinfo(self, transport):
        records, errors = run("govinfo", transport)
        assert errors == []
        by_id = {r.identifier: r for r in records}
        sres = by_id["SRES 918"]
        assert sres.url == "https://www.congress.gov/bill/119th-congress/senate-resolution/918"
        assert sres.official_url == "https://www.govinfo.gov/app/details/BILLS-119sres918is"
        assert sres.status == "Introduced in Senate"
        assert sres.published_at == "2026-09-29"
        assert sres.movement == ("bill_introduced", True)
        assert by_id["S 891"].url == "https://www.congress.gov/bill/119th-congress/senate-bill/891"
        assert all(r.kind == "bill" and r.jurisdiction == "federal" for r in records)

    def test_keeps_newest_text_version_per_bill(self, recorder, transport):
        recorder.overrides["api.govinfo.gov"] = lambda _r: {"results": [
            {"packageId": "BILLS-119hr10024ih", "dateIssued": "2026-08-03", "title": "Act"},
            {"packageId": "BILLS-119hr10024eh", "dateIssued": "2026-09-03", "title": "Act"},
        ]}
        records, _ = run("govinfo", transport)
        assert [r.status for r in records] == ["Engrossed in House"]

    def test_enforces_since_cutoff(self, recorder, transport):
        recorder.overrides["api.govinfo.gov"] = lambda _r: {"results": [
            {"packageId": "BILLS-110hconres2ih", "dateIssued": "2008-06-24", "title": "Stale"},
            {"packageId": "BILLS-119hr1234ih", "dateIssued": "2026-02-01", "title": "Current"},
        ]}
        records, _ = run("govinfo", transport, since="2026-01-01")
        assert [r.title for r in records] == ["Current"]

    def test_http_error_carries_status(self, recorder, transport):
        recorder.overrides["api.govinfo.gov"] = lambda _r: httpx.Response(403)
        with pytest.raises(SourceError) as caught:
            run("govinfo", transport)
        assert str(caught.value) == "HTTP 403 Forbidden"
        assert caught.value.call.http_status == 403


class TestFederalRegister:
    def test_maps_documents(self, recorder, transport):
        records, _ = run("federal_register", transport, since="2025-01-01", limit=5)
        params = recorder.to("www.federalregister.gov")[0].url.params
        assert params["conditions[publication_date][gte]"] == "2025-01-01"
        assert params["per_page"] == "5"
        first = records[0]
        assert first.identifier == "2025-01234"
        assert first.movement == ("proposed_rule", True)
        assert first.details == {"agencies": ["Centers for Medicare & Medicaid Services"]}
        assert records[1].movement == ("agency_notice", True)

    def test_respects_limit_even_if_api_ignores_page_size(self, transport):
        records, _ = run("federal_register", transport, limit=1)
        assert len(records) == 1


class TestRegulations:
    def test_key_in_header_and_minimum_page_size(self, recorder, transport):
        records, _ = run("regulations", transport, limit=2)
        request = recorder.to("api.regulations.gov")[0]
        assert request.headers["X-Api-Key"] == "rg-secret"
        assert "api_key" not in request.url.params
        assert request.url.params["page[size]"] == "5"
        assert records[0].url == "https://www.regulations.gov/document/CMS-2025-0001-0001"
        assert records[0].published_at == "2025-07-14"
        assert records[0].details["docketId"] == "CMS-2025-0001"


class TestCourtListener:
    def test_maps_opinion_snippet_and_since(self, recorder, transport):
        records, _ = run("courtlistener", transport, since="2025-01-01")
        assert recorder.to("www.courtlistener.com")[0].url.params["filed_after"] == "2025-01-01"
        case = records[0]
        assert case.url == "https://www.courtlistener.com/opinion/123/doe-v-state/"
        assert case.summary == "a family caregiver may claim"
        assert case.movement == ("court_action", None)


class TestOpenStates:
    def test_pages_at_twenty_and_sets_state_code_and_official_url(self, recorder, transport):
        records, errors = run("openstates", transport, since="2025-01-01", limit=40)
        assert errors == []
        assert len(records) == 8
        params = [r.url.params for r in recorder.to("v3.openstates.org")]
        assert [p["page"] for p in params] == ["1", "2"]
        assert all(p["per_page"] == "20" and p["action_since"] == "2025-01-01" for p in params)
        first = records[0]
        assert first.jurisdiction == "state:OH"
        assert first.url.startswith("https://openstates.org/oh/")
        assert first.official_url.startswith("https://www.legislature.ohio.gov/")
        assert first.published_at == "2026-10-09"
        assert first.movement == ("bill_enacted", False)

    def test_stops_at_last_page(self, recorder, transport):
        page = fixture("openstates_page1.json")
        page["pagination"]["max_page"] = 1
        recorder.overrides["v3.openstates.org"] = lambda _r: page
        run("openstates", transport, limit=100)
        assert len(recorder.to("v3.openstates.org")) == 1

    def test_page_count_is_bounded(self, recorder, transport):
        page = fixture("openstates_page1.json")
        page["pagination"]["max_page"] = 99
        recorder.overrides["v3.openstates.org"] = lambda _r: page
        run("openstates", transport, limit=1000)
        assert len(recorder.to("v3.openstates.org")) == 5

    def test_later_page_error_keeps_records_and_reports_error(self, recorder, transport):
        recorder.overrides["v3.openstates.org"] = lambda r: (
            fixture("openstates_page1.json") if r.url.params["page"] == "1" else httpx.Response(400)
        )
        records, errors = run("openstates", transport, limit=40)
        assert len(records) == 4
        assert [str(e) for e in errors] == ["HTTP 400 Bad Request"]

    def test_one_query_per_state(self, recorder, transport):
        run("openstates", transport, scope="state:CA,NY", limit=20)
        assert [r.url.params["jurisdiction"] for r in recorder.to("v3.openstates.org")] == ["ca", "ny"]

    def test_handles_missing_jurisdiction_object(self, recorder, transport):
        recorder.overrides["v3.openstates.org"] = lambda _r: {"results": [{"identifier": "SB 1"}]}
        records, _ = run("openstates", transport)
        assert records[0].jurisdiction == ""


class TestLegiScan:
    def test_searches_all_states_and_filters_since(self, recorder, transport):
        records, _ = run("legiscan", transport, scope="policy", since="2025-01-01")
        assert recorder.to("api.legiscan.com")[0].url.params["state"] == "ALL"
        assert [r.identifier for r in records] == ["AB2324"]
        bill = records[0]
        assert bill.jurisdiction == "state:CA"
        assert bill.official_url.startswith("https://leginfo.legislature.ca.gov/")
        assert bill.movement == ("bill_referred", True)
        ops = [r.url.params["op"] for r in recorder.to("api.legiscan.com")]
        assert ops == ["getSearch", "getBill"]  # since-filtered bill costs no getBill

    def test_getbill_failure_keeps_record_and_reports_error(self, recorder, transport):
        recorder.overrides["api.legiscan.com"] = lambda r: (
            fixture("legiscan.json") if r.url.params["op"] == "getSearch"
            else {"status": "ERROR"})
        records, errors = run("legiscan", transport)
        assert records[0].official_url == ""
        assert "getBill status ERROR" in str(errors[0])

    def test_key_never_enters_trace(self, transport):
        records, _ = run("legiscan", transport)
        assert "key" not in records[0].provenance["params"]
        assert "ls-secret" not in json.dumps(records[0].to_dict())

    def test_status_error_is_reported(self, recorder, transport):
        recorder.overrides["api.legiscan.com"] = lambda _r: {
            "status": "ERROR", "alert": {"message": "Invalid API key"}}
        with pytest.raises(SourceError, match="LegiScan status ERROR: Invalid API key"):
            run("legiscan", transport, scope="state:CA")


class TestCensus:
    def test_matches_topic_variables_and_state_fips(self, recorder, transport):
        records, _ = run("census", transport, topic="household income", scope="state:CA,NY")
        params = recorder.to("api.census.gov")[0].url.params
        assert params["get"] == "NAME,B19013_001E"
        assert params["for"] == "state:06,36"
        assert [r.jurisdiction for r in records] == ["state:CA", "state:NY"]
        assert records[0].details["values"] == {"income": "91905"}
        assert "cn-secret" not in records[0].url and "key" not in records[0].provenance["params"]

    def test_defaults_to_national_core_variables(self, recorder, transport):
        recorder.overrides["api.census.gov"] = lambda _r: [["NAME", "a", "b", "c", "us"],
                                                           ["United States", "1", "2", "3", "1"]]
        records, _ = run("census", transport, topic="caregiver")
        assert recorder.to("api.census.gov")[0].url.params["for"] == "us:*"
        assert records[0].jurisdiction == "federal"

    def test_runs_without_key(self):
        assert ADAPTERS["census"].available({})


class TestSemanticScholar:
    def test_maps_paper_and_applies_since(self, recorder, transport):
        records, _ = run("semantic_scholar", transport, since="2025-01-01")
        assert recorder.to("api.semanticscholar.org")[0].url.params["publicationDateOrYear"] == "2025-01-01:"
        paper = records[0]
        assert paper.citations == 4
        assert paper.official_url == "https://doi.org/10.1000/xyz"
        assert paper.summary == ""  # null abstract stays empty


class TestExa:
    def test_posts_search_with_key_header(self, recorder, transport):
        records, _ = run("exa", transport, since="2025-01-01", limit=3)
        request = recorder.to("api.exa.ai")[0]
        assert request.method == "POST"
        assert request.headers["x-api-key"] == "ex-secret"
        body = json.loads(request.content)
        assert body["numResults"] == 3 and body["startPublishedDate"] == "2025-01-01"
        assert records[0].published_at == "2025-06-01"
        assert records[0].kind == "web"
