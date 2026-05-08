import argparse
import functools
import json
import os
import signal
import sys
import types as _stdlib_types
from collections.abc import Callable
from contextlib import nullcontext
from datetime import datetime
from pathlib import Path
from typing import TypedDict

import httpx
import tomllib
from dotenv import load_dotenv
from rich.console import Console

from _agent_cli import DoctorCheck, doctor_runner
from agents import compare_research, research, review, write_brief, write_comparison
from output import format_json, save_report
from output_signals import emit_signals
from scopes import Scope, compare_target_scope, parse_compare, parse_scope, scope_label
from tools import ResearchOutput, ResearchResults, get_tool_names
from tools.base import clear_cache, get_cache_stats, set_results_limit
from tools.declarations import HARD_REQUIRED_TOOL_ENV_VARS

__version__ = "0.6.0"

_NO_COLOR = bool(os.environ.get("NO_COLOR")) or not sys.stdout.isatty()
_FORCE_TERM = sys.stdout.isatty() and not os.environ.get("NO_COLOR")

console = Console(no_color=_NO_COLOR, force_terminal=_FORCE_TERM)
err_console = Console(stderr=True, no_color=_NO_COLOR, force_terminal=_FORCE_TERM)

# API key signup URLs surfaced by `civic doctor`.
API_KEY_SIGNUP = {
    "GOOGLE_API_KEY": "https://aistudio.google.com/apikey",
    "EXA_API_KEY": "https://dashboard.exa.ai/api-keys",
    "CONGRESS_GOV_API_KEY": "https://api.congress.gov/sign-up",
    "OPENSTATES_API_KEY": "https://openstates.org/accounts/register/",
    "LEGISCAN_API_KEY": "https://legiscan.com/legiscan",
    "REGULATIONS_GOV_API_KEY": "https://open.gsa.gov/api/regulationsgov/",
    "CENSUS_API_KEY": "https://api.census.gov/data/key_signup.html",
}

TOPICS_FILENAME = "topics.toml"


class TopicConfig(TypedDict, total=False):
    topic: str
    scope: str
    compare: str
    questions: list[str]
    output: str


def find_topics_file() -> Path | None:
    """Search cwd, all parent dirs, and the package directory for topics.toml."""
    candidates: list[Path] = []
    cwd = Path.cwd()
    candidates.extend(parent / TOPICS_FILENAME for parent in (cwd, *cwd.parents))
    candidates.append(Path(__file__).with_name(TOPICS_FILENAME))
    candidates.append(Path(__file__).resolve().parent.parent / TOPICS_FILENAME)

    seen: set[Path] = set()
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        if resolved.exists():
            return resolved
    return None


@functools.lru_cache(maxsize=1)
def load_topics() -> dict[str, TopicConfig]:
    topics_file = find_topics_file()
    if not topics_file:
        return {}
    with open(topics_file, "rb") as f:
        data = tomllib.load(f)
    return data.get("topics", {})


def get_topic(name: str) -> TopicConfig | None:
    return load_topics().get(name)


def _requested_tool_names(scope: Scope, compare: list[str] | None = None) -> list[str]:
    scopes = [compare_target_scope(target) for target in compare] if compare else [scope]
    names: list[str] = []
    seen: set[str] = set()
    for requested_scope in scopes:
        for tool_name in get_tool_names(requested_scope):
            if tool_name in seen:
                continue
            seen.add(tool_name)
            names.append(tool_name)
    return names


def check_env(scope: Scope, compare: list[str] | None = None) -> list[str]:
    required = ["GOOGLE_API_KEY"]
    for tool_name in _requested_tool_names(scope, compare):
        env_name = HARD_REQUIRED_TOOL_ENV_VARS.get(tool_name)
        if env_name and env_name not in required:
            required.append(env_name)
    return [name for name in required if not os.getenv(name)]


def _print_missing_env(missing: list[str]) -> None:
    err_console.print(f"[red]Missing env vars:[/] {', '.join(missing)}")
    urls = [API_KEY_SIGNUP[name] for name in missing if name in API_KEY_SIGNUP]
    if urls:
        err_console.print(f"Add to .env or export. Get keys: {' | '.join(urls)}")


