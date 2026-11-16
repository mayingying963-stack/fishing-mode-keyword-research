#!/usr/bin/env python3
"""Environment contract + documentation consistency checks.

Two things this pins down, because both have broken silently before:

1. **The skill runs on a machine that is not this one.** A Chinese log line on a
   cp1252 console used to raise UnicodeEncodeError and kill the run; `preflight()`
   and `_safe_stream()` fix that, and this suite proves the fix still holds.

2. **The docs must not drift from the code.** `references/environment-and-preflight.md`
   tells the reader what to install, which Python version is required, which market
   codes to pass, and which words must never enter the blocklists. Every one of
   those claims is asserted here against the actual source, so a doc can never
   promise something the code does not do.

Run:  python scripts/preflight_checks.py
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import fishing  # noqa: E402

PASS = FAIL = 0
FAILED: list[str] = []
CRED_KEYS = ("DATAFORSEO_LOGIN", "DATAFORSEO_USERNAME", "DATAFORSEO_PASSWORD")


def check(label: str, got, want) -> None:
    global PASS, FAIL
    if got == want:
        PASS += 1
    else:
        FAIL += 1
        FAILED.append(f"{label}: got {got!r} want {want!r}")


def run_fishing(args: list[str]) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k not in CRED_KEYS}
    return subprocess.run([sys.executable, str(HERE / "fishing.py"), *args],
                          capture_output=True, env=env)


def section(t: str) -> None:
    print("\n" + "-" * 66 + f"\n{t}\n" + "-" * 66)


# ------------------------------------------------ 1. console encoding safety
section("[1] logs survive consoles that cannot encode Chinese")
for enc in ("utf-8", "ascii", "cp1252", "cp936"):
    r = subprocess.run(
        [sys.executable, "-c",
         f"import sys; sys.path.insert(0, {str(HERE)!r});"
         "import fishing; fishing.main(['--check-env','--mode','offline'])"],
        capture_output=True, env={**os.environ, "PYTHONIOENCODING": enc})
    check(f"PYTHONIOENCODING={enc}", r.returncode, 0)

src = (HERE / "fishing.py").read_text(encoding="utf-8")
main_body = src.split("def main(argv=None)")[1][:900]
check("_safe_stream runs before argparse",
      main_body.index("_safe_stream") < main_body.index("ArgumentParser"), True)

# ------------------------------------------------ 2. preflight contract
section("[2] preflight blocks what it should, warns what it should")
ok, blockers, _ = fishing.preflight("offline")
check("offline ok without credentials", ok, True)
check("offline has no blockers", blockers, [])

saved = {k: os.environ.pop(k, None) for k in CRED_KEYS}
try:
    ok_api, b_api, _ = fishing.preflight("api")
    check("api blocked without credentials", ok_api, False)
    check("blocker names DATAFORSEO", any("DATAFORSEO" in b for b in b_api), True)

    os.environ["DATAFORSEO_LOGIN"] = "probe"
    os.environ["DATAFORSEO_PASSWORD"] = "probe"
    ok_ok, b_ok, _ = fishing.preflight("api")
    check("api ok once credentials exist", ok_ok, True)
    check("no blockers with credentials", b_ok, [])
finally:
    for k in CRED_KEYS:
        os.environ.pop(k, None)
        if saved[k] is not None:
            os.environ[k] = saved[k]

# ------------------------------------------------ 3. CLI surface
section("[3] --check-env reports state with meaningful exit codes")
r = run_fishing(["--check-env", "--mode", "offline"])
out = r.stdout.decode("utf-8", "replace")
check("offline exit 0", r.returncode, 0)
check("says ready", "ready" in out.lower(), True)
for token in ("python", "platform", "console enc", "mode", "requests", "openpyxl"):
    check(f"reports {token}", token in out, True)

r2 = run_fishing(["--check-env", "--mode", "api"])
check("api without creds exit 2", r2.returncode, 2)
check("prints BLOCK", "BLOCK" in r2.stdout.decode("utf-8", "replace"), True)

# ------------------------------------------------ 4. write probe
section("[4] unwritable output dir is caught before spending")
tmp = Path(tempfile.mkdtemp(prefix="preflight-check-"))
try:
    blocker = tmp / "file_not_dir"
    blocker.write_text("x", encoding="utf-8")
    r3 = run_fishing(["--seeds", "a battery", "--mode", "offline",
                      "--out", str(blocker / "sub")])
    check("non-creatable out dir fails", r3.returncode != 0, True)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

# ------------------------------------------------ 5. version floor
section("[5] documented Python floor matches real syntax")
allsrc = "\n".join((HERE / f).read_text(encoding="utf-8")
                   for f in ("fishing.py", "selftest.py", "export_workbook.py"))
check("no 3.10-only match statement",
      bool(re.search(r"^\s*match\s+\w+.*:\s*$", allsrc, re.M)), False)
check("runtime dict merge present (3.9 floor)",
      bool(re.search(r'\w+\s*\|\s*\{\s*"', allsrc)), True)
check("__future__ annotations used",
      "from __future__ import annotations" in allsrc, True)
envdoc = (ROOT / "references" / "environment-and-preflight.md").read_text(encoding="utf-8")
check("doc does not overstate the floor to 3.10+",
      bool(re.search(r"Python ≥ 3\.1[01]", envdoc)), False)

# ------------------------------------------------ 6. docs match code
section("[6] reference doc covers every enforced requirement")
skilldoc = (ROOT / "SKILL.md").read_text(encoding="utf-8")

# The description is the only text read before the skill is loaded, so its length
# is a hard budget: past ~350 chars it turns into a spec nobody triggers on. It
# has been re-inflated by concurrent edits twice, hence the assertion.
import yaml
_fm_match = re.search(r"\n---\s*\n", skilldoc[3:])
_fm = yaml.safe_load(skilldoc[3:_fm_match.start() + 3])
_desc = _fm.get("description", "")
check("description <= 350 chars", len(_desc) <= 350, True)
check("description is non-trivial (> 80 chars)", len(_desc) > 80, True)

# `name` must match the directory, but only when the skill is installed the normal
# way (skills/<category>/<name>/). A plain `git clone` can land anywhere —
# repo-clone, tmp123, my copy — and failing there says nothing about the code.
# The discriminator has to be the *skill name itself*, not the folder's shape:
# an earlier version tested "looks like a skill name (lowercase + hyphen)", which
# is true for 'repo-clone' too and produced exactly the false failure it was
# meant to prevent.
_skill_name = _fm.get("name", "")
if ROOT.name == _skill_name:
    check("name matches directory", _skill_name, ROOT.name)
else:
    print(f"  (skipped name/dir check: running from {ROOT.name!r}, "
          f"skill is {_skill_name!r} — clone dir, not an install)")
for trigger in ("捕鱼法", "种子词", "以词找词", "用词找站", "以站找词", "挖词"):
    check(f"description keeps trigger {trigger}", trigger in _desc, True)
for needle in ("Python", "requests", "openpyxl", "DATAFORSEO_LOGIN",
               "DATAFORSEO_PASSWORD", "location", "2840"):
    check(f"env doc mentions {needle}", needle in envdoc, True)
for needle in ("--check-env", "preflight", "DataForSEO REST", "requests", "openpyxl"):
    check(f"SKILL.md mentions {needle}", needle in skilldoc, True)
for const in ("ADJACENT_VERTICALS", "NON_PRODUCT_INTENT", "ANTONYM_GROUPS"):
    check(f"{const} exists", hasattr(fishing, const), True)
    check(f"{const} documented", const in envdoc, True)

# The words that must never be blocklisted, recorded in the doc that tells
# people how to edit the blocklists.
for word, why in (("truck", "a forklift IS a forklift truck"),
                  ("home", "ordinary modifier for solar/storage products")):
    check(f"doc warns against blocklisting {word!r}", word in envdoc.lower(), True)
check("truck really is absent from ADJACENT_VERTICALS",
      "truck" in fishing.ADJACENT_VERTICALS, False)
check("for sale is not in NON_PRODUCT_INTENT",
      "for sale" in fishing.NON_PRODUCT_INTENT, False)
check("repair is not in NON_PRODUCT_INTENT",
      "repair" in fishing.NON_PRODUCT_INTENT, False)

# ------------------------------------------------ 7. hygiene
section("[7] repo hygiene")
check("BASE is the DataForSEO v3 endpoint",
      fishing.BASE, "https://api.dataforseo.com/v3/")
check("relevant_competitor exists", callable(fishing.relevant_competitor), True)
check("opposites_of exists", callable(fishing.opposites_of), True)
# Scan for credential *shapes* rather than literals: hardcoding the real strings
# here would put them in the repo, which is exactly what this check exists to
# prevent (it caught itself doing so).
leak = subprocess.run(
    ["git", "-C", str(ROOT), "grep", "-nE",
     r"([A-Za-z0-9]{18,}:[A-Za-z0-9]{12,})|(Basic\s+[A-Za-z0-9+/]{20,})"],
    capture_output=True).stdout.decode().strip()
# DATAFORSEO_LOGIN=value in .env.example style docs is fine; a real pair is not.
leak = "\n".join(l for l in leak.splitlines() if "your-" not in l and "example" not in l.lower())
check("no credential-shaped strings committed", leak, "")

print("\n" + "=" * 66)
print(f"{PASS} passed, {FAIL} failed")
print("=" * 66)
for f in FAILED:
    print("  FAIL", f)
sys.exit(1 if FAIL else 0)
