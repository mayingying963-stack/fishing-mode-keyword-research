#!/usr/bin/env python3
"""Single entry point for every check in this repo.

    python scripts/run_checks.py            # everything
    python scripts/run_checks.py --quick    # skip the slow end-to-end case

Exit code 0 means all checks pass. This is the canonical test command: if you
touch `fishing.py`, `export_workbook.py`, or the relevance constants, run it.

Suites:
  1. selftest.py            offline end-to-end pipeline (67 assertions)
  2. verify_field_shapes.py DataForSEO wire shapes captured from live responses
  3. preflight.py           environment contract: what the skill needs and how it
                            reports missing pieces (docs must match code)
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SUITES = [
    ("selftest.py", "offline end-to-end pipeline"),
    ("verify_field_shapes.py", "DataForSEO wire shapes"),
    ("preflight_checks.py", "environment contract + docs"),
    ("push_script_checks.py", "push_ssh.sh guard rails"),
]


def run_one(script: str) -> tuple[bool, str, float]:
    path = HERE / script
    if not path.exists():
        return False, f"missing {script}", 0.0
    t0 = time.time()
    r = subprocess.run([sys.executable, str(path)], capture_output=True)
    dur = time.time() - t0
    out = (r.stdout + r.stderr).decode("utf-8", "replace")
    # Pull the suite's own summary line if present.
    tail = [l for l in out.strip().splitlines() if "passed" in l.lower()]
    summary = tail[-1].strip() if tail else out.strip().splitlines()[-1:] or [""]
    if isinstance(summary, list):
        summary = summary[0] if summary else ""
    return r.returncode == 0, summary, dur


def main(argv=None) -> int:
    argv = argv or []
    print("=" * 64)
    print("fishing-mode-keyword-research — full check run")
    print("=" * 64)

    failures = 0
    total = 0.0
    for script, label in SUITES:
        ok, summary, dur = run_one(script)
        total += dur
        status = "PASS" if ok else "FAIL"
        print(f"\n[{status}] {script:26} {label}")
        print(f"        {summary[:100]}")
        print(f"        {dur:.1f}s")
        if not ok:
            failures += 1

    print("\n" + "=" * 64)
    if failures:
        print(f"RESULT: {failures} suite(s) FAILED")
        print("Re-run the failing script directly for full output:")
        for script, _ in SUITES:
            print(f"  python {HERE / script}")
    else:
        print(f"RESULT: all {len(SUITES)} suites passed in {total:.1f}s")
    print("=" * 64)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