def _load_topic_or_error(name: str, *, label: str) -> TopicConfig | None:
    topic_config = get_topic(name)
    if topic_config:
        return topic_config

    topics = load_topics()
    err_console.print(f"[red]{label} '{name}' not found.[/]")
    if topics:
        err_console.print(f"Available: {', '.join(topics.keys())}")
    else:
        err_console.print(f"No topics.toml found. Create {Path.cwd() / TOPICS_FILENAME}")
    return None


def _print_sources(results: ResearchResults) -> None:
    level, explanation = results.confidence_score()
    color = {"HIGH": "green", "MEDIUM": "yellow", "LOW": "red"}.get(level, "white")
    console.print(f"\n[bold]Confidence:[/] [{color}]{explanation}[/]")
    console.print("\n[bold]Sources:[/]")
    for tool, count in sorted(results.tool_usage.items()):
        console.print(f"  {tool}: {'[green]' + str(count) + 'x[/]' if count else '[dim]0[/]'}")
    console.print()


def _merge_compare_results(
    outputs: list[ResearchOutput],
    compare_str: str | None,
) -> tuple[ResearchResults, str]:
    results = ResearchResults()
    for output in outputs:
        results.findings.extend(output.results.findings)
        for tool, count in output.results.tool_usage.items():
            results.tool_usage[tool] = results.tool_usage.get(tool, 0) + count
    label = f"compare:{compare_str}"
    return results, label


def _run_guarded(fn: Callable[[], int], verbose: bool) -> int:
    """Uniform error surface for pipeline commands: KeyboardInterrupt → 130, Exception → 1."""
    try:
        return fn()
    except KeyboardInterrupt:
        err_console.print("\n[yellow]Interrupted[/]")
        return 130
    except Exception as e:
        err_console.print(f"[red]Error:[/] {e}")
        if verbose:
            err_console.print_exception()
        return 1


def _status(message: str, *, enabled: bool):
    return console.status(message) if enabled else nullcontext()


def run_pipeline(
    topic: str,
    scope_str: str = "all",
    compare_str: str | None = None,
    questions: list[str] | None = None,
    output_path: str = "outputs/report.md",
    verbose: bool = False,
    show_sources: bool = False,
    no_appendix: bool = False,
    output_format: str = "markdown",
    limit: int | None = None,
    no_review: bool = False,
    since: str | None = None,
) -> int:
    """Run the full research pipeline. Returns exit code."""
    if limit:
        set_results_limit(limit)

    if topic == "-":
        topic = sys.stdin.read().strip()
        if not topic:
            err_console.print("[red]Error:[/] empty topic on stdin")
            return 1

    try:
        scope = parse_scope(scope_str)
        compare_targets = parse_compare(compare_str) if compare_str else None
    except ValueError as e:
        err_console.print(f"[red]Error:[/] {e}")
        return 1

    missing = check_env(scope, compare_targets)
    if missing:
        _print_missing_env(missing)
        return 1

    is_json = output_format == "json"

    def _body() -> int:
        if compare_targets:
            if not is_json:
                console.print(f"[bold]Civic[/] comparing: {topic}")
                console.print(f"[dim]{' vs '.join(compare_targets)}[/]\n")

            with _status("[dim]Researching...", enabled=not is_json):
                outputs = compare_research(topic, compare_targets, questions, verbose and not is_json, since)

            if is_json:
                all_results = {}
                for o in outputs:
                    all_results[o.scope_label] = o.results.to_dict()
                print(json.dumps({
                    "topic": topic,
                    "mode": "compare",
                    "targets": compare_targets,
                    "results": all_results,
                }, indent=2))
                return 0

            if show_sources:
                for o in outputs:
                    console.print(f"\n[bold]{o.scope_label}:[/]")
                    for tool, count in sorted(o.results.tool_usage.items()):
                        console.print(f"  {tool}: {'[green]' + str(count) + 'x[/]' if count else '[dim]0[/]'}")

            console.print("[green]✓[/] Research")
            with _status("[dim]Writing comparison...", enabled=True):
                final = write_comparison(topic, outputs)
            console.print("[green]✓[/] Comparison")

        else:
            label = scope_label(scope)

            if not is_json:
                console.print(f"[bold]Civic[/] researching: {topic}")
                console.print(f"[dim]Scope: {label}[/]\n")

            with _status("[dim]Researching...", enabled=not is_json):
                research_output = research(topic, questions, scope=scope, verbose=verbose and not is_json, since=since)

            if is_json:
                results_dict = research_output.results.to_dict()
                print(format_json(topic, label, results_dict))
                return 0

            console.print("[green]✓[/] Research")

            if show_sources:
                _print_sources(research_output.results)

            with _status("[dim]Writing...", enabled=True):
                draft = write_brief(topic, research_output, include_appendix=not no_appendix)
            console.print("[green]✓[/] Draft")

            if not no_review:
                with _status("[dim]Reviewing...", enabled=True):
                    draft = review(draft)
                console.print("[green]✓[/] Review")
            final = draft

        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        save_report(final, out)
        console.print(f"\n[green]Saved:[/] {out}")
        return 0

    return _run_guarded(_body, verbose)


