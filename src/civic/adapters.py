"""Source adapters. Each wraps one official or open service.

Interface: `Adapter.search(query, transport) -> (records, errors)`. An adapter
raises `SourceError` when it produces nothing; it returns errors alongside
records when only part of the work failed (for example, a later page).
Attribution, terms, and rate limits for every service: SOURCES.md.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import ClassVar

from .http import Call, SourceError, Transport
from .record import Record, trace
from .scope import STATE_FIPS, Scope


@dataclass(frozen=True)
class Query:
    topic: str
    scope: Scope
    since: str | None = None
    limit: int = 25


Result = tuple[list[Record], list[SourceError]]


def _map(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def _maps(value: object) -> list[dict[str, object]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _str(value: object) -> str:
    if value is None:
        return ""
    return value if isinstance(value, str) else str(value)


class Adapter:
    name: str = ""
    version: str = "1"
    service: str = ""
    docs: str = ""
    terms: str = ""
    env: str | None = None  # required credential, if any
    scopes: frozenset[str] = frozenset()

    def available(self, env: Mapping[str, str]) -> bool:
        return not self.env or bool(env.get(self.env))

    def key(self, env: Mapping[str, str]) -> str:
        return env.get(self.env, "") if self.env else ""

    def search(self, q: Query, http: Transport, env: Mapping[str, str]) -> Result:
        raise NotImplementedError

    def _get(self, http: Transport, url: str, **kwargs) -> tuple[object, Call]:
        return http.request(self.name, "GET", url, **kwargs)

    def _trace(self, record: Record, call: Call, item: object) -> Record:
        return trace(record, adapter=self.name, version=self.version, terms=self.terms,
                     call=call, item=item)


def _expect(data: object, kind: type, call: Call) -> None:
    if not isinstance(data, kind):
        raise SourceError(f"unexpected response shape: {type(data).__name__}", call)


# --- Federal legislation ---------------------------------------------------

_BILL_PATH = {
    "hr": "house-bill", "s": "senate-bill", "hjres": "house-joint-resolution",
    "sjres": "senate-joint-resolution", "hconres": "house-concurrent-resolution",
    "sconres": "senate-concurrent-resolution", "hres": "house-resolution",
    "sres": "senate-resolution",
}
_BILL_VERSION = {
    "ih": "Introduced in House", "is": "Introduced in Senate", "rh": "Reported in House",
    "rs": "Reported in Senate", "rfh": "Referred in House", "rfs": "Referred in Senate",
    "eh": "Engrossed in House", "es": "Engrossed in Senate",
    "eah": "Engrossed amendment House", "eas": "Engrossed amendment Senate",
    "pcs": "Placed on calendar Senate", "pch": "Placed on calendar House",
    "enr": "Enrolled bill",
}
_PACKAGE_ID = re.compile(r"^BILLS-(\d+)(hr|s|hjres|sjres|hconres|sconres|hres|sres)(\d+)([a-z]+)$")


def _ordinal(value: str) -> str:
    n = int(value)
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


class GovInfo(Adapter):
    """Federal bill text search. Links each bill to its Congress.gov page.

    The Congress.gov API bill endpoint has no keyword filter, so topic search
    runs against GovInfo's BILLS collection instead.
    """

    name = "govinfo"
    service = "GovInfo (U.S. GPO) search API, BILLS collection"
    docs = "https://api.govinfo.gov/docs/"
    terms = "U.S. government work, public domain (17 U.S.C. 105)"
    env = "GOVINFO_API_KEY"
    scopes = frozenset({"all", "federal", "policy"})
    URL = "https://api.govinfo.gov/search"

    def search(self, q: Query, http: Transport, env: Mapping[str, str]) -> Result:
        terms = [t for t in q.topic.replace('"', " ").split() if t]
        clauses = ["collection:BILLS", *terms]
        if q.since:
            clauses.insert(1, f"publishdate:range({q.since},)")
        body = {"query": " AND ".join(clauses), "pageSize": min(100, q.limit * 3), "offsetMark": "*"}
        data, call = http.request(self.name, "POST", self.URL, json_body=body,
                                  headers={"X-Api-Key": self.key(env)})
        _expect(data, dict, call)
        # One record per bill: keep the newest text version.
        latest: dict[tuple[str, str, str], tuple[str, str, dict[str, object]]] = {}
        for row in _maps(data.get("results")):
            match = _PACKAGE_ID.match(_str(row.get("packageId")))
            if not match:
                continue
            congress, bill_type, number, version = match.groups()
            date = _str(row.get("dateIssued"))[:10]
            if q.since and date and date < q.since:
                continue
            key = (congress, bill_type, number)
            if key not in latest or date > latest[key][0]:
                latest[key] = (date, version, row)
        records = [
            self._trace(
                Record(
                    source=self.name,
                    kind="bill",
                    title=_str(row.get("title")) or "No title",
                    url=(f"https://www.congress.gov/bill/{_ordinal(congress)}-congress/"
                         f"{_BILL_PATH[bill_type]}/{number}"),
                    published_at=date,
                    jurisdiction="federal",
                    identifier=f"{bill_type.upper()} {number}",
                    status=_BILL_VERSION.get(version, f"Text version {version}"),
                    official_url=f"https://www.govinfo.gov/app/details/{row.get('packageId')}",
                    details={"congress": congress, "package_id": row.get("packageId")},
                ),
                call,
                row,
            )
            for (congress, bill_type, number), (date, version, row) in latest.items()
        ]
        records.sort(key=lambda r: r.published_at, reverse=True)
        return records[: q.limit], []


# --- Federal regulation ----------------------------------------------------


class FederalRegister(Adapter):
    name = "federal_register"
    service = "Federal Register API v1 (Office of the Federal Register / GPO)"
    docs = "https://www.federalregister.gov/developers/documentation/api/v1"
    terms = "U.S. government work, public domain (17 U.S.C. 105)"
    scopes = frozenset({"all", "federal", "policy"})
    URL = "https://www.federalregister.gov/api/v1/documents.json"

    def search(self, q: Query, http: Transport, env: Mapping[str, str]) -> Result:
        params: dict[str, object] = {"conditions[term]": q.topic, "per_page": q.limit,
                                     "order": "relevance"}
        if q.since:
            params["conditions[publication_date][gte]"] = q.since
        data, call = self._get(http, self.URL, params=params)
        _expect(data, dict, call)
        records = []
        for doc in _maps(data.get("results"))[: q.limit]:
            agencies = [_str(a.get("name")) for a in _maps(doc.get("agencies")) if a.get("name")]
            records.append(self._trace(
                Record(
                    source=self.name,
                    kind="regulatory",
                    title=_str(doc.get("title")) or "Untitled",
                    url=_str(doc.get("html_url")),
                    summary=_str(doc.get("abstract")),
                    published_at=_str(doc.get("publication_date")),
                    jurisdiction="federal",
                    identifier=_str(doc.get("document_number")),
                    status=_str(doc.get("type")),
                    details={"agencies": agencies} if agencies else {},
                ),
                call,
                doc,
            ))
        return records, []


class RegulationsGov(Adapter):
    name = "regulations"
    service = "Regulations.gov API v4 (GSA eRulemaking)"
    docs = "https://open.gsa.gov/api/regulationsgov/"
    terms = "U.S. government data; public comments may carry third-party rights"
    env = "REGULATIONS_GOV_API_KEY"
    scopes = frozenset({"all", "federal", "policy"})
    URL = "https://api.regulations.gov/v4/documents"

    def search(self, q: Query, http: Transport, env: Mapping[str, str]) -> Result:
        params: dict[str, object] = {"filter[searchTerm]": q.topic,
                                     "page[size]": min(250, max(5, q.limit))}
        if q.since:
            params["filter[postedDate][ge]"] = q.since
        data, call = self._get(http, self.URL, params=params,
                               headers={"X-Api-Key": self.key(env)})
        _expect(data, dict, call)
        records = []
        for doc in _maps(data.get("data"))[: q.limit]:
            attrs = _map(doc.get("attributes"))
            doc_id = _str(doc.get("id"))
            details = {k: attrs[k] for k in ("agencyId", "docketId", "frDocNum",
                                               "commentEndDate") if attrs.get(k)}
            records.append(self._trace(
                Record(
                    source=self.name,
                    kind="regulatory",
                    title=_str(attrs.get("title")) or "Untitled",
                    url=f"https://www.regulations.gov/document/{doc_id}",
                    summary=_str(attrs.get("summary")),
                    published_at=_str(attrs.get("postedDate"))[:10],
                    jurisdiction="federal",
                    identifier=doc_id,
                    status=_str(attrs.get("documentType")),
                    details=details,
                ),
                call,
                doc,
            ))
        return records, []


# --- Courts ------------------------------------------------------------------


class CourtListener(Adapter):
    name = "courtlistener"
    service = "CourtListener REST API v4 search (Free Law Project)"
    docs = "https://www.courtlistener.com/help/api/rest/"
    terms = "Court opinions are public domain; CourtListener terms of use apply"
    scopes = frozenset({"all", "federal", "policy"})
    URL = "https://www.courtlistener.com/api/rest/v4/search/"

    def search(self, q: Query, http: Transport, env: Mapping[str, str]) -> Result:
        params: dict[str, object] = {"q": q.topic, "type": "o", "order_by": "score desc",
                                     "page_size": q.limit}
        if q.since:
            params["filed_after"] = q.since
        data, call = self._get(http, self.URL, params=params)
        _expect(data, dict, call)
        records = []
        for case in _maps(data.get("results"))[: q.limit]:
            snippet = _str(case.get("snippet"))
            opinions = _maps(case.get("opinions"))
            if not snippet and opinions:
                snippet = _str(opinions[0].get("snippet"))
            records.append(self._trace(
                Record(
                    source=self.name,
                    kind="opinion",
                    title=_str(case.get("caseName")) or "Unknown case",
                    url=f"https://www.courtlistener.com{_str(case.get('absolute_url'))}",
                    summary=snippet.replace("<mark>", "").replace("</mark>", ""),
                    published_at=_str(case.get("dateFiled")),
                    identifier=_str(case.get("docketNumber")),
                    details={"court": case.get("court")} if case.get("court") else {},
                ),
                call,
                case,
            ))
        return records, []


# --- State legislation -------------------------------------------------------


class OpenStates(Adapter):
    name = "openstates"
    service = "Open States API v3 (Plural)"
    docs = "https://docs.openstates.org/api-v3/"
    terms = "Public domain dedication unless noted; attribution requested"
    env = "OPENSTATES_API_KEY"
    scopes = frozenset({"all", "state", "policy"})
    URL = "https://v3.openstates.org/bills"
    PAGE_SIZE = 20  # the API rejects per_page > 20
    MAX_PAGES = 5

    def search(self, q: Query, http: Transport, env: Mapping[str, str]) -> Result:
        records: list[Record] = []
        errors: list[SourceError] = []
        for state in q.scope.states or (None,):
            found, failed = self._search_one(q, http, env, state)
            records.extend(found)
            errors.extend(failed)
        if errors and not records:
            raise errors[0]
        return records, errors

    def _search_one(self, q: Query, http: Transport, env: Mapping[str, str],
                    state: str | None) -> Result:
        params: dict[str, object] = {"q": q.topic, "per_page": self.PAGE_SIZE,
                                     "include": "sources", "sort": "latest_action_desc"}
        if state:
            params["jurisdiction"] = state.lower()
        if q.since:
            params["action_since"] = q.since
        pages = min(self.MAX_PAGES, -(-q.limit // self.PAGE_SIZE))
        records: list[Record] = []
        for page in range(1, pages + 1):
            try:
                data, call = self._get(http, self.URL, params={**params, "page": page},
                                       headers={"X-API-KEY": self.key(env)})
                _expect(data, dict, call)
            except SourceError as error:
                return records[: q.limit], [error]
            for bill in _maps(data.get("results")):
                records.append(self._trace(self._record(bill), call, bill))
            if page >= int(_map(data.get("pagination")).get("max_page") or 1):
                break
        return records[: q.limit], []

    def _record(self, bill: dict[str, object]) -> Record:
        jurisdiction = _map(bill.get("jurisdiction"))
        openstates_url = _str(bill.get("openstates_url"))
        code = re.search(r"/state:([a-z]{2})/", _str(jurisdiction.get("id"))) or re.search(
            r"openstates\.org/([a-z]{2})/", openstates_url
        )
        sources = _maps(bill.get("sources"))
        return Record(
            source=self.name,
            kind="bill",
            title=_str(bill.get("title")) or "No title",
            url=openstates_url,
            published_at=_str(bill.get("latest_action_date"))[:10],
            jurisdiction=f"state:{code.group(1).upper()}" if code else "",
            identifier=_str(bill.get("identifier")),
            status=_str(bill.get("latest_action_description")),
            official_url=_str(sources[0].get("url")) if sources else "",
            details={"session": _str(bill.get("session"))} if bill.get("session") else {},
        )


class LegiScan(Adapter):
    name = "legiscan"
    service = "LegiScan API getSearch"
    docs = "https://legiscan.com/legiscan"
    terms = "CC BY 4.0; credit LegiScan"
    env = "LEGISCAN_API_KEY"
    scopes = frozenset({"all", "state", "policy"})
    URL = "https://api.legiscan.com/"

    def search(self, q: Query, http: Transport, env: Mapping[str, str]) -> Result:
        records: list[Record] = []
        errors: list[SourceError] = []
        for state in q.scope.states or ("ALL",):
            try:
                records.extend(self._search_one(q, http, env, state))
            except SourceError as error:
                errors.append(error)
        if errors and not records:
            raise errors[0]
        return records, errors

    def _search_one(self, q: Query, http: Transport, env: Mapping[str, str],
                    state: str) -> list[Record]:
        params = {"key": self.key(env), "op": "getSearch", "state": state, "query": q.topic}
        data, call = self._get(http, self.URL, params=params, secret_params=frozenset({"key"}))
        _expect(data, dict, call)
        status = _str(data.get("status"))
        if status and status.upper() != "OK":
            alert = _str(_map(data.get("alert")).get("message"))
            raise SourceError(f"LegiScan status {status}{': ' + alert if alert else ''}", call)
        result = _map(data.get("searchresult"))
        records = []
        for key in sorted((k for k in result if k.isdigit()), key=int):
            bill = _map(result.get(key))
            date = _str(bill.get("last_action_date"))
            if q.since and date and date < q.since:
                continue
            code = _str(bill.get("state")) or state
            records.append(self._trace(
                Record(
                    source=self.name,
                    kind="bill",
                    title=_str(bill.get("title")) or "No title",
                    url=_str(bill.get("url")),
                    published_at=date,
                    jurisdiction=f"state:{code}" if code in STATE_FIPS else "",
                    identifier=_str(bill.get("bill_number")),
                    status=_str(bill.get("last_action")),
                    official_url=_str(bill.get("state_link")),
                    details={"bill_id": bill.get("bill_id")} if bill.get("bill_id") else {},
                ),
                call,
                bill,
            ))
            if len(records) >= q.limit:
                break
        return records


# --- Statistics, research, web ----------------------------------------------


class Census(Adapter):
    """ACS 5-year estimates. Topic words select variables; `since` does not apply."""

    name = "census"
    service = "U.S. Census Bureau Data API, ACS 5-year 2022"
    docs = "https://www.census.gov/data/developers/guidance/api-user-guide.html"
    terms = "U.S. government work, public domain; not endorsed by the Census Bureau"
    scopes = frozenset({"all", "federal", "state"})
    URL = "https://api.census.gov/data/2022/acs/acs5"
    VARIABLES: ClassVar[dict[str, str]] = {
        "population": "B01003_001E", "median_age": "B01002_001E", "income": "B19013_001E",
        "poverty": "B17001_002E", "gini": "B19083_001E", "housing": "B25001_001E",
        "median_rent": "B25064_001E", "homeownership": "B25003_002E",
        "vacancy": "B25002_003E", "education": "B15003_022E", "high_school": "B15003_017E",
        "unemployment": "B23025_005E", "labor_force": "B23025_002E",
        "uninsured": "B27010_017E", "insurance": "B27010_002E", "disability": "B18101_001E",
    }
    DEFAULT = ("population", "income", "poverty")

    def available(self, env: Mapping[str, str]) -> bool:
        return True  # CENSUS_API_KEY is optional; it raises the rate limit

    def search(self, q: Query, http: Transport, env: Mapping[str, str]) -> Result:
        topic = q.topic.lower()
        labels = [label for label in self.VARIABLES if label in topic][:5] or list(self.DEFAULT)
        variables = [self.VARIABLES[label] for label in labels]
        geography = (
            "state:" + ",".join(STATE_FIPS[s] for s in q.scope.states)
            if q.scope.states else "us:*"
        )
        params: dict[str, object] = {"get": ",".join(["NAME", *variables]), "for": geography}
        if env.get("CENSUS_API_KEY"):
            params["key"] = env["CENSUS_API_KEY"]
        data, call = self._get(http, self.URL, params=params, secret_params=frozenset({"key"}))
        _expect(data, list, call)
        if len(data) < 2:
            raise SourceError("no Census rows returned", call)
        header = [_str(h) for h in data[0]] if isinstance(data[0], list) else []
        records = []
        for row in [r for r in data[1:] if isinstance(r, list)][: q.limit]:
            values = {label: row[i + 1] for i, label in enumerate(labels) if i + 1 < len(row)}
            item = dict(zip(header, row)) if header else row
            fips = _str(_map(item).get("state"))
            state = next((code for code, value in STATE_FIPS.items() if value == fips), "")
            records.append(self._trace(
                Record(
                    source=self.name,
                    kind="statistic",
                    title=_str(row[0]),
                    url=f"{self.URL}?get={params['get']}&for={geography}",
                    summary=" | ".join(f"{k}: {v}" for k, v in values.items()),
                    published_at="2022",
                    jurisdiction=f"state:{state}" if state else "federal",
                    details={"variables": dict(zip(labels, variables)), "values": values},
                ),
                call,
                item,
            ))
        return records, []


class SemanticScholar(Adapter):
    name = "semantic_scholar"
    service = "Semantic Scholar Academic Graph API (Ai2)"
    docs = "https://api.semanticscholar.org/api-docs/graph"
    terms = "Semantic Scholar API License Agreement; abstracts belong to publishers"
    scopes = frozenset({"all", "federal", "state"})
    URL = "https://api.semanticscholar.org/graph/v1/paper/search"

    def search(self, q: Query, http: Transport, env: Mapping[str, str]) -> Result:
        params: dict[str, object] = {
            "query": q.topic,
            "limit": min(100, q.limit),
            "fields": "title,abstract,year,publicationDate,citationCount,url,authors,externalIds",
        }
        if q.since:
            params["publicationDateOrYear"] = f"{q.since}:"
        data, call = self._get(http, self.URL, params=params)
        _expect(data, dict, call)
        records = []
        for paper in _maps(data.get("data"))[: q.limit]:
            authors = [_str(a.get("name")) for a in _maps(paper.get("authors")) if a.get("name")]
            doi = _str(_map(paper.get("externalIds")).get("DOI"))
            citations = paper.get("citationCount")
            records.append(self._trace(
                Record(
                    source=self.name,
                    kind="paper",
                    title=_str(paper.get("title")) or "Untitled",
                    url=_str(paper.get("url")),
                    summary=_str(paper.get("abstract")),
                    published_at=_str(paper.get("publicationDate") or paper.get("year")),
                    identifier=doi,
                    official_url=f"https://doi.org/{doi}" if doi else "",
                    citations=citations if isinstance(citations, int) else None,
                    details={"authors": authors} if authors else {},
                ),
                call,
                paper,
            ))
        return records, []


class Exa(Adapter):
    name = "exa"
    service = "Exa search API (web)"
    docs = "https://docs.exa.ai/reference/search"
    terms = "Exa terms of service; page content belongs to its publishers"
    env = "EXA_API_KEY"
    scopes = frozenset({"all", "federal", "state", "news"})
    URL = "https://api.exa.ai/search"

    def search(self, q: Query, http: Transport, env: Mapping[str, str]) -> Result:
        body: dict[str, object] = {"query": q.topic, "type": "auto", "numResults": q.limit,
                                   "contents": {"text": {"maxCharacters": 1500}}}
        if q.since:
            body["startPublishedDate"] = q.since
        data, call = http.request(self.name, "POST", self.URL, json_body=body,
                                  headers={"x-api-key": self.key(env)})
        _expect(data, dict, call)
        records = [
            self._trace(
                Record(
                    source=self.name,
                    kind="web",
                    title=_str(item.get("title")) or "Untitled",
                    url=_str(item.get("url")),
                    summary=_str(item.get("summary") or item.get("text"))[:1500],
                    published_at=_str(item.get("publishedDate"))[:10],
                    details={"author": item.get("author")} if item.get("author") else {},
                ),
                call,
                item,
            )
            for item in _maps(data.get("results"))[: q.limit]
        ]
        return records, []


ADAPTERS: dict[str, Adapter] = {
    adapter.name: adapter
    for adapter in (
        GovInfo(), FederalRegister(), RegulationsGov(), CourtListener(), OpenStates(),
        LegiScan(), Census(), SemanticScholar(), Exa(),
    )
}
