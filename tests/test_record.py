import pytest

from civic.record import Record, classify_document_status, record_id, stable_id


@pytest.mark.parametrize(
    ("status", "movement", "pending"),
    [
        ("Effective 10/9/26", "bill_enacted", False),
        ("Signed by the Governor, Chapter 179 of the Acts of 2026", "bill_enacted", False),
        ("Act 198, 07/07/2026 (Gov. Msg. No. 1300).", "bill_enacted", False),
        ("Public Act . . . . . . . . . 104-0612", "bill_enacted", False),
        ("Chapter No. 2026-165", "bill_enacted", False),
        ("06/23/2026 Signed by Governor", "bill_enacted", False),
        ("Vetoed by the Governor", "bill_vetoed", False),
        ("Introduced in Senate", "bill_introduced", True),
        ("Referred to committee", "bill_referred", True),
        ("Re-referred to Com. on APPR.", "bill_referred", True),
        ("Engrossed in House", "bill_advanced", True),
        ("Laid on the table", "bill_update", True),
    ],
)
def test_bill_movement(status, movement, pending):
    record = Record(source="openstates", kind="bill", title="T", url="u", status=status)
    assert record.movement == (movement, pending)
    assert record.to_dict()["movement"] == movement
    assert record.to_dict()["pending"] is pending


@pytest.mark.parametrize(
    ("status", "movement", "pending"),
    [("Proposed Rule", "proposed_rule", True), ("Rule", "final_rule", False),
     ("Notice", "agency_notice", True), ("Supporting & Related Material", "docket_document", True),
     ("", None, None)],
)
def test_document_movement(status, movement, pending):
    assert classify_document_status(status) == (movement, pending)


def test_stable_id_is_deterministic():
    assert stable_id("federal_register", "https://www.federalregister.gov/d/1") == (
        "federal-register-federalregister-gov-d-1"
    )


def test_bill_ids_key_on_movement_so_later_actions_are_distinct():
    base = {"source": "openstates", "kind": "bill", "title": "T",
            "url": "https://openstates.org/ca/bills/1/",
            "jurisdiction": "state:CA", "identifier": "SB 525"}
    referred = Record(**base, status="Referred to committee", published_at="2026-01-01")
    enacted = Record(**base, status="Chaptered", published_at="2026-06-01")
    assert record_id(referred) != record_id(enacted)
    assert record_id(referred) == record_id(Record(**base, status="Referred to committee",
                                                   published_at="2026-01-01"))


def test_empty_optional_fields_are_omitted():
    out = Record(source="exa", kind="web", title="T", url="u").to_dict()
    assert set(out) == {"id", "source", "kind", "title", "url", "summary", "published_at", "provenance"}