def handle_interrupt(
    _signum: int,
    _frame: _stdlib_types.FrameType | None,
) -> None:
    err_console.print("\n[yellow]Interrupted[/]")
    sys.exit(130)


def cmd_run(args: argparse.Namespace) -> int:
    topic_config = _load_topic_or_error(args.name, label="Topic")
    if not topic_config:
        return 1

    if getattr(args, "format", None) != "json":
        console.print(f"[dim]Running preset:[/] {args.name}")
    return run_pipeline(
        topic=topic_config["topic"],
        scope_str=topic_config.get("scope", "all"),
        compare_str=topic_config.get("compare"),
        questions=topic_config.get("questions"),
        output_path=topic_config.get("output", f"outputs/{args.name}.md"),
        verbose=args.verbose,
        show_sources=args.sources,
        no_appendix=args.no_appendix,
        output_format=getattr(args, "format", "markdown"),
        limit=getattr(args, "limit", None),
        no_review=getattr(args, "no_review", False),
        since=getattr(args, "since", None),
    )


def cmd_cache(args: argparse.Namespace) -> int:
    if args.cache_action == "clear":
        if clear_cache():
            console.print("[green]Cache cleared[/]")
        else:
            console.print("[dim]No cache to clear[/]")
        return 0

    if args.cache_action == "stats":
        stats = get_cache_stats()
        if not stats:
            console.print("[dim]No cache file[/]")
            return 0
        console.print("[bold]Cache stats[/]")
        console.print(f"  Entries: {stats['entries']}")
        console.print(f"  Size: {stats['size_kb']:.1f} KB")
        if stats["oldest"]:
            console.print(f"  Oldest: {datetime.fromtimestamp(stats['oldest']).isoformat()}")
            console.print(f"  Newest: {datetime.fromtimestamp(stats['newest']).isoformat()}")
        return 0

    return 0


def cmd_topics(args: argparse.Namespace) -> int:
    topics = load_topics()
    if not topics:
        console.print(f"[dim]No topics found. Create {Path.cwd() / TOPICS_FILENAME}[/]")
        return 0
    console.print(f"[bold]Topics[/] ({len(topics)} presets)\n")
    for name, config in topics.items():
        scope = config.get("scope") or f"compare: {config.get('compare', 'all')}"
        console.print(f"  [bold]{name}[/]")
        console.print(f"    [dim]{config['topic']} — {scope}[/]")
    console.print("\n[dim]Run with: civic run <name>[/]")
    return 0


def cmd_research(args: argparse.Namespace) -> int:
    return run_pipeline(
        topic=args.topic,
        scope_str=args.scope,
        compare_str=args.compare,
        questions=args.questions,
        output_path=args.output,
        verbose=args.verbose,
        show_sources=args.sources,
        no_appendix=args.no_appendix,
        output_format=args.format,
        limit=getattr(args, "limit", None),
        no_review=getattr(args, "no_review", False),
        since=getattr(args, "since", None),
    )


