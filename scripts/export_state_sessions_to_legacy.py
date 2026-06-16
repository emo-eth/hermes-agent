#!/usr/bin/env python3
"""Export Hermes state.db sessions into legacy JSONL transcripts.

The workspace backup intentionally excludes state.db because it is large and
mutable. A recoverable backup therefore needs legacy session files that can
rebuild every live SQLite session and message. This utility fills gaps by
writing sessions/<session_id>.jsonl for state.db sessions that do not already
have one.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from hermes_constants import get_hermes_home
from hermes_state import SessionDB


SESSION_COLUMNS = [
    "id",
    "source",
    "user_id",
    "model",
    "model_config",
    "system_prompt",
    "parent_session_id",
    "started_at",
    "ended_at",
    "end_reason",
    "message_count",
    "tool_call_count",
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "reasoning_tokens",
    "billing_provider",
    "billing_base_url",
    "billing_mode",
    "estimated_cost_usd",
    "actual_cost_usd",
    "cost_status",
    "cost_source",
    "pricing_version",
    "title",
    "api_call_count",
    "handoff_state",
    "handoff_platform",
    "handoff_error",
]

MESSAGE_COLUMNS = [
    "id",
    "session_id",
    "role",
    "content",
    "tool_call_id",
    "tool_calls",
    "tool_name",
    "timestamp",
    "token_count",
    "finish_reason",
    "reasoning",
    "reasoning_content",
    "reasoning_details",
    "codex_reasoning_items",
    "codex_message_items",
]


def parse_json(value: Any) -> Any:
    if not isinstance(value, str) or not value:
        return value
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def jsonable(value: Any) -> Any:
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError:
            return value.hex()
    return value


def iso_ts(value: Any) -> str | None:
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(float(value), tz=timezone.utc).isoformat()
    except (TypeError, ValueError, OSError):
        return None


def existing_jsonl_ids(sessions_dir: Path) -> set[str]:
    if not sessions_dir.is_dir():
        return set()
    return {
        path.stem
        for path in sessions_dir.glob("*.jsonl")
        if not path.name.startswith("request_dump_")
    }


def jsonl_message_count(path: Path) -> int:
    if not path.exists():
        return 0
    count = 0
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict) and (row.get("role") != "session_meta" or row.get("db_message")):
                count += 1
    return count


def fetch_sessions(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        f"SELECT {', '.join(SESSION_COLUMNS)} FROM sessions ORDER BY started_at, id"
    ).fetchall()
    return {str(row["id"]): dict(row) for row in rows}


def fetch_messages(conn: sqlite3.Connection, session_id: str) -> list[dict[str, Any]]:
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        f"""SELECT {', '.join(MESSAGE_COLUMNS)}
            FROM messages
            WHERE session_id = ?
            ORDER BY timestamp, id""",
        (session_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def session_meta(session: dict[str, Any]) -> dict[str, Any]:
    started_at = iso_ts(session.get("started_at"))
    ended_at = iso_ts(session.get("ended_at"))
    meta: dict[str, Any] = {
        "role": "session_meta",
        "session_id": session["id"],
        "platform": session.get("source"),
        "source": session.get("source"),
        "user_id": session.get("user_id"),
        "model": session.get("model"),
        "model_config": parse_json(session.get("model_config")),
        "system_prompt": session.get("system_prompt"),
        "parent_session_id": session.get("parent_session_id"),
        "timestamp": started_at,
        "started_at": started_at,
        "ended_at": ended_at,
        "end_reason": session.get("end_reason"),
        "title": session.get("title"),
        "display_name": session.get("title"),
        "message_count": session.get("message_count"),
        "tool_call_count": session.get("tool_call_count"),
        "input_tokens": session.get("input_tokens"),
        "output_tokens": session.get("output_tokens"),
        "cache_read_tokens": session.get("cache_read_tokens"),
        "cache_write_tokens": session.get("cache_write_tokens"),
        "reasoning_tokens": session.get("reasoning_tokens"),
        "billing_provider": session.get("billing_provider"),
        "billing_base_url": session.get("billing_base_url"),
        "billing_mode": session.get("billing_mode"),
        "estimated_cost_usd": session.get("estimated_cost_usd"),
        "actual_cost_usd": session.get("actual_cost_usd"),
        "cost_status": session.get("cost_status"),
        "cost_source": session.get("cost_source"),
        "pricing_version": session.get("pricing_version"),
        "api_call_count": session.get("api_call_count"),
        "handoff_state": parse_json(session.get("handoff_state")),
        "handoff_platform": session.get("handoff_platform"),
        "handoff_error": session.get("handoff_error"),
    }
    return {key: jsonable(value) for key, value in meta.items() if value is not None}


def message_row(message: dict[str, Any]) -> dict[str, Any]:
    row: dict[str, Any] = {
        "role": message.get("role"),
        "content": SessionDB._decode_content(message.get("content")),
        "tool_call_id": message.get("tool_call_id"),
        "tool_calls": parse_json(message.get("tool_calls")),
        "tool_name": message.get("tool_name"),
        "timestamp": iso_ts(message.get("timestamp")),
        "token_count": message.get("token_count"),
        "finish_reason": message.get("finish_reason"),
        "reasoning": message.get("reasoning"),
        "reasoning_content": message.get("reasoning_content"),
        "reasoning_details": parse_json(message.get("reasoning_details")),
        "codex_reasoning_items": parse_json(message.get("codex_reasoning_items")),
        "codex_message_items": parse_json(message.get("codex_message_items")),
    }
    if message.get("role") == "session_meta":
        row["db_message"] = True
    return {key: jsonable(value) for key, value in row.items() if value is not None}


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    tmp.replace(path)


def export_sessions(
    hermes_home: Path,
    requested_ids: list[str],
    force: bool,
    dry_run: bool,
    refresh_mismatched: bool,
) -> dict[str, Any]:
    db_path = hermes_home / "state.db"
    sessions_dir = hermes_home / "sessions"
    if not db_path.exists():
        raise SystemExit(f"No state.db found at {db_path}")
    sessions_dir.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(db_path)
    try:
        sessions = fetch_sessions(conn)
        existing = existing_jsonl_ids(sessions_dir)
        if requested_ids:
            targets = [session_id for session_id in requested_ids if session_id in sessions]
            missing_requested = sorted(set(requested_ids) - set(targets))
        elif refresh_mismatched:
            targets = []
            for session_id, session in sessions.items():
                path = sessions_dir / f"{session_id}.jsonl"
                db_count = int(session.get("message_count") or 0)
                if not path.exists() or jsonl_message_count(path) != db_count:
                    targets.append(session_id)
            targets.sort()
            missing_requested = []
        else:
            targets = sorted(set(sessions) - existing)
            missing_requested = []

        skipped_existing = 0
        exported = 0
        exported_messages = 0
        samples: list[str] = []
        for session_id in targets:
            path = sessions_dir / f"{session_id}.jsonl"
            if path.exists() and not force and not refresh_mismatched:
                skipped_existing += 1
                continue
            messages = fetch_messages(conn, session_id)
            rows = [session_meta(sessions[session_id])] + [message_row(message) for message in messages]
            if not dry_run:
                write_jsonl(path, rows)
            exported += 1
            exported_messages += len(messages)
            if len(samples) < 30:
                samples.append(session_id)
    finally:
        conn.close()

    return {
        "home": str(hermes_home),
        "sessions_considered": len(sessions),
        "sessions_exported": exported,
        "messages_exported": exported_messages,
        "skipped_existing": skipped_existing,
        "missing_requested": missing_requested,
        "exported_sample": samples,
        "dry_run": dry_run,
        "refresh_mismatched": refresh_mismatched,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", type=Path, default=get_hermes_home())
    parser.add_argument(
        "--session-id",
        action="append",
        default=[],
        help="Export a specific state.db session. May be passed more than once.",
    )
    parser.add_argument("--force", action="store_true", help="Overwrite existing JSONL files.")
    parser.add_argument(
        "--refresh-mismatched",
        action="store_true",
        help="Export sessions whose JSONL message count differs from state.db, overwriting stale files.",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--report", help="Write JSON report to this path")
    args = parser.parse_args(argv)

    report = export_sessions(
        args.home.expanduser().resolve(),
        args.session_id,
        args.force,
        args.dry_run,
        args.refresh_mismatched,
    )
    text = json.dumps(report, indent=2, sort_keys=True)
    if args.report:
        path = Path(args.report).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text + "\n", encoding="utf-8")
    print(text)
    if report["missing_requested"]:
        print("requested sessions not found in state.db", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
