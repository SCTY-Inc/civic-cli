import os

import httpx
from exa_py import Exa

from . import base as _base
from .base import BaseTool
from .models import Finding, ToolResult

STATE_FIPS = {
    "AL": "01",
    "AK": "02",
    "AZ": "04",
    "AR": "05",
    "CA": "06",
    "CO": "08",
    "CT": "09",
    "DE": "10",
    "DC": "11",
    "FL": "12",
    "GA": "13",
    "HI": "15",
    "ID": "16",
    "IL": "17",
    "IN": "18",
    "IA": "19",
    "KS": "20",
    "KY": "21",
    "LA": "22",
    "ME": "23",
    "MD": "24",
    "MA": "25",
    "MI": "26",
    "MN": "27",
    "MS": "28",
    "MO": "29",
    "MT": "30",
    "NE": "31",
    "NV": "32",
    "NH": "33",
    "NJ": "34",
    "NM": "35",
    "NY": "36",
    "NC": "37",
    "ND": "38",
    "OH": "39",
    "OK": "40",
    "OR": "41",
    "PA": "42",
    "PR": "72",
    "RI": "44",
    "SC": "45",
    "SD": "46",
    "TN": "47",
    "TX": "48",
    "UT": "49",
    "VT": "50",
    "VA": "51",
    "WA": "53",
    "WV": "54",
    "WI": "55",
    "WY": "56",
}


