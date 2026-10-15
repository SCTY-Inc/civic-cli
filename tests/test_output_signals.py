"""Unit tests for output_signals — no network, pure formatting."""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

spec = importlib.util.spec_from_file_location(
    "output_signals", SRC / "output_signals.py"
)
module = importlib.util.module_from_spec(spec)
assert spec.loader is not None
sys.modules[spec.name] = module
spec.loader.exec_module(module)

from tools.models import Finding, ResearchResults  # noqa: E402


def test_stable_id_is_url_deterministic():
    a = module._stable_id("CONGRESS", "https://www.congress.gov/bill/118th-congress/house-bill/2406", "t")
    b = module._stable_id("CONGRESS", "https://congress.gov/bill/118th-congress/house-bill/2406", "t")
    # `www.` is stripped so the IDs collapse to the same canonical form.
    assert a == b
    assert a.startswith("congress-")


def test_congress_extractor_pulls_identifier_and_status():
    f = Finding(
        title="HR2406: Credit for Caring Act of 2024",
        snippet="Congress 118 | Status: Referred to Ways and Means",
        url="https://www.congress.gov/bill/118th-congress/house-bill/2406",
        date="2024-04-12",
        source_type="CONGRESS",
    )
    sig = module.signal_from_finding(f)
    assert sig["identifier"] == "HR 2406"
    assert sig["title"] == "Credit for Caring Act of 2024"
    assert sig["status"] == "Referred to Ways and Means"
    assert sig["congress"] == "118"
    assert sig["jurisdiction"] == "federal"
    assert sig["source_system"] == "congress.gov"
    assert sig["source_tool"] == "congress_search"
    assert sig["signal_kind"] == "bill_referred"
    assert sig["pending"] is True


def test_federal_register_extractor_pulls_document_id():
    f = Finding(
        title="CMS final rule on HCBS",
        snippet="Type: Rule | Agency: CMS",
        url="https://www.federalregister.gov/documents/2024/03/15/2024-05432/cms-rule",
        date="2024-03-15",
        source_type="FED_REGISTER",
    )
    sig = module.signal_from_finding(f)
    assert sig["identifier"] == "2024-05432"
    assert sig["jurisdiction"] == "federal"
    assert sig["status"] == "Rule"
    assert sig["signal_kind"] == "final_rule"
    assert sig["pending"] is False


def test_state_leg_extractor_infers_state():
    f = Finding(
        title="SB 525: In-home support wages",
        snippet="State: California | Session: 2023-2024 | Latest: Passed Assembly",
        url="https://openstates.org/CA/bills/20232024/SB525/",
        date="2023-09-12",
        source_type="STATE_LEG",
        jurisdiction="state:CA",
        official_url="https://leginfo.legislature.ca.gov/sb525",
    )
    sig = module.signal_from_finding(f)
    assert sig["jurisdiction"] == "state:CA"
    assert sig["identifier"] == "SB525"
    assert sig["status"] == "Passed Assembly"
    assert sig["signal_kind"] == "bill_advanced"
    assert sig["pending"] is True
    assert sig["official_url"] == "https://leginfo.legislature.ca.gov/sb525"
    assert sig["openstates_url"] == "https://openstates.org/CA/bills/20232024/SB525/"


@pytest.mark.parametrize(
    ("latest", "kind", "pending"),
    [
        ("Effective 10/9/26", "bill_enacted", False),
        ("Signed by the Governor, Chapter 179 of the Acts of 2026", "bill_enacted", False),
        ("Act 198, 07/07/2026 (Gov. Msg. No. 1300).", "bill_enacted", False),
        ("Public Act . . . . . . . . . 104-0612", "bill_enacted", False),
        ("Chapter No. 2026-165", "bill_enacted", False),
        ("06/23/2026 Signed by Governor", "bill_enacted", False),
        ("Vetoed by the Governor", "bill_vetoed", False),
        ("Referred to committee", "bill_referred", True),
        ("Laid on the table", "bill_update", True),
    ],
)
def test_state_status_comes_from_latest_action(latest, kind, pending):
    f = Finding(
        title="HB 1: T",
        snippet=f"State: Ohio | Session: 136 | Latest: {latest}",
        url="https://openstates.org/oh/bills/136/HB1/",
        date="2026-10-09",
        source_type="STATE_LEG",
        jurisdiction="state:OH",
    )
    sig = module.signal_from_finding(f)
    assert sig["signal_kind"] == kind
    assert sig["pending"] is pending


