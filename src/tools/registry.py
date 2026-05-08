from collections.abc import Mapping

from scopes import Scope

from .declarations import get_available_tool_names
from .models import Finding
from .implementations import (
    AcademicSearch,
    CensusSearch,
    CongressSearch,
    CourtSearch,
    FederalRegisterSearch,
    RegulationsSearch,
    StateLegislationSearch,
    WebSearch,
)


class ToolRegistry:

    def __init__(self, scope: Scope):
        self.scope = scope
        all_tools = {
            "web_search": WebSearch(),
            "academic_search": AcademicSearch(),
            "census_search": CensusSearch(),
            "congress_search": CongressSearch(),
            "federal_register_search": FederalRegisterSearch(),
            "regulations_search": RegulationsSearch(),
            "court_search": CourtSearch(),
            "state_legislation_search": StateLegislationSearch(scope.get("states", [])),
        }
        self._tools = {
            name: tool
            for name, tool in all_tools.items()
            if name in get_available_tool_names(scope)
        }

    def execute(self, tool_name: str, args: Mapping[str, object]) -> tuple[list[Finding], str]:
        """Return (findings, formatted_str) where formatted_str is the markdown text for Gemini."""
        tool = self._tools.get(tool_name)
        if not tool:
            return [], f"Unknown tool: {tool_name}"

        result = tool.execute(**args)

        errors = [f"Tool error: {message}" for message in result.errors]
        findings = result.findings

        if not findings:
            if errors:
                return [], "\n".join(errors)
            return [], "No results."

        formatted = []
        for f in findings:
            date_str = f" ({f.date})" if f.date else ""
            cite_str = f" [{f.citations} citations]" if f.citations else ""
            formatted.append(f"**{f.title}**{date_str}{cite_str}\n{f.snippet}\nSource: {f.url}")

        if errors:
            formatted.extend(errors)

        return findings, "\n\n---\n\n".join(formatted)
