#!/usr/bin/env python3
"""Normalize machine-local Hermes paths in restored active state.

Raw ~/.hermes backups contain both live configuration and historical receipts.
This utility rewrites active text files that still point at an old Hermes home
while leaving transcripts, logs, cron output, webhook captures, snapshots, and
other historical evidence untouched.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path


DEFAULT_ACTIVE_DIRS = {
    ".agents",
    ".claude",
    "bin",
    "cron",
    "gateway",
    "hooks",
    "memories",
    "memory",
    "otp-bridge",
    "profiles",
    "response-verifier",
    "scripts",
    "skills",
    "workspace",
}

DEFAULT_ACTIVE_FILES = {
    ".env",
    "config.yaml",
    "config.yml",
    "gateway_state.json",
    "webhook_subscriptions.json",
}

DEFAULT_EXCLUDED_DIRS = {
    ".git",
    "artifacts",
    "backups",
    "cache",
    "checkpoints",
    "cron/output",
    "hermes-agent",
    "hermes-agent.worktrees",
    "logs",
    "pastes",
    "sessions",
    "state-snapshots",
    "tmp",
    "voice-drops",
    "webhook-captures",
    "workspace/hermes-agent.worktrees",
}


def rel_posix(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def is_excluded(path: Path, root: Path, excluded_dirs: set[str]) -> bool:
    rel = rel_posix(path, root)
    parts = rel.split("/")
    for i in range(1, len(parts) + 1):
        if "/".join(parts[:i]) in excluded_dirs:
            return True
    return False


def is_active_file(
    path: Path,
    root: Path,
    active_dirs: set[str],
    active_files: set[str],
    excluded_dirs: set[str],
) -> bool:
    rel = rel_posix(path, root)
    if rel in active_files:
        return True
    if is_excluded(path, root, excluded_dirs):
        return False
    first = rel.split("/", 1)[0]
    return first in active_dirs


def is_text_file(path: Path) -> bool:
    try:
        chunk = path.read_bytes()[:8192]
    except OSError:
        return False
    if b"\x00" in chunk:
        return False
    try:
        chunk.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def rg_files(root: Path, needle: str) -> list[Path] | None:
    try:
        proc = subprocess.run(
            ["rg", "--hidden", "--glob", "!.git/**", "-l", "--fixed-strings", needle, str(root)],
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        return None
    if proc.returncode not in (0, 1):
        return None
    return [Path(line) for line in proc.stdout.splitlines() if line]


def fallback_files(root: Path, needle: str) -> list[Path]:
    needle_bytes = needle.encode("utf-8")
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        if ".git" in dirnames:
            dirnames.remove(".git")
        base = Path(dirpath)
        for name in filenames:
            path = base / name
            try:
                if needle_bytes in path.read_bytes():
                    found.append(path)
            except OSError:
                continue
    return found


def parse_csv(value: str, defaults: set[str]) -> set[str]:
    items = {item.strip().strip("/") for item in value.split(",") if item.strip()}
    return items or set(defaults)


def normalize(
    root: Path,
    old: str,
    new: str,
    dry_run: bool,
    active_dirs: set[str],
    active_files: set[str],
    excluded_dirs: set[str],
) -> dict[str, object]:
    candidates = rg_files(root, old)
    if candidates is None:
        candidates = fallback_files(root, old)

    total_files = len(candidates)
    active_candidates = [
        path for path in candidates
        if path.is_file()
        and is_active_file(path, root, active_dirs, active_files, excluded_dirs)
    ]
    active_text = [path for path in active_candidates if is_text_file(path)]

    rewritten_files = 0
    replacements = 0
    skipped_binary = len(active_candidates) - len(active_text)

    for path in active_text:
        text = path.read_text(encoding="utf-8")
        count = text.count(old)
        if count <= 0:
            continue
        replacements += count
        rewritten_files += 1
        if not dry_run:
            path.write_text(text.replace(old, new), encoding="utf-8")

    remaining_active_files = 0
    if not dry_run:
        after = rg_files(root, old)
        if after is None:
            after = fallback_files(root, old)
        remaining_active_files = sum(
            1
            for path in after
            if path.is_file()
            and is_active_file(path, root, active_dirs, active_files, excluded_dirs)
        )
    else:
        remaining_active_files = len(active_candidates)

    return {
        "old_path": old,
        "new_path": new,
        "total_legacy_path_files": total_files,
        "active_legacy_path_files_before": len(active_candidates),
        "active_text_files_considered": len(active_text),
        "active_binary_files_skipped": skipped_binary,
        "rewritten_files": rewritten_files,
        "replacements": replacements,
        "active_legacy_path_files_after": remaining_active_files,
        "dry_run": dry_run,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--home", type=Path, required=True)
    parser.add_argument("--old", default="/Users/emo/.hermes")
    parser.add_argument("--new", default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--active-dirs", default=",".join(sorted(DEFAULT_ACTIVE_DIRS)))
    parser.add_argument("--active-files", default=",".join(sorted(DEFAULT_ACTIVE_FILES)))
    parser.add_argument("--exclude-dirs", default=",".join(sorted(DEFAULT_EXCLUDED_DIRS)))
    args = parser.parse_args()

    root = args.home.expanduser().resolve()
    if not root.is_dir():
        raise SystemExit(f"Hermes home not found: {root}")
    new = args.new or str(root)
    result = normalize(
        root=root,
        old=args.old,
        new=new,
        dry_run=args.dry_run,
        active_dirs=parse_csv(args.active_dirs, DEFAULT_ACTIVE_DIRS),
        active_files=parse_csv(args.active_files, DEFAULT_ACTIVE_FILES),
        excluded_dirs=parse_csv(args.exclude_dirs, DEFAULT_EXCLUDED_DIRS),
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