def _as_mapping(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def _as_mapping_list(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _as_string(value: object, default: str = "") -> str:
    return value if isinstance(value, str) else default


def _stringify(value: object, default: str = "") -> str:
    if value is None:
        return default
    return value if isinstance(value, str) else str(value)


class WebSearch(BaseTool):
    SOURCE_TYPE = "WEB"

    def __init__(self):
        super().__init__()
        self._client: Exa | None = None

    @property
    def client(self) -> Exa:
        if self._client is None:
            api_key = os.getenv("EXA_API_KEY")
            if not api_key:
                raise ValueError("EXA_API_KEY not set")
            self._client = Exa(api_key=api_key)
        return self._client

    def execute(self, query: str = "", **kwargs) -> ToolResult:
        if not query:
            return self._error("No query provided")
        if not os.getenv("EXA_API_KEY"):
            return self._missing_api_key("EXA_API_KEY")
        try:
            results = self.client.search(
                query,
                type="auto",
                num_results=_base.RESULTS_LIMIT,
                contents={"text": {"max_characters": 1500}},
            )
        except ValueError as error:
            return self._error(f"Exa API error: {error}")

        findings = []
        for result in results.results[:_base.RESULTS_LIMIT]:
            snippet = getattr(result, "summary", "") or _as_string(
                getattr(result, "text", "")
            )[:1000]
            date = _as_string(getattr(result, "published_date", ""))[:10]
            findings.append(
                Finding(
                    title=getattr(result, "title", "") or "Untitled",
                    snippet=snippet,
                    url=getattr(result, "url", ""),
                    date=date,
                    source_type=self.SOURCE_TYPE,
                )
            )
        return self._ok(findings)


class AcademicSearch(BaseTool):
    SOURCE_TYPE = "ACADEMIC"
    BASE_URL = "https://api.semanticscholar.org/graph/v1"

    def execute(self, query: str = "", **kwargs) -> ToolResult:
        if not query:
            return self._error("No query provided")
        try:
            data = self._fetch_json(
                f"{self.BASE_URL}/paper/search",
                params={
                    "query": query,
                    "limit": _base.RESULTS_LIMIT,
                    "fields": "title,abstract,year,citationCount,url,authors",
                },
            )
        except httpx.HTTPError as error:
            return self._http_error("Semantic Scholar", error)

        if not isinstance(data, dict):
            return self._parse_error("academic", TypeError("response was not an object"))

        findings = []
        for paper in _as_mapping_list(data.get("data"))[:_base.RESULTS_LIMIT]:
            authors_raw = _as_mapping_list(paper.get("authors"))
            authors = ", ".join(
                _as_string(author.get("name"))
                for author in authors_raw[:3]
                if _as_string(author.get("name"))
            )
            if len(authors_raw) > 3:
                authors += " et al."
            abstract = _as_string(paper.get("abstract"))
            citation_count = paper.get("citationCount", 0)
            findings.append(
                Finding(
                    title=_as_string(paper.get("title"), "Untitled"),
                    snippet=f"Authors: {authors}\n{abstract[:600]}",
                    url=_as_string(paper.get("url")),
                    date=_stringify(paper.get("year")),
                    source_type=self.SOURCE_TYPE,
                    citations=citation_count if isinstance(citation_count, int) else 0,
                )
            )
        return self._ok(findings)


class CongressSearch(BaseTool):
    SOURCE_TYPE = "CONGRESS"
    BASE_URL = "https://api.congress.gov/v3"

    def execute(self, query: str = "", **kwargs) -> ToolResult:
        api_key = os.getenv("CONGRESS_GOV_API_KEY")
        if not api_key:
            return self._missing_api_key("CONGRESS_GOV_API_KEY")
        if not query:
            return self._error("No query provided")
        try:
            data = self._fetch_json(
                f"{self.BASE_URL}/bill",
                params={"query": query, "limit": _base.RESULTS_LIMIT, "api_key": api_key},
            )
        except httpx.HTTPError as error:
            return self._http_error("Congress.gov", error)

        if not isinstance(data, dict):
            return self._parse_error("Congress", TypeError("response was not an object"))

        findings = []
        for bill in _as_mapping_list(data.get("bills"))[:_base.RESULTS_LIMIT]:
            latest_action = _as_mapping(bill.get("latestAction"))
            bill_id = f"{_as_string(bill.get('type'))}{_as_string(bill.get('number'))}"
            findings.append(
                Finding(
                    title=f"{bill_id}: {_as_string(bill.get('title'), 'No title')}",
                    snippet=(
                        f"Congress {_stringify(bill.get('congress'), 'N/A')} | "
                        f"Status: {_as_string(latest_action.get('text'), 'N/A')}"
                    ),
                    url=_as_string(bill.get("url")),
                    date=_as_string(latest_action.get("actionDate"))
                    or _as_string(bill.get("introducedDate")),
                    source_type=self.SOURCE_TYPE,
                )
            )
        return self._ok(findings)


class FederalRegisterSearch(BaseTool):
    SOURCE_TYPE = "FED_REGISTER"
    BASE_URL = "https://www.federalregister.gov/api/v1"

    def execute(self, query: str = "", **kwargs) -> ToolResult:
        if not query:
            return self._error("No query provided")
        try:
            data = self._fetch_json(
                f"{self.BASE_URL}/documents.json",
                params={
                    "conditions[term]": query,
                    "per_page": _base.RESULTS_LIMIT,
                    "order": "relevance",
                },
            )
        except httpx.HTTPError as error:
            return self._http_error("Federal Register", error)

        if not isinstance(data, dict):
            return self._parse_error("Federal Register", TypeError("response was not an object"))

        findings = []
        for document in _as_mapping_list(data.get("results"))[:_base.RESULTS_LIMIT]:
            agencies = ", ".join(
                _as_string(agency.get("name"))
                for agency in _as_mapping_list(document.get("agencies"))[:2]
                if _as_string(agency.get("name"))
            ) or "N/A"
            snippet = f"Type: {_as_string(document.get('type'), 'N/A')} | Agency: {agencies}"
            abstract = _as_string(document.get("abstract"))
            if abstract:
                snippet += f"\n{abstract[:500]}"
            findings.append(
                Finding(
                    title=_as_string(document.get("title"), "Untitled"),
                    snippet=snippet,
                    url=_as_string(document.get("html_url")),
                    date=_as_string(document.get("publication_date")),
                    source_type=self.SOURCE_TYPE,
                )
            )
        return self._ok(findings)


class RegulationsSearch(BaseTool):
    SOURCE_TYPE = "REGULATIONS"
    BASE_URL = "https://api.regulations.gov/v4"

    def execute(self, query: str = "", **kwargs) -> ToolResult:
        api_key = os.getenv("REGULATIONS_GOV_API_KEY")
        if not api_key:
            return self._missing_api_key("REGULATIONS_GOV_API_KEY")
        if not query:
            return self._error("No query provided")
        try:
            data = self._fetch_json(
                f"{self.BASE_URL}/documents",
                params={
                    "filter[searchTerm]": query,
                    "page[size]": max(5, _base.RESULTS_LIMIT),
                    "api_key": api_key,
                },
            )
        except httpx.HTTPError as error:
            return self._http_error("Regulations.gov", error)

        if not isinstance(data, dict):
            return self._parse_error("Regulations.gov", TypeError("response was not an object"))

        findings = []
        for document in _as_mapping_list(data.get("data"))[:_base.RESULTS_LIMIT]:
            attrs = _as_mapping(document.get("attributes"))
            summary = _as_string(attrs.get("summary"))
            snippet = (
                f"Type: {_as_string(attrs.get('documentType'), 'N/A')} | "
                f"Agency: {_as_string(attrs.get('agencyId'), 'N/A')}"
            )
            if summary:
                snippet += f"\n{summary[:500]}"
            findings.append(
                Finding(
                    title=_as_string(attrs.get("title"), "Untitled"),
                    snippet=snippet,
                    url=f"https://www.regulations.gov/document/{_as_string(document.get('id'))}",
                    date=_as_string(attrs.get("postedDate"))[:10],
                    source_type=self.SOURCE_TYPE,
                )
            )
        return self._ok(findings)


class CourtSearch(BaseTool):

    SOURCE_TYPE = "COURT"
    BASE_URL = "https://www.courtlistener.com/api/rest/v4"

    def execute(self, query: str = "", **kwargs) -> ToolResult:
        if not query:
            return self._error("No query provided")
        try:
            data = self._fetch_json(
                f"{self.BASE_URL}/search/",
                params={
                    "q": query,
                    "type": "o",
                    "order_by": "score desc",
                    "page_size": _base.RESULTS_LIMIT,
                },
            )
        except httpx.HTTPError as error:
            return self._http_error("CourtListener", error)

        if not isinstance(data, dict):
            return self._parse_error("CourtListener", TypeError("response was not an object"))

        findings = []
        for case in _as_mapping_list(data.get("results"))[:_base.RESULTS_LIMIT]:
            snippet = f"Court: {_as_string(case.get('court'), 'N/A')}"
            raw_snippet = _as_string(case.get("snippet"))
            if raw_snippet:
                clean = raw_snippet.replace("<mark>", "**").replace("</mark>", "**")
                snippet += f"\n{clean[:500]}"
            findings.append(
                Finding(
                    title=_as_string(case.get("caseName"), "Unknown Case"),
                    snippet=snippet,
                    url=f"https://www.courtlistener.com{_as_string(case.get('absolute_url'))}",
                    date=_as_string(case.get("dateFiled")),
                    source_type=self.SOURCE_TYPE,
                )
            )
        return self._ok(findings)


class CensusSearch(BaseTool):
    SOURCE_TYPE = "CENSUS"
    BASE_URL = "https://api.census.gov/data"

    VARIABLES = {
        "population": "B01003_001E",
        "median_age": "B01002_001E",
        "income": "B19013_001E",
        "poverty": "B17001_002E",
        "gini": "B19083_001E",
        "housing": "B25001_001E",
        "median_rent": "B25064_001E",
        "homeownership": "B25003_002E",
        "vacancy": "B25002_003E",
        "education": "B15003_022E",
        "high_school": "B15003_017E",
        "unemployment": "B23025_005E",
        "labor_force": "B23025_002E",
        "uninsured": "B27010_017E",
        "insurance": "B27010_002E",
        "disability": "B18101_001E",
    }

    def execute(self, topic: str = "", geography: str = "us", **kwargs) -> ToolResult:
        api_key = os.getenv("CENSUS_API_KEY")
        topic_lower = topic.lower()

        matched = [(key, value) for key, value in self.VARIABLES.items() if key in topic_lower]
        if not matched:
            matched = [
                ("population", self.VARIABLES["population"]),
                ("income", self.VARIABLES["income"]),
                ("poverty", self.VARIABLES["poverty"]),
            ]

        selected: list[tuple[str, str]] = []
        seen_variables: set[str] = set()
        for label, variable in matched:
            if variable in seen_variables:
                continue
            seen_variables.add(variable)
            selected.append((label, variable))
            if len(selected) == 5:
                break

        variables = [variable for _, variable in selected]
        var_labels = [label for label, _ in selected]
        geo_params = self._parse_geography(geography)
        params: dict[str, object] = {"get": ",".join(["NAME"] + variables), **geo_params}
        if api_key:
            params["key"] = api_key

        try:
            data = self._fetch_json(f"{self.BASE_URL}/2022/acs/acs5", params=params)
        except httpx.HTTPStatusError as error:
            if error.response.status_code == 302 and "invalid_key" in str(error.response.headers.get("location", "")):
                return self._error("CENSUS_API_KEY invalid")
            return self._http_error("Census", error)
        except httpx.HTTPError as error:
            return self._http_error("Census", error)

        if not isinstance(data, list):
            return self._parse_error("Census", TypeError("response was not a table"))
        if len(data) < 2:
            return self._error(f"No census data for {topic}")

        findings = []
        for row in [item for item in data[1:6] if isinstance(item, list)]:
            if not row:
                continue
            parts = []
            for index, label in enumerate(var_labels, start=1):
                value = row[index] if index < len(row) else "N/A"
                parts.append(f"{label}: {value}")
            findings.append(
                Finding(
                    title=str(row[0]),
                    snippet=" | ".join(parts),
                    url="https://data.census.gov",
                    date="2022",
                    source_type=self.SOURCE_TYPE,
                )
            )
        return self._ok(findings)

    def _parse_geography(self, geography: str) -> dict[str, str]:
        if not geography or geography == "us":
            return {"for": "us:*"}
        if geography.startswith("state:"):
            code = geography[6:].strip().upper()
            fips = STATE_FIPS.get(code)
            if fips:
                return {"for": f"state:{fips}"}
            return {"for": "state:*"}
        if geography.startswith("county:"):
            parts = geography[7:].strip().split(",")
            if len(parts) == 2:
                state_fips = STATE_FIPS.get(parts[1].strip().upper())
                if state_fips:
                    return {"for": "county:*", "in": f"state:{state_fips}"}
            return {"for": "county:*", "in": "state:*"}
        return {"for": "state:*"}


class StateLegislationSearch(BaseTool):
    SOURCE_TYPE = "STATE_LEG"
    BASE_URL = "https://v3.openstates.org"
    LEGISCAN_URL = "https://api.legiscan.com/"

    def __init__(self, default_states: list[str] | None = None):
        super().__init__()
        self.default_states = default_states or []

    def execute(self, query: str = "", state: str | None = None, **kwargs) -> ToolResult:
        if not query:
            return self._error("No query provided")

        openstates_api_key = os.getenv("OPENSTATES_API_KEY")
        legiscan_api_key = os.getenv("LEGISCAN_API_KEY")
        if not openstates_api_key and not legiscan_api_key:
            return self._error("OPENSTATES_API_KEY or LEGISCAN_API_KEY not set")

        state_code = self._single_state_code(state)
        jurisdiction = state_code.lower() if state_code else None

        openstates_result: ToolResult | None = None
        if openstates_api_key:
            openstates_result = self._openstates_search(query, jurisdiction, openstates_api_key)
            if openstates_result.findings:
                return openstates_result
            if not legiscan_api_key or not state_code:
                return openstates_result

        if not legiscan_api_key:
            return openstates_result or self._missing_api_key("LEGISCAN_API_KEY")
        if not state_code:
            if openstates_result is not None:
                return openstates_result
            return self._error("LegiScan fallback requires a single state")

        legiscan_result = self._legiscan_search(query, state_code, legiscan_api_key)
        if legiscan_result.findings or openstates_result is None:
            return legiscan_result
        if openstates_result.errors and legiscan_result.errors:
            return ToolResult(errors=[*openstates_result.errors, *legiscan_result.errors])
        return legiscan_result if legiscan_result.errors else openstates_result

    def _single_state_code(self, state: str | None) -> str | None:
        if state:
            return state.strip().upper()
        if len(self.default_states) == 1:
            return self.default_states[0].strip().upper()
        return None

    def _openstates_search(
        self,
        query: str,
        jurisdiction: str | None,
        api_key: str,
    ) -> ToolResult:
        params: dict[str, object] = {"q": query, "per_page": _base.RESULTS_LIMIT}
        if jurisdiction:
            params["jurisdiction"] = jurisdiction

        try:
            data = self._fetch_json(
                f"{self.BASE_URL}/bills",
                params=params,
                headers={"X-API-KEY": api_key},
            )
        except httpx.HTTPError as error:
            return self._http_error("OpenStates", error)

        if not isinstance(data, dict):
            return self._parse_error("state legislation", TypeError("response was not an object"))

        findings = []
        for bill in _as_mapping_list(data.get("results"))[:_base.RESULTS_LIMIT]:
            jurisdiction_info = _as_mapping(bill.get("jurisdiction"))
            latest = _as_string(bill.get("latest_action_description"), "N/A")[:100]
            findings.append(
                Finding(
                    title=(
                        f"{_as_string(bill.get('identifier'), 'N/A')}: "
                        f"{_as_string(bill.get('title'), 'No title')[:80]}"
                    ),
                    snippet=(
                        f"State: {_as_string(jurisdiction_info.get('name'), 'N/A')} | "
                        f"Session: {_stringify(bill.get('session'), 'N/A')} | Latest: {latest}"
                    ),
                    url=_as_string(bill.get("openstates_url")),
                    date=_as_string(bill.get("latest_action_date")),
                    source_type=self.SOURCE_TYPE,
                )
            )
        return self._ok(findings)

    def _legiscan_search(self, query: str, state_code: str, api_key: str) -> ToolResult:
        try:
            data = self._fetch_json(
                self.LEGISCAN_URL,
                params={
                    "key": api_key,
                    "op": "getSearch",
                    "state": state_code,
                    "query": query,
                },
            )
        except httpx.HTTPError as error:
            return self._http_error("LegiScan", error)

        if not isinstance(data, dict):
            return self._parse_error("LegiScan", TypeError("response was not an object"))

        status = _as_string(data.get("status"))
        if status and status.upper() != "OK":
            return self._error(f"LegiScan API error: {status}")

        searchresult = _as_mapping(data.get("searchresult"))
        findings = []
        for key in sorted(
            (item for item in searchresult if item.isdigit()),
            key=int,
        )[:_base.RESULTS_LIMIT]:
            bill = _as_mapping(searchresult.get(key))
            latest = _as_string(bill.get("last_action"), "N/A")[:120]
            findings.append(
                Finding(
                    title=(
                        f"{_as_string(bill.get('bill_number'), 'N/A')}: "
                        f"{_as_string(bill.get('title'), 'No title')[:80]}"
                    ),
                    snippet=f"State: {state_code} | Latest: {latest}",
                    url=_as_string(bill.get("url")),
                    date=_as_string(bill.get("last_action_date")),
                    source_type="LEGISCAN",
                )
            )
        return self._ok(findings)