def cmd_signals(args: argparse.Namespace) -> int:
    """Skips Gemini write/review; emits raw findings as atomic JSON for downstream consumers."""
    if args.preset:
        topic_config = _load_topic_or_error(args.preset, label="Preset")
        if not topic_config:
            return 1
        topic = topic_config["topic"]
        scope_str = topic_config.get("scope", "all")
        compare_str = topic_config.get("compare")
        questions = topic_config.get("questions")
        preset_name = args.preset
    elif args.topic:
        topic = args.topic
        scope_str = args.scope
        compare_str = args.compare
        questions = args.questions
        preset_name = None
    else:
        err_console.print("[red]Provide either a preset (positional) or --topic[/]")
        return 1

    try:
        scope = parse_scope(scope_str)
        compare_targets = parse_compare(compare_str) if compare_str else None
    except ValueError as e:
        err_console.print(f"[red]Error:[/] {e}")
        return 1

    missing = check_env(scope, compare_targets)
    if missing:
        _print_missing_env(missing)
        return 1

    if args.limit:
        set_results_limit(args.limit)

    since = getattr(args, "since", None)

    def _body() -> int:
        if compare_targets:
            outputs = compare_research(topic, compare_targets, questions, verbose=args.verbose, since=since)
            results, label = _merge_compare_results(outputs, compare_str)
        else:
            research_output = research(topic, questions, scope=scope, verbose=args.verbose, since=since)
            label = research_output.scope_label
            results = research_output.results

        print(emit_signals(topic, preset_name, label, results))
        return 0

    return _run_guarded(_body, args.verbose)


def cmd_doctor(args: argparse.Namespace) -> int:
    """Validate env vars. Required keys fail the run; optional keys warn only."""
    required_keys = ["GOOGLE_API_KEY", "EXA_API_KEY"]
    optional_keys = [
        "CONGRESS_GOV_API_KEY",
        "OPENSTATES_API_KEY",
        "LEGISCAN_API_KEY",
        "REGULATIONS_GOV_API_KEY",
        "CENSUS_API_KEY",
    ]

    def _env_check(name: str) -> bool:
        return bool(os.getenv(name))

    # Shared column width keeps optional (printed manually) and required (via doctor_runner)
    # rows visually aligned in the [PASS]/[FAIL]/[WARN] output.
    width = max(len(f"env {k}") for k in required_keys + optional_keys)

    # Advisory scan of optional keys — never exits nonzero.
    for k in optional_keys:
        ok = _env_check(k)
        mark = "PASS" if ok else "WARN"
        name = f"env {k}".ljust(width)
        line = f"  [{mark}] {name}"
        if not ok:
            line += f"  — optional — sign up: {API_KEY_SIGNUP.get(k, '(no URL)')}"
        print(line, file=sys.stderr)

    required_checks = [
        DoctorCheck(
            name=f"env {k}".ljust(width),
            check=(lambda n=k: _env_check(n)),
            hint=f"required — sign up: {API_KEY_SIGNUP.get(k, '(no URL)')}",
        )
        for k in required_keys
    ]
    return doctor_runner(required_checks, exit_on_fail=False)


def cmd_get(args: argparse.Namespace) -> int:
    """Fetch a URL's full content. Markdown by default, JSON envelope with -f json."""
    url = args.url
    is_json = getattr(args, "format", "markdown") == "json"
    try:
        with httpx.Client(timeout=30, follow_redirects=True) as client:
            resp = client.get(url, headers={"User-Agent": f"civic/{__version__}"})
            resp.raise_for_status()
            body = resp.text
            status_code = resp.status_code
            content_type = resp.headers.get("content-type", "")
    except httpx.HTTPError as e:
        if is_json:
            print(json.dumps({
                "status": "error",
                "command": "get",
                "error": str(e),
            }, separators=(",", ":")))
        else:
            err_console.print(f"[red]Error:[/] {e}")
        return 1

    if is_json:
        print(json.dumps({
            "status": "ok",
            "command": "get",
            "data": {
                "url": url,
                "status_code": status_code,
                "content_type": content_type,
                "content": body,
            },
        }, separators=(",", ":")))
    else:
        print(body)
    return 0


def _add_common_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("-f", "--format", choices=["markdown", "json"], default="markdown",
                        help="Output format: markdown (file) or json (stdout)")
    parser.add_argument("--sources", action="store_true")
    parser.add_argument("--no-appendix", action="store_true")
    parser.add_argument("--no-review", action="store_true",
                        help="Skip the reviewer pass (faster; lower polish)")
    parser.add_argument("--since", metavar="YYYY-MM-DD", default=None,
                        help="Filter results to items published on or after this date")
    parser.add_argument("--limit", type=int, default=None,
                        help="Per-tool results limit (default: 25)")


