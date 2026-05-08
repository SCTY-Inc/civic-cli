import sys
from dataclasses import dataclass
from typing import Callable


@dataclass
class DoctorCheck:
    name: str
    check: Callable[[], bool]
    hint: str | None = None


def doctor_runner(
    checks: list[DoctorCheck],
    *,
    exit_on_fail: bool = True,
) -> int:
    failures = 0
    width = max((len(check.name) for check in checks), default=0)
    for check in checks:
        try:
            ok = bool(check.check())
            hint = check.hint or ""
        except Exception as error:
            ok = False
            hint = f"{check.hint or ''} ({error})".strip()

        mark = "PASS" if ok else "FAIL"
        line = f"  [{mark}] {check.name.ljust(width)}"
        if not ok and hint:
            line += f"  — {hint}"
        print(line, file=sys.stderr)
        failures += int(not ok)

    if failures:
        print(f"\ndoctor: {failures} check(s) failed", file=sys.stderr)
        if exit_on_fail:
            sys.exit(1)
        return 1

    print("\ndoctor: all checks passed", file=sys.stderr)
    return 0
