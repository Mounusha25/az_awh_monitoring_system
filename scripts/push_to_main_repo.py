#!/usr/bin/env python3
"""Ship local changes to the main repo (azawhasu-team/AzAWH-Project).

Automates the clone-diff-copy procedure from CLAUDE.md section 4: the main repo
has unrelated git history, so a plain `git push` from this checkout can't reach
it. This clones it fresh, copies over only the files that differ, then commits
and pushes from the clone.

    python scripts/push_to_main_repo.py                    # dry run (default)
    python scripts/push_to_main_repo.py --apply -m "msg"   # commit + push (asks first)

Safety properties:
  * Dry run unless --apply, and --apply still asks before pushing (--yes skips).
  * Files come from `git ls-files`, so .gitignore is honoured, and a hard
    exclude list (secrets, .env*, node_modules, build output) applies on top.
  * Never deletes anything in the main repo; files that exist only there are
    reported and left alone.
  * Never creates a new top-level folder in the main repo unless asked
    (--allow-new-top-level).
  * No Co-Authored-By trailer is added to the commit message.

Backend changes ALSO need a normal `git push origin main` from this checkout so
Render redeploys (it still watches Mounusha25/az_awh_monitoring_system); the
script reminds you but does not do that push itself.
"""
import argparse
import fnmatch
import filecmp
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

MAIN_REPO_URL = "https://github.com/azawhasu-team/AzAWH-Project.git"
REPO_ROOT = Path(__file__).resolve().parent.parent

# Directories that are their own git repos inside this checkout (gitlinks in
# the outer repo), so their files must be listed from the inner repo.
NESTED_REPOS = ["awh_az/water-station-dashboard"]

MAX_BYTES = 5 * 1024 * 1024

EXCLUDE_DIRS = {".git", "node_modules", ".next", "__pycache__", ".pytest_cache", "venv", ".venv"}
EXCLUDE_NAMES = [
    "next-env.d.ts", "*.tsbuildinfo", ".DS_Store", "*.pyc",
    ".env", ".env.*", "*.env",
    "*serviceAccount*.json", "awh-project-*.json", "firebase-key.json",
    "*.pem", "*.key",
]
# Templates that look like secrets by name but are meant to be committed.
ALLOW_NAMES = ["*.example"]


def exclusion_reason(rel: str):
    """Why `rel` (posix path relative to repo root) must never be copied, or None."""
    parts = rel.split("/")
    for d in parts[:-1]:
        if d in EXCLUDE_DIRS:
            return f"inside excluded dir '{d}'"
    name = parts[-1]
    if any(fnmatch.fnmatch(name, pat) for pat in ALLOW_NAMES):
        return None
    for pat in EXCLUDE_NAMES:
        if fnmatch.fnmatch(name, pat):
            return f"matches excluded pattern '{pat}'"
    return None