def _build_adhoc_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="civic",
        description="Policy research CLI — evidence-based briefs from 8 government sources",
    )
    parser.add_argument("topic", help="Policy topic to research (use '-' to read from stdin)")
    parser.add_argument("-s", "--scope", default="all",
                        help="federal | state:XX | all | news | policy (default: all)")
    parser.add_argument("-c", "--compare", metavar="A,B",
                        help="Compare targets: CA,NY or federal,CA or policy,news")
    parser.add_argument("-o", "--output", default="outputs/report.md")
    parser.add_argument("-q", "--questions", nargs="+", metavar="Q")
    _add_common_flags(parser)
    return parser


def main() -> int:
    load_dotenv()
    signal.signal(signal.SIGINT, handle_interrupt)

    # Route ad-hoc topics (e.g. `civic "AI regulation"`) without requiring a subcommand.
    known_commands = {"run", "topics", "cache", "doctor", "get", "signals"}
    first_pos = next((a for a in sys.argv[1:] if not a.startswith("-")), None)

    if first_pos and first_pos not in known_commands:
        args = _build_adhoc_parser().parse_args()
        return cmd_research(args)

    parser = argparse.ArgumentParser(
        prog="civic",
        description="Policy research CLI — evidence-based briefs from 8 government sources",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  civic "AI regulation" -s federal
  civic "AI regulation" -s federal -f json
  civic "Caregiver policy" --compare CA,NY,MA
  civic run caregiver-federal
  civic topics
  civic cache stats
        """,
    )
    parser.add_argument("-V", "--version", action="version", version=f"%(prog)s {__version__}")

    subparsers = parser.add_subparsers(dest="command")

    run_parser = subparsers.add_parser("run", help="Run a named topic preset from topics.toml")
    run_parser.add_argument("name", help="Topic preset name (see: civic topics)")
    _add_common_flags(run_parser)
    run_parser.set_defaults(func=cmd_run)

    topics_parser = subparsers.add_parser("topics", help="List available topic presets")
    topics_parser.set_defaults(func=cmd_topics)

    cache_parser = subparsers.add_parser("cache", help="Manage response cache")
    cache_parser.add_argument("cache_action", choices=["stats", "clear"], help="stats | clear")
    cache_parser.set_defaults(func=cmd_cache)

    signals_parser = subparsers.add_parser(
        "signals",
        help="Emit atomic per-finding JSON signals (for web-pulse and other consumers)",
    )
    signals_parser.add_argument("preset", nargs="?", help="Preset name from topics.toml")
    signals_parser.add_argument("--topic", help="Ad-hoc topic (alternative to preset)")
    signals_parser.add_argument("-s", "--scope", default="all",
                                 help="federal | state:XX | all | news | policy (used with --topic)")
    signals_parser.add_argument("-c", "--compare", metavar="A,B",
                                 help="Compare targets (used with --topic)")
    signals_parser.add_argument("-q", "--questions", nargs="+", metavar="Q")
    signals_parser.add_argument("--limit", type=int, default=None,
                                 help="Per-tool results limit (default: 25)")
    signals_parser.add_argument("--since", metavar="YYYY-MM-DD", default=None,
                                 help="Filter results to items published on or after this date")
    signals_parser.add_argument("-v", "--verbose", action="store_true")
    signals_parser.set_defaults(func=cmd_signals)

    doctor_parser = subparsers.add_parser("doctor", help="Validate env vars + API keys")
    doctor_parser.set_defaults(func=cmd_doctor)

    get_parser = subparsers.add_parser("get", help="Fetch a URL's full content")
    get_parser.add_argument("url", help="URL to fetch")
    get_parser.add_argument("-f", "--format", choices=["markdown", "json"], default="markdown",
                            help="Output format: markdown (raw body) or json envelope")
    get_parser.set_defaults(func=cmd_get)

    args = parser.parse_args()

    if hasattr(args, "func") and args.func:
        return args.func(args)

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