def test_legiscan_extractor_infers_state():
    f = Finding(
        title="AB2324: Vocational education: youth caregivers.",
        snippet="State: CA | Latest: Re-referred to Com. on APPR.",
        url="https://legiscan.com/CA/bill/AB2324/2025",
        date="2026-04-21",
        source_type="LEGISCAN",
        jurisdiction="state:CA",
    )
    sig = module.signal_from_finding(f)
    assert sig["jurisdiction"] == "state:CA"
    assert sig["identifier"] == "AB2324"
    assert sig["status"] == "Re-referred to Com. on APPR."
    assert sig["signal_kind"] == "bill_referred"
    assert sig["pending"] is True
    assert sig["source_system"] == "legiscan.com"
    assert sig["source_tool"] == "state_legislation_search"


def test_emit_signals_dedupes_by_signal_id_and_counts():
    results = ResearchResults()
    f1 = Finding(
        title="HR2406: Credit for Caring Act",
        snippet="Congress 118 | Status: Introduced",
        url="https://www.congress.gov/bill/118/2406",
        date="2024-04-12",
        source_type="CONGRESS",
    )
    results.add(f1, "congress_search")
    # Exact duplicate signal — should be dropped.
    results.add(f1, "congress_search")
    f2 = Finding(
        title="CMS rule on HCBS",
        snippet="Type: Rule | Agency: CMS",
        url="https://www.federalregister.gov/documents/2024/03/15/2024-05432/cms",
        date="2024-03-15",
        source_type="FED_REGISTER",
    )
    results.add(f2, "federal_register_search")

    out = json.loads(module.emit_signals("caregiver policy", "caregiver-federal", "federal", results))
    assert out["schema_version"] == 1
    assert out["preset"] == "caregiver-federal"
    assert out["counts"]["signals"] == 2
    assert out["counts"]["by_source_type"] == {"CONGRESS": 1, "FED_REGISTER": 1}
    assert {s["source_type"] for s in out["signals"]} == {"CONGRESS", "FED_REGISTER"}


def test_emit_signals_keeps_distinct_bill_movements_for_same_url():
    results = ResearchResults()
    url = "https://www.congress.gov/bill/118th-congress/house-bill/2406"
    results.add(
        Finding(
            title="HR2406: Credit for Caring Act",
            snippet="Congress 118 | Status: Introduced",
            url=url,
            date="2024-04-12",
            source_type="CONGRESS",
        ),
        "congress_search",
    )
    results.add(
        Finding(
            title="HR2406: Credit for Caring Act",
            snippet="Congress 118 | Status: Passed House",
            url=url,
            date="2024-05-02",
            source_type="CONGRESS",
        ),
        "congress_search",
    )

    out = json.loads(module.emit_signals("caregiver policy", "pulse-policy-weekly", "policy", results))
    assert out["counts"]["signals"] == 2
    assert [s["signal_kind"] for s in out["signals"]] == ["bill_introduced", "bill_advanced"]


def test_academic_preserves_citations():
    f = Finding(
        title="Paid leave and caregiver wellbeing",
        snippet="Meta-analysis…",
        url="https://www.semanticscholar.org/paper/abc",
        date="2023",
        source_type="ACADEMIC",
        citations=42,
    )
    sig = module.signal_from_finding(f)
    assert sig["citations"] == 42
    assert "identifier" not in sig
    assert "jurisdiction" not in sig


def test_emit_signals_includes_adapter_errors():
    results = ResearchResults()
    results.errors.append({"source_tool": "state_legislation_search", "error": "OpenStates API error (400): Bad Request"})
    envelope = json.loads(module.emit_signals("t", None, "policy", results))
    assert envelope["errors"] == results.errors
    assert envelope["signals"] == []


def test_emit_signals_errors_empty_by_default():
    envelope = json.loads(module.emit_signals("t", None, "policy", ResearchResults()))
    assert envelope["errors"] == []
