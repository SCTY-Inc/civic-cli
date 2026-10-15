"""`civic search` and `civic doctor`."""

from __future__ import annotations

import argparse
import json
import os
import sys

from dotenv import load_dotenv

from . import __version__
from .adapters import ADAPTERS
from .scope import parse_scope
from .search import search, select


def _sources(value: str | None) -> list[str] | None:
    return [name.strip() for name in value.split(",") if name.strip()] if value else None


def cmd_search(args: argparse.Namespace) -> int:
    envelope = search(
        args.topic,
        scope=args.scope,
        since=args.since,
        sources=_sources(args.sources),
        limit=args.limit,
        raw_dir=args.raw_dir,
    )
    if args.format == "jsonl":
        print(json.dumps({"type": "run", **envelope.header()}, ensure_ascii=False))
        for record in envelope.records:
            print(json.dumps({"type": "record", **record.to_dict()}, ensure_ascii=False))
    else:
        print(json.dumps(envelope.to_dict(), indent=2, ensure_ascii=False))
    return 0 if envelope.ok else 1


def cmd_doctor(args: argparse.Namespace) -> int:
    """Report which sources can run. No network. Exit 1 if a named source cannot."""
    names = _sources(args.sources)
    adapters = select(parse_scope(args.scope), names)
    failures = 0
    for adapter in adapters:
        ready = adapter.available(os.environ)
        note = "" if ready else f"  set {adapter.env} ({adapter.docs})"
        print(f"  [{'ready' if ready else 'missing'}] {adapter.name:<17}{note}", file=sys.stderr)
        failures += int(not ready and names is not None)
    if not any(a.available(os.environ) for a in adapters):
        failures += 1
        print("doctor: no source in scope can run", file=sys.stderr)
    return 1 if failures else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="civic", description=__doc__)
    parser.add_argument("-V", "--version", action="version", version=f"civic {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument("-s", "--scope", default="all",
                       help="all | federal | policy | news | state:CA,NY (default: all)")
        p.add_argument("--sources", help=f"comma list; overrides scope selection: {','.join(ADAPTERS)}")

    s = sub.add_parser("search", help="search sources; print one traceable envelope")
    s.add_argument("topic")
    common(s)
    s.add_argument("--since", help="YYYY-MM-DD; sources that support it filter on/after")
    s.add_argument("--limit", type=int, default=25, help="max records per source query (default 25)")
    s.add_argument("-f", "--format", choices=("json", "jsonl"), default="json")
    s.add_argument("--raw-dir", help="save each raw response under DIR/<run_id>/ for audit")
    s.set_defaults(func=cmd_search)

    d = sub.add_parser("doctor", help="check which sources have the credentials they need")
    common(d)
    d.set_defaults(func=cmd_doctor)
    return parser


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except ValueError as error:
        parser.error(str(error))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
