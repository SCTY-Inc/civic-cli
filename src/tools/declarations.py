import os
from collections.abc import Mapping

from google.genai import types

from scopes import Scope

# TOOL_SPECS drives both Gemini function declarations and scope filtering — single source of truth.
TOOL_SPECS = {
    "web_search": {
        "description": "Search the web for current news, articles, and general policy information",
        "params": {"query": ("STRING", "Search query")},
    },
    "academic_search": {
        "description": "Search academic papers and research studies on Semantic Scholar (200M+ papers)",
        "params": {"query": ("STRING", "Research topic or keywords")},
    },
    "census_search": {
        "description": "Search US Census data for demographics, population, housing, and economic statistics",
        "params": {
            "topic": ("STRING", "Data topic (e.g., population, income, housing, education)"),
            "geography": ("STRING", "Geographic level: us, state:XX, or county:XXXXX"),
        },
        "required": ["topic"],
    },
    "congress_search": {
        "description": "Search federal legislation on Congress.gov - bills, resolutions, amendments",
        "params": {"query": ("STRING", "Legislation search terms")},
    },
    "federal_register_search": {
        "description": "Search Federal Register for rules, proposed rules, and agency notices",
        "params": {"query": ("STRING", "Regulation search terms")},
    },
    "court_search": {
        "description": "Search federal case law and court opinions on CourtListener",
        "params": {"query": ("STRING", "Legal search terms, case names, or topics")},
    },
    "regulations_search": {
        "description": "Search Regulations.gov for public comments, proposed rules, and regulatory dockets",
        "params": {"query": ("STRING", "Regulation or docket search terms")},
    },
    "state_legislation_search": {
        "description": "Search state legislation via OpenStates, with LegiScan fallback for single-state searches",
        "params": {
            "query": ("STRING", "Legislation search terms"),
            "state": ("STRING", "State code (e.g., CA, NY)"),
        },
        "required": ["query"],
    },
}

TOOL_ENV_VARS = {
    "web_search": "EXA_API_KEY",
    "congress_search": "CONGRESS_GOV_API_KEY",
    "regulations_search": "REGULATIONS_GOV_API_KEY",
    "state_legislation_search": "OPENSTATES_API_KEY",
}

HARD_REQUIRED_TOOL_ENV_VARS = {
    "web_search": "EXA_API_KEY",
}

SCOPE_TOOLS = {
    "news": ["web_search"],
    "policy": [
        "congress_search",
        "federal_register_search",
        "regulations_search",
        "court_search",
        "state_legislation_search",
    ],
    "federal": [
        "web_search",
        "academic_search",
        "census_search",
        "congress_search",
        "federal_register_search",
        "regulations_search",
        "court_search",
    ],
    "state": ["web_search", "academic_search", "census_search", "state_legislation_search"],
    "all": [
        "web_search",
        "academic_search",
        "census_search",
        "congress_search",
        "federal_register_search",
        "regulations_search",
        "court_search",
        "state_legislation_search",
    ],
}


def get_tool_names(scope: Scope) -> list[str]:
    return list(SCOPE_TOOLS.get(scope["type"], SCOPE_TOOLS["all"]))


def get_available_tool_names(
    scope: Scope,
    env: Mapping[str, str] | None = None,
) -> list[str]:
    """Filter to tools whose API keys are present.

    Keeps Gemini's tool list aligned with what can actually execute. LegiScan
    is only included for single-state scopes (situational fallback for OpenStates).
    """
    env = env or os.environ
    available = []
    for name in get_tool_names(scope):
        if name == "state_legislation_search":
            has_openstates = bool(env.get("OPENSTATES_API_KEY"))
            has_legiscan = bool(env.get("LEGISCAN_API_KEY"))
            single_state = scope["type"] == "state" and len(scope.get("states", [])) == 1
            if not has_openstates and not (has_legiscan and single_state):
                continue
            available.append(name)
            continue

        required_env = TOOL_ENV_VARS.get(name)
        if required_env and not env.get(required_env):
            continue
        available.append(name)
    return available


def _make_declaration(name: str, description_suffix: str = "") -> types.FunctionDeclaration:
    spec = TOOL_SPECS[name]
    description = spec["description"]
    if description_suffix:
        description = f"{description} ({description_suffix})"

    properties = {}
    for param_name, (param_type, param_desc) in spec["params"].items():
        properties[param_name] = types.Schema(type=param_type, description=param_desc)

    required = spec.get("required", list(spec["params"].keys())[:1])

    return types.FunctionDeclaration(
        name=name,
        description=description,
        parameters=types.Schema(type="OBJECT", properties=properties, required=required),
    )


def get_tool_declarations(scope: Scope) -> list[types.FunctionDeclaration]:
    scope_type = scope["type"]
    tool_names = get_available_tool_names(scope)

    declarations = []
    for name in tool_names:
        suffix = ""
        if name == "state_legislation_search" and scope_type == "state":
            suffix = ", ".join(scope.get("states", []))
        elif name == "state_legislation_search" and scope_type == "all":
            suffix = "all states"
        declarations.append(_make_declaration(name, suffix))

    return declarations
