from __future__ import annotations

from typing import Literal, TypedDict

ScopeType = Literal["federal", "state", "all", "news", "policy"]

VALID_COMPARE_TARGETS = {"federal", "news", "policy"}
VALID_STATES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA",
    "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD",
    "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ",
    "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC",
    "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY", "DC", "PR",
}


Scope = TypedDict("Scope", {"type": ScopeType, "states": list[str]})


DEFAULT_SCOPE: Scope = {"type": "all", "states": []}


def parse_scope(scope_str: str) -> Scope:
    if scope_str in {"federal", "all", "news", "policy"}:
        return {"type": scope_str, "states": []}
    if scope_str.startswith("state:"):
        states = [state.strip().upper() for state in scope_str[6:].split(",")]
        invalid = [state for state in states if state not in VALID_STATES]
        if invalid:
            raise ValueError(f"Invalid state codes: {', '.join(invalid)}")
        return {"type": "state", "states": states}
    raise ValueError(
        f"Invalid scope: {scope_str}. Use 'federal', 'all', 'news', 'policy', or 'state:CA,NY'"
    )


def parse_compare(compare_str: str) -> list[str]:
    targets = [target.strip() for target in compare_str.split(",")]
    for target in targets:
        if target not in VALID_COMPARE_TARGETS and target.upper() not in VALID_STATES:
            raise ValueError(f"Invalid compare target: {target}")
    return targets


def compare_target_scope(target: str) -> Scope:
    if target in VALID_COMPARE_TARGETS:
        return {"type": target, "states": []}
    return {"type": "state", "states": [target.upper()]}


def scope_label(scope: Scope) -> str:
    """Canonical string used in JSON output and markdown headers (e.g. 'state:CA', 'federal + state')."""
    if scope["type"] == "state":
        return f"state:{','.join(scope['states'])}"
    if scope["type"] == "all":
        return "federal + state"
    return scope["type"]
