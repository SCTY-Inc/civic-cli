from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Finding:
    title: str
    snippet: str
    url: str
    date: str = ""
    source_type: str = ""
    citations: int = 0

    def to_dict(self) -> dict[str, object]:
        return {
            "title": self.title,
            "snippet": self.snippet,
            "url": self.url,
            "date": self.date,
            "source_type": self.source_type,
            "citations": self.citations,
        }


@dataclass
class ToolResult:
    findings: list[Finding] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


@dataclass
class ResearchResults:
    findings: list[Finding] = field(default_factory=list)
    tool_usage: dict[str, int] = field(default_factory=dict)

    def add(self, finding: Finding, tool_name: str):
        self.findings.append(finding)
        self.tool_usage[tool_name] = self.tool_usage.get(tool_name, 0) + 1

    def confidence_score(self) -> tuple[str, str]:
        if not self.findings:
            return "LOW", "No findings"

        source_types = {f.source_type for f in self.findings if f.source_type}
        diversity = min(len(source_types) / 5, 1.0)

        current_year = datetime.now().year
        recency_scores = []
        for f in self.findings:
            if f.date:
                try:
                    year = int(f.date[:4])
                    age = current_year - year
                    recency_scores.append(max(0, 1 - (age * 0.2)))
                except (ValueError, IndexError):
                    pass
        recency = sum(recency_scores) / len(recency_scores) if recency_scores else 0.5

        cited = [f for f in self.findings if f.citations > 0]
        citation_score = min(len(cited) / 3, 1.0) if cited else 0.0

        score = (diversity * 0.4) + (recency * 0.3) + (citation_score * 0.3)

        if score >= 0.7:
            level, dots = "HIGH", "●●●●●"
        elif score >= 0.4:
            level, dots = "MEDIUM", "●●●○○"
        else:
            level, dots = "LOW", "●○○○○"

        return level, f"{dots} {level} — {len(source_types)} source types, {len(self.findings)} findings"

    def to_dict(self) -> dict[str, object]:
        level, detail = self.confidence_score()
        return {
            "confidence": {"level": level, "detail": detail},
            "findings": [f.to_dict() for f in self.findings],
            "tool_usage": self.tool_usage,
        }

    def _group_by_source(self) -> dict[str, list[Finding]]:
        by_source: dict[str, list[Finding]] = {}
        for f in self.findings:
            by_source.setdefault(f.source_type, []).append(f)
        return by_source

    def to_appendix(self) -> str:
        by_source = self._group_by_source()
        _, confidence_str = self.confidence_score()
        parts = [f"## Appendix: Source Data\n\n**Research Confidence:** {confidence_str}\n"]

        for source, items in sorted(by_source.items()):
            parts.append(f"### {source} ({len(items)} results)")
            for i, f in enumerate(items, 1):
                date_str = f"[{f.date}] " if f.date else ""
                cite_str = f" ({f.citations} citations)" if f.citations else ""
                parts.append(f"{i}. {date_str}**{f.title}**{cite_str}")
                parts.append(f"   {f.url}")
            parts.append("")
        return "\n".join(parts)


@dataclass
class ResearchOutput:
    text: str
    results: ResearchResults
    scope_label: str = ""
