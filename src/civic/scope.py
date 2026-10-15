"""Search scope: which jurisdictions a query covers and which sources it selects."""

from __future__ import annotations

from dataclasses import dataclass, field

STATE_FIPS = {
    "AL": "01", "AK": "02", "AZ": "04", "AR": "05", "CA": "06", "CO": "08",
    "CT": "09", "DE": "10", "DC": "11", "FL": "12", "GA": "13", "HI": "15",
    "ID": "16", "IL": "17", "IN": "18", "IA": "19", "KS": "20", "KY": "21",
    "LA": "22", "ME": "23", "MD": "24", "MA": "25", "MI": "26", "MN": "27",
    "MS": "28", "MO": "29", "MT": "30", "NE": "31", "NV": "32", "NH": "33",
    "NJ": "34", "NM": "35", "NY": "36", "NC": "37", "ND": "38", "OH": "39",
    "OK": "40", "OR": "41", "PA": "42", "PR": "72", "RI": "44", "SC": "45",
    "SD": "46", "TN": "47", "TX": "48", "UT": "49", "VT": "50", "VA": "51",
    "WA": "53", "WV": "54", "WI": "55", "WY": "56",
}

KINDS = ("all", "federal", "state", "policy", "news")


@dataclass(frozen=True)
class Scope:
    kind: str = "all"
    states: tuple[str, ...] = field(default_factory=tuple)

    def __str__(self) -> str:
        return f"state:{','.join(self.states)}" if self.kind == "state" else self.kind


def parse_scope(value: str | Scope) -> Scope:
    """Parse `all`, `federal`, `policy`, `news`, or `state:CA,NY`."""
    if isinstance(value, Scope):
        return value
    text = value.strip()
    if text in KINDS and text != "state":
        return Scope(text)
    if text.startswith("state:"):
        states = tuple(code.strip().upper() for code in text[6:].split(",") if code.strip())
        invalid = [code for code in states if code not in STATE_FIPS]
        if not states or invalid:
            raise ValueError(f"Invalid state codes: {', '.join(invalid) or '(none)'}")
        return Scope("state", states)
    raise ValueError(f"Invalid scope: {value}. Use all, federal, policy, news, or state:CA,NY")