def _git_files(repo: Path, prefix: str = "") -> list[str]:
    out = subprocess.run(
        ["git", "-C", str(repo), "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        check=True, capture_output=True,
    ).stdout.decode()
    return [prefix + p for p in out.split("\0") if p]


def local_files(root: Path = REPO_ROOT, nested=NESTED_REPOS) -> list[str]:
    """Every non-ignored file in the working tree (tracked or new), nested repos included."""
    files = [f for f in _git_files(root) if (root / f).is_file()]  # drops gitlinks + deleted files
    for n in nested:
        if (root / n / ".git").exists():
            files += [f for f in _git_files(root / n, n + "/") if (root / f).is_file()]
    return sorted(set(files))


def plan(root: Path, clone: Path, files: list[str], allow_new_top_level: bool = False) -> dict:
    """Classify each local file against the clone. Pure filesystem comparison."""
    res = {"new": [], "changed": [], "excluded": {}, "skipped_top": set(), "too_big": []}
    for rel in sorted(files):
        reason = exclusion_reason(rel)
        if reason:
            res["excluded"][rel] = reason
            continue
        top = rel.split("/")[0]
        if "/" in rel and not (clone / top).exists() and not allow_new_top_level:
            res["skipped_top"].add(top)
            continue
        if "/" not in rel and not (clone / rel).exists() and not allow_new_top_level:
            res["skipped_top"].add(rel)
            continue
        src, dst = root / rel, clone / rel
        if src.stat().st_size > MAX_BYTES:
            res["too_big"].append(rel)
        elif not dst.exists():
            res["new"].append(rel)
        elif not filecmp.cmp(src, dst, shallow=False):
            res["changed"].append(rel)
    return res


def _run(cmd, cwd=None, **kw):
    return subprocess.run(cmd, cwd=cwd, check=True, **kw)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--apply", action="store_true", help="commit and push (default is a dry run)")
    ap.add_argument("-m", "--message", help="commit message (required with --apply)")
    ap.add_argument("--branch", default="main")
    ap.add_argument("--repo-url", default=MAIN_REPO_URL)
    ap.add_argument("--yes", action="store_true", help="skip the push confirmation prompt")
    ap.add_argument("--allow-new-top-level", action="store_true",
                    help="also copy top-level folders/files that don't exist in the main repo yet")
    ap.add_argument("--only", action="append", metavar="PREFIX",
                    help="restrict to paths starting with PREFIX (repeatable)")
    ap.add_argument("--verbose", action="store_true", help="list excluded files and main-repo-only files")
    args = ap.parse_args(argv)

    if args.apply and not args.message:
        ap.error("--apply requires -m/--message")

    tmp = Path(tempfile.mkdtemp(prefix="azawh-main-"))
    clone = tmp / "repo"
    try:
        print(f"Cloning {args.repo_url} ({args.branch}) ...")
        _run(["git", "clone", "--quiet", "--depth", "1", "--branch", args.branch, args.repo_url, str(clone)])

        files = local_files()
        if args.only:
            files = [f for f in files if any(f.startswith(p) for p in args.only)]
        res = plan(REPO_ROOT, clone, files, args.allow_new_top_level)
        to_copy = res["changed"] + res["new"]

        print(f"\nChanged: {len(res['changed'])}   New: {len(res['new'])}   "
              f"Unchanged/other: {len(files) - len(to_copy) - len(res['excluded']) - len(res['too_big'])}")
        for f in res["changed"]:
            print(f"  M  {f}")
        for f in res["new"]:
            print(f"  A  {f}")
        if res["too_big"]:
            print(f"\nSkipped (over {MAX_BYTES // 1024 // 1024} MB): " + ", ".join(res["too_big"]))
        if res["skipped_top"]:
            print("\nSkipped (top-level not in main repo; use --allow-new-top-level): "
                  + ", ".join(sorted(res["skipped_top"])))
        secrets = sorted(f for f, why in res["excluded"].items() if "excluded pattern" in why)
        print(f"\nExcluded by safety rules: {len(res['excluded'])}"
              + (f"  (incl. {len(secrets)} secret-looking file(s))" if secrets else ""))
        if args.verbose:
            for f, why in sorted(res["excluded"].items()):
                print(f"  - {f}: {why}")

        if any(f.startswith("awh_az/backend/") for f in to_copy):
            print("\nREMINDER: backend files changed. Also run `git push origin main` from this checkout\n"
                  "so Render redeploys (it still watches Mounusha25/az_awh_monitoring_system).")

        if not to_copy:
            print("\nMain repo is already up to date. Nothing to do.")
            return 0
        if not args.apply:
            print("\nDry run only. Re-run with --apply -m \"message\" to commit and push.")
            return 0

        for rel in to_copy:
            dst = clone / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(REPO_ROOT / rel, dst)
        _run(["git", "add", "--"] + to_copy, cwd=clone)
        _run(["git", "commit", "--quiet", "-m", args.message], cwd=clone)
        _run(["git", "show", "--stat", "--format=%h %s", "HEAD"], cwd=clone)

        if not args.yes:
            ans = input(f"\nPush {len(to_copy)} file(s) to {args.repo_url} ({args.branch})? [y/N] ")
            if ans.strip().lower() != "y":
                print("Aborted. Nothing was pushed.")
                return 1
        _run(["git", "push", "origin", f"HEAD:{args.branch}"], cwd=clone)
        sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=clone,
                             capture_output=True, text=True).stdout.strip()
        print(f"\nPushed {sha} to {args.repo_url} ({args.branch}).")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
