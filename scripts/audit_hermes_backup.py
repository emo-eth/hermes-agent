#!/usr/bin/env python3
"""Compare live Hermes state with a raw hermes-workspace-backup checkout.

This is a freshness gate for disaster recovery. The container restore drill
proves that a backup can be restored; this script proves whether the backup is
current relative to a live Hermes home.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any


def load_sessions_index(root: Path) -> dict[str, Any]:
    path = root / "sessions" / "sessions.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    by_id: dict[str, Any] = {}
    for key, entry in data.items():
        if not isinstance(entry, dict):
            continue
        session_id = entry.get("session_id")
        if isinstance(session_id, str) and session_id:
            by_id[session_id] = entry
        elif isinstance(key, str) and key:
            by_id[key] = entry
    return by_id


def jsonl_names(root: Path) -> set[str]:
    sessions_dir = root / "sessions"
    if not sessions_dir.is_dir():
        return set()
    return {
        path.name
        for path in sessions_dir.glob("*.jsonl")
        if not path.name.startswith("request_dump_")
    }


def jsonl_stems(root: Path) -> set[str]:
    sessions_dir = root / "sessions"
    if not sessions_dir.is_dir():
        return set()
    return {
        path.stem
        for path in sessions_dir.glob("*.jsonl")
        if not path.name.startswith("request_dump_")
    }


def count_db_sessions(root: Path) -> tuple[int | None, int | None, int]:
    db_path = root / "state.db"
    if not db_path.exists():
        return None, None, 0
    size = db_path.stat().st_size
    try:
        conn = sqlite3.connect(db_path)
        try:
            sessions = conn.execute("select count(*) from sessions").fetchone()[0]
            messages = conn.execute("select count(*) from messages").fetchone()[0]
        finally:
            conn.close()
    except Exception:
        return None, None, size
    return int(sessions), int(messages), size


def state_session_ids(root: Path) -> set[str]:
    db_path = root / "state.db"
    if not db_path.exists():
        return set()
    try:
        conn = sqlite3.connect(db_path)
        try:
            return {str(row[0]) for row in conn.execute("select id from sessions")}
        finally:
            conn.close()
    except Exception:
        return set()


def state_message_count_for_sessions(root: Path, session_ids: set[str]) -> int | None:
    if not session_ids:
        return 0
    db_path = root / "state.db"
    if not db_path.exists():
        return None
    try:
        conn = sqlite3.connect(db_path)
        try:
            total = 0
            ids = sorted(session_ids)
            for offset in range(0, len(ids), 500):
                chunk = ids[offset:offset + 500]
                placeholders = ",".join("?" for _ in chunk)
                total += int(
                    conn.execute(
                        f"select count(*) from messages where session_id in ({placeholders})",
                        chunk,
                    ).fetchone()[0]
                )
            return total
        finally:
            conn.close()
    except Exception:
        return None


def state_session_ids_with_messages(root: Path) -> set[str]:
    db_path = root / "state.db"
    if not db_path.exists():
        return set()
    try:
        conn = sqlite3.connect(db_path)
        try:
            return {
                str(row[0])
                for row in conn.execute(
                    "select distinct session_id from messages"
                )
            }
        finally:
            conn.close()
    except Exception:
        return set()


def jsonl_message_count(path: Path) -> int:
    count = 0
    try:
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(row, dict) and (row.get("role") != "session_meta" or row.get("db_message")):
                    count += 1
    except FileNotFoundError:
        return 0
    return count


def state_message_count_by_session(root: Path) -> dict[str, int]:
    db_path = root / "state.db"
    if not db_path.exists():
        return {}
    try:
        conn = sqlite3.connect(db_path)
        try:
            return {
                str(row[0]): int(row[1])
                for row in conn.execute(
                    "select session_id, count(*) from messages group by session_id"
                )
            }
        finally:
            conn.close()
    except Exception:
        return {}


def rel_sample(names: set[str], limit: int) -> list[str]:
    return sorted(names)[-limit:]


def audit(live_home: Path, backup_dir: Path, sample_limit: int) -> dict[str, Any]:
    live_jsonl = jsonl_names(live_home)
    backup_jsonl = jsonl_names(backup_dir)
    live_jsonl_ids = jsonl_stems(live_home)
    live_index = load_sessions_index(live_home)
    backup_index = load_sessions_index(backup_dir)
    live_db_sessions, live_db_messages, live_db_size = count_db_sessions(live_home)
    backup_db_sessions, backup_db_messages, backup_db_size = count_db_sessions(backup_dir)
    live_state_ids = state_session_ids(live_home)
    live_legacy_ids = live_jsonl_ids | set(live_index)
    live_state_without_legacy = live_state_ids - live_legacy_ids
    live_state_message_ids = state_session_ids_with_messages(live_home)
    live_message_sessions_without_jsonl = live_state_message_ids - live_jsonl_ids
    live_state_message_counts = state_message_count_by_session(live_home)
    live_jsonl_message_mismatches = {
        session_id: {
            "state_db_messages": count,
            "jsonl_messages": jsonl_message_count(live_home / "sessions" / f"{session_id}.jsonl"),
        }
        for session_id, count in live_state_message_counts.items()
        if jsonl_message_count(live_home / "sessions" / f"{session_id}.jsonl") != count
    }

    missing_jsonl = live_jsonl - backup_jsonl
    extra_jsonl = backup_jsonl - live_jsonl
    common_jsonl_ids = live_jsonl_ids & jsonl_stems(backup_dir)
    backup_jsonl_message_drifts = {
        session_id: {
            "live_jsonl_messages": jsonl_message_count(live_home / "sessions" / f"{session_id}.jsonl"),
            "backup_jsonl_messages": jsonl_message_count(backup_dir / "sessions" / f"{session_id}.jsonl"),
        }
        for session_id in common_jsonl_ids
        if jsonl_message_count(live_home / "sessions" / f"{session_id}.jsonl")
        != jsonl_message_count(backup_dir / "sessions" / f"{session_id}.jsonl")
    }
    missing_index = set(live_index) - set(backup_index)
    extra_index = set(backup_index) - set(live_index)

    return {
        "live_home": str(live_home),
        "backup_dir": str(backup_dir),
        "live_jsonl_count": len(live_jsonl),
        "backup_jsonl_count": len(backup_jsonl),
        "missing_jsonl_count": len(missing_jsonl),
        "extra_jsonl_count": len(extra_jsonl),
        "missing_jsonl_sample": rel_sample(missing_jsonl, sample_limit),
        "extra_jsonl_sample": rel_sample(extra_jsonl, sample_limit),
        "backup_jsonl_message_drift_count": len(backup_jsonl_message_drifts),
        "backup_jsonl_message_drift_sample": dict(
            sorted(backup_jsonl_message_drifts.items())[-sample_limit:]
        ),
        "live_sessions_json_entries": len(live_index),
        "backup_sessions_json_entries": len(backup_index),
        "missing_sessions_json_entries": len(missing_index),
        "extra_sessions_json_entries": len(extra_index),
        "missing_sessions_json_sample": rel_sample(missing_index, sample_limit),
        "extra_sessions_json_sample": rel_sample(extra_index, sample_limit),
        "live_state_db_sessions": live_db_sessions,
        "live_state_db_messages": live_db_messages,
        "live_state_db_size_bytes": live_db_size,
        "live_state_sessions_without_legacy_files": len(live_state_without_legacy),
        "live_state_messages_without_legacy_files": state_message_count_for_sessions(live_home, live_state_without_legacy),
        "live_state_sessions_without_legacy_sample": rel_sample(live_state_without_legacy, sample_limit),
        "live_message_sessions_without_jsonl": len(live_message_sessions_without_jsonl),
        "live_messages_without_jsonl": state_message_count_for_sessions(live_home, live_message_sessions_without_jsonl),
        "live_message_sessions_without_jsonl_sample": rel_sample(live_message_sessions_without_jsonl, sample_limit),
        "live_jsonl_message_mismatch_count": len(live_jsonl_message_mismatches),
        "live_jsonl_message_mismatch_sample": dict(
            sorted(live_jsonl_message_mismatches.items())[-sample_limit:]
        ),
        "backup_state_db_sessions": backup_db_sessions,
        "backup_state_db_messages": backup_db_messages,
        "backup_state_db_size_bytes": backup_db_size,
        "backup_state_db_tracked": backup_state_db_tracked(backup_dir),
    }


def backup_state_db_tracked(backup_dir: Path) -> bool:
    git_dir = backup_dir / ".git"
    if not git_dir.exists():
        return False
    import subprocess

    result = subprocess.run(
        ["git", "-C", str(backup_dir), "ls-files", "--error-unmatch", "state.db"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return result.returncode == 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live-home", default=str(Path.home() / ".hermes"))
    parser.add_argument("--backup-dir", required=True)
    parser.add_argument("--report", help="Write JSON report to this path")
    parser.add_argument("--sample-limit", type=int, default=30)
    parser.add_argument("--max-missing-jsonl", type=int, default=0)
    parser.add_argument("--max-missing-index", type=int, default=0)
    parser.add_argument(
        "--max-extra-jsonl",
        type=int,
        help="Fail when backup has more than this many JSONL transcripts absent from live state",
    )
    parser.add_argument(
        "--max-extra-index",
        type=int,
        help="Fail when backup sessions.json has more than this many entries absent from live state",
    )
    parser.add_argument(
        "--fail-on-state-legacy-gaps",
        action="store_true",
        help="Fail when live state.db has sessions not represented by current legacy session files",
    )
    parser.add_argument(
        "--fail-on-untracked-state-db",
        action="store_true",
        help="Fail when backup state.db exists but is not tracked by git",
    )
    args = parser.parse_args(argv)

    live_home = Path(args.live_home).expanduser().resolve()
    backup_dir = Path(args.backup_dir).expanduser().resolve()
    report = audit(live_home, backup_dir, max(0, args.sample_limit))

    text = json.dumps(report, indent=2, sort_keys=True)
    if args.report:
        path = Path(args.report).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text + "\n", encoding="utf-8")
    print(text)

    errors: list[str] = []
    if report["missing_jsonl_count"] > args.max_missing_jsonl:
        errors.append(
            f"missing_jsonl_count={report['missing_jsonl_count']} "
            f"> max_missing_jsonl={args.max_missing_jsonl}"
        )
    if args.max_extra_jsonl is not None and report["extra_jsonl_count"] > args.max_extra_jsonl:
        errors.append(
            f"extra_jsonl_count={report['extra_jsonl_count']} "
            f"> max_extra_jsonl={args.max_extra_jsonl}"
        )
    if report["backup_jsonl_message_drift_count"] > 0:
        errors.append(
            "backup_jsonl_message_drift_count="
            f"{report['backup_jsonl_message_drift_count']}"
        )
    if report["missing_sessions_json_entries"] > args.max_missing_index:
        errors.append(
            f"missing_sessions_json_entries={report['missing_sessions_json_entries']} "
            f"> max_missing_index={args.max_missing_index}"
        )
    if args.max_extra_index is not None and report["extra_sessions_json_entries"] > args.max_extra_index:
        errors.append(
            f"extra_sessions_json_entries={report['extra_sessions_json_entries']} "
            f"> max_extra_index={args.max_extra_index}"
        )
    if args.fail_on_untracked_state_db and report["backup_state_db_size_bytes"] and not report["backup_state_db_tracked"]:
        errors.append("backup state.db exists but is not tracked")
    if args.fail_on_state_legacy_gaps and report["live_state_sessions_without_legacy_files"]:
        errors.append(
            "live_state_sessions_without_legacy_files="
            f"{report['live_state_sessions_without_legacy_files']}"
        )
    if args.fail_on_state_legacy_gaps and report["live_message_sessions_without_jsonl"]:
        errors.append(
            "live_message_sessions_without_jsonl="
            f"{report['live_message_sessions_without_jsonl']}"
        )
    if args.fail_on_state_legacy_gaps and report["live_jsonl_message_mismatch_count"]:
        errors.append(
            "live_jsonl_message_mismatch_count="
            f"{report['live_jsonl_message_mismatch_count']}"
        )

    if errors:
        print("backup freshness failed: " + "; ".join(errors), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
