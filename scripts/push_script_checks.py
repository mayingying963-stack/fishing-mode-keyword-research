#!/usr/bin/env python3
"""Checks for push_ssh.sh — the repo's only non-Python executable.

Kept in the repo so "is this verified?" has an answer that survives the session.
Runs entirely locally against throwaway bare repos: no network, no real push.

What it guarantees:
  * the script refuses to push when the remote repo does not exist
  * it refuses to push a dirty working tree, and nothing reaches the remote
  * it pushes main to a valid remote and leaves origin configured
  * it never force-pushes and never emits credential-shaped output

Run:  python scripts/push_script_checks.py
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "push_ssh.sh"
BASH = shutil.which("bash") or r"C:\Program Files\Git\bin\bash.exe"

PASS = FAIL = 0
FAILED: list[str] = []


def check(label: str, got, want) -> None:
    global PASS, FAIL
    if got == want:
        PASS += 1
    else:
        FAIL += 1
        FAILED.append(f"{label}: got {got!r} want {want!r}")


def sh(args, cwd=None, timeout=90):
    return subprocess.run([BASH, *args], capture_output=True, cwd=cwd, timeout=timeout)


def git(cwd, *args, check=True):
    return subprocess.run(["git", "-C", str(cwd), *args],
                          capture_output=True, check=check)


def section(t: str) -> None:
    print("\n" + "-" * 66 + f"\n{t}\n" + "-" * 66)


def main() -> int:
    section("[1] static properties")
    if not SCRIPT.exists():
        print(f"  missing {SCRIPT}")
        return 1
    body = SCRIPT.read_text(encoding="utf-8")
    check("bash -n clean", sh(["-n", str(SCRIPT)]).returncode, 0)
    check("no literal token variable",
          bool(re.search(r"^\s*TOKEN\s*=", body, re.M)), False)
    check("no credential-shaped literal",
          bool(re.search(r"[A-Za-z0-9]{18,}:[A-Za-z0-9]{12,}", body)), False)
    check("never force-pushes", "--force" in body, False)
    check("targets ssh url form", "git@github.com:" in body, True)
    check("checks ssh auth before pushing", "successfully authenticated" in body, True)
    check("checks remote exists before pushing", "ls-remote" in body, True)
    check("checks dirty tree before pushing", "status --porcelain" in body, True)
    # Order matters: a dirty-tree guard placed after the push is decorative.
    idx_dirty = body.index("status --porcelain")
    idx_push = body.index("git push")
    check("dirty check precedes push", idx_dirty < idx_push, True)

    section("[2] behaviour against throwaway remotes")
    tmp = Path(tempfile.mkdtemp(prefix="push-script-checks-"))
    try:
        work = tmp / "work"
        work.mkdir()
        subprocess.run(["git", "init", "-q", "-b", "master", str(work)], check=True)
        (work / "a.txt").write_text("x", encoding="utf-8")
        git(work, "add", ".")
        subprocess.run(["git", "-C", str(work), "-c", "user.email=t@t",
                        "-c", "user.name=t", "commit", "-q", "-m", "init"], check=True)

        # Rewrite SKILL_DIR so the script operates on the throwaway clone.
        variant = tmp / "push_variant.sh"
        variant.write_text(
            body.replace(
                'SKILL_DIR="/d/Hermes/skills/keyword-research/fishing-mode-keyword-research"',
                f'SKILL_DIR="{work.as_posix()}"'),
            encoding="utf-8")

        # --- remote missing
        fake = f"git@github.com:someone/{tmp.name}-no-such-repo.git"
        r1 = sh([str(variant), fake], timeout=90)
        o1 = (r1.stdout + r1.stderr).decode("utf-8", "replace")
        check("missing remote -> non-zero", r1.returncode != 0, True)
        check("missing remote -> explains", "不存在" in o1 or "not found" in o1.lower(), True)

        # --- dirty tree (separate bare repo so no leftover branch confuses it)
        (work / "a.txt").write_text("changed", encoding="utf-8")
        bare_dirty = tmp / "remote_dirty.git"
        subprocess.run(["git", "init", "-q", "--bare", str(bare_dirty)], check=True)
        r2 = sh([str(variant), str(bare_dirty)], timeout=90)
        o2 = (r2.stdout + r2.stderr).decode("utf-8", "replace")
        check("dirty tree -> non-zero", r2.returncode != 0, True)
        check("dirty tree -> explains",
              "未提交" in o2 or "uncommitted" in o2.lower(), True)
        check("dirty tree -> remote untouched",
              git(bare_dirty, "rev-parse", "--verify", "main", check=False).returncode != 0,
              True)

        # --- success path
        git(work, "checkout", "--", "a.txt")
        bare = tmp / "remote.git"
        subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
        r3 = sh([str(variant), str(bare)], timeout=120)
        o3 = (r3.stdout + r3.stderr).decode("utf-8", "replace")
        check("clean tree -> exit 0", r3.returncode, 0)
        ref = git(bare, "rev-parse", "--verify", "main", check=False)
        check("remote received main", ref.returncode, 0)
        if ref.returncode == 0:
            check("remote head == local head",
                  ref.stdout.strip(), git(work, "rev-parse", "HEAD").stdout.strip())
        origin = git(work, "remote", "get-url", "origin", check=False)
        check("origin configured", origin.returncode, 0)
        check("origin points at given remote",
              origin.stdout.decode("utf-8", "replace").strip(), str(bare))

        section("[3] output hygiene")
        for out in (o1, o2, o3):
            check("no credential-shaped output",
                  bool(re.search(r"[A-Za-z0-9]{18,}:[A-Za-z0-9]{12,}", out)), False)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + "=" * 66)
    print(f"{PASS} passed, {FAIL} failed")
    print("=" * 66)
    for f in FAILED:
        print("  FAIL", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
