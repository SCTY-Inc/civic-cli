import os
import random
import time
from concurrent.futures import ThreadPoolExecutor

from google import genai
from google.genai import errors, types
from rich.console import Console

from prompts import COMPARATOR, RESEARCHER, REVIEWER, WRITER
from scopes import DEFAULT_SCOPE, Scope, compare_target_scope, scope_label
from tools import Finding, ResearchOutput, ResearchResults, ToolRegistry, get_tool_declarations

# google-genai args type: dict[str, Any] per FunctionCall.args field definition
_FunctionCallArgs = dict[str, object]

console = Console()

MODEL = os.getenv("CIVIC_MODEL", "gemini-3.1-flash-lite-preview")
MAX_ITERATIONS = int(os.getenv("CIVIC_MAX_ITERATIONS", "15"))
MAX_RETRIES = int(os.getenv("CIVIC_MAX_RETRIES", "4"))


def _retry_delay_seconds(attempt: int) -> float:
    return (30 * (2**attempt)) + random.uniform(0, 5)


def _generate_with_retry(client: genai.Client, **kwargs) -> types.GenerateContentResponse:
    """Retry generate_content on 429 with exponential backoff."""
    for attempt in range(MAX_RETRIES):
        try:
            return client.models.generate_content(**kwargs)
        except errors.APIError as error:
            if error.code != 429 or attempt == MAX_RETRIES - 1:
                raise
            delay = _retry_delay_seconds(attempt)
            console.print(
                f"[yellow]Gemini 429 (attempt {attempt + 1}/{MAX_RETRIES}); "
                f"retrying in {delay:.0f}s[/]"
            )
            time.sleep(delay)
    raise RuntimeError("_generate_with_retry: exhausted retries without returning or raising")

_client: genai.Client | None = None


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        api_key = os.getenv("GOOGLE_API_KEY")
        if not api_key:
            raise ValueError("GOOGLE_API_KEY not set in environment")
        _client = genai.Client(api_key=api_key)
    return _client


def _extract_text(response: types.GenerateContentResponse) -> str:
    if not response.candidates:
        return ""
    parts = []
    for part in response.candidates[0].content.parts:
        if part.text:
            parts.append(part.text)
    return "".join(parts)


def _scope_context(scope: Scope) -> tuple[str, str]:
    label = scope_label(scope)
    if scope["type"] == "federal":
        return "\n\nFocus on FEDERAL policy only (Congress, federal agencies).", label
    if scope["type"] == "state":
        states = ", ".join(scope["states"])
        return f"\n\nFocus on STATE policy only for: {states}", label
    if scope["type"] == "news":
        return "\n\nFocus on current news, public commentary, and recent coverage only.", label
    if scope["type"] == "policy":
        return "\n\nFocus on legislation, regulation, and case law only.", label
    return "\n\nSearch BOTH federal and state policy sources.", label


def research(
    topic: str,
    questions: list[str] | None = None,
    scope: Scope | None = None,
    verbose: bool = False
) -> ResearchOutput:
    client = _get_client()
    scope = scope or DEFAULT_SCOPE
    results = ResearchResults()

    context = f"Research this policy topic: {topic}"
    if questions:
        context += "\n\nSpecific questions to address:\n"
        context += "\n".join(f"- {q}" for q in questions)

    scope_context, label = _scope_context(scope)
    context += scope_context

    tool_declarations = get_tool_declarations(scope)
    tool_registry = ToolRegistry(scope)

    contents = [types.Content(role="user", parts=[types.Part(text=context)])]
    tools = [types.Tool(function_declarations=tool_declarations)]

    def _run_tool(fc: types.FunctionCall) -> tuple[types.FunctionCall, tuple[list[Finding], str]]:
        tool_args: _FunctionCallArgs = dict(fc.args) if fc.args else {}
        if verbose:
            query = tool_args.get("query", tool_args.get("topic", ""))
            console.print(f"  [dim]{fc.name}: {query}[/]")
        return fc, tool_registry.execute(fc.name, tool_args)

    with ThreadPoolExecutor(max_workers=8) as pool:
        for _ in range(MAX_ITERATIONS):
            response = _generate_with_retry(
                client,
                model=MODEL,
                contents=contents,
                config=types.GenerateContentConfig(
                    system_instruction=RESEARCHER,
                    tools=tools,
                    max_output_tokens=4096,
                ),
            )

            if not response.candidates or not response.candidates[0].content.parts:
                return ResearchOutput(text="", results=results, scope_label=label)

            func_calls: list[types.FunctionCall] = [
                part.function_call
                for part in response.candidates[0].content.parts
                if part.function_call is not None
            ]

            if not func_calls:
                return ResearchOutput(
                    text=_extract_text(response),
                    results=results,
                    scope_label=label,
                )

            function_responses = []
            futures = [pool.submit(_run_tool, fc) for fc in func_calls]
            for future in futures:
                fc, (findings, formatted) = future.result()
                for f in findings:
                    results.add(f, fc.name)
                function_responses.append(
                    types.Part(
                        function_response=types.FunctionResponse(
                            name=fc.name,
                            response={"result": formatted},
                        )
                    )
                )

            contents.append(response.candidates[0].content)
            contents.append(types.Content(role="user", parts=function_responses))

    return ResearchOutput(
        text=_extract_text(response),
        results=results,
        scope_label=label,
    )


def write_brief(topic: str, research_output: ResearchOutput, include_appendix: bool = True) -> str:
    client = _get_client()

    response = _generate_with_retry(
        client,
        model=MODEL,
        contents=f"Write a policy brief on: {topic}\n\nBased on this research:\n\n{research_output.text}",
        config=types.GenerateContentConfig(
            system_instruction=WRITER,
            max_output_tokens=8192,
        ),
    )

    brief = _extract_text(response)
    if include_appendix and research_output.results.findings:
        brief += "\n\n---\n\n" + research_output.results.to_appendix()
    return brief


def review(draft: str) -> str:
    client = _get_client()

    response = _generate_with_retry(
        client,
        model=MODEL,
        contents=f"Review and refine this policy brief:\n\n{draft}",
        config=types.GenerateContentConfig(
            system_instruction=REVIEWER,
            max_output_tokens=8192,
        ),
    )
    return _extract_text(response)


def compare_research(
    topic: str,
    targets: list[str],
    questions: list[str] | None = None,
    verbose: bool = False,
) -> list[ResearchOutput]:
    def _run(target: str) -> ResearchOutput:
        scope = compare_target_scope(target)
        if verbose:
            console.print(f"\n[bold]Researching: {target}[/]")
        return research(topic, questions, scope, verbose)

    with ThreadPoolExecutor(max_workers=min(len(targets), 4)) as pool:
        return list(pool.map(_run, targets))


def write_comparison(topic: str, outputs: list[ResearchOutput]) -> str:
    client = _get_client()

    sections = [f"## {o.scope_label.upper()}\n\n{o.text}" for o in outputs]
    combined = "\n\n---\n\n".join(sections)

    response = _generate_with_retry(
        client,
        model=MODEL,
        contents=f"Compare policy approaches on: {topic}\n\nResearch by jurisdiction:\n\n{combined}",
        config=types.GenerateContentConfig(
            system_instruction=COMPARATOR,
            max_output_tokens=8192,
        ),
    )

    comparison = _extract_text(response)
    all_findings = [f for o in outputs for f in o.results.findings]
    if all_findings:
        comparison += "\n\n---\n\n" + ResearchResults(findings=all_findings).to_appendix()
    return comparison
