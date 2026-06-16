#!/usr/bin/env python3
"""Rebuild Hermes state.db from legacy sessions.json + JSONL transcripts.

This is a recovery utility for raw ~/.hermes backups that preserved legacy
session files but not the populated SQLite session index.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from hermes_constants import get_hermes_home
from hermes_state import SCHEMA_VERSION, SessionDB


def parse_ts(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return float(text)
        except ValueError:
            pass
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(text)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.timestamp()
        except ValueError:
            return None
    return None


def encode_content(content: Any) -> Any:
    return SessionDB._encode_content(content)


def load_sessions_index(sessions_dir: Path) -> dict[str, dict[str, Any]]:
    index_path = sessions_dir / "sessions.json"
    if not index_path.exists():
        return {}
    data = json.loads(index_path.read_text(encoding="utf-8"))
    by_id: dict[str, dict[str, Any]] = {}
    for entry in data.values():
        if not isinstance(entry, dict):
            continue
        session_id = entry.get("session_id")
        if isinstance(session_id, str) and session_id:
            by_id[session_id] = entry
    return by_id


def iter_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                rows.append(item)
    return rows


def infer_source(entry: dict[str, Any] | None, rows: list[dict[str, Any]]) -> str:
    if entry:
        platform = entry.get("platform")
        if isinstance(platform, str) and platform:
            return platform
        origin = entry.get("origin")
        if isinstance(origin, dict):
            platform = origin.get("platform")
            if isinstance(platform, str) and platform:
                return platform
    for row in rows:
        platform = row.get("platform")
        if isinstance(platform, str) and platform:
            return platform
    return "unknown"


def infer_started_at(entry: dict[str, Any] | None, rows: list[dict[str, Any]]) -> float:
    if entry:
        for key in ("created_at", "session_start", "started_at"):
            ts = parse_ts(entry.get(key))
            if ts is not None:
                return ts
    for row in rows:
        ts = parse_ts(row.get("timestamp"))
        if ts is not None:
            return ts
    return datetime.now(timezone.utc).timestamp()


def session_meta(rows: list[dict[str, Any]]) -> dict[str, Any]:
    for row in rows:
        if row.get("role") == "session_meta":
            return row
    return {}


def infer_title(entry: dict[str, Any] | None, rows: list[dict[str, Any]]) -> str | None:
    if entry:
        for key in ("display_name", "title", "chat_name"):
            value = entry.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()[:240]
    meta = session_meta(rows)
    for key in ("display_name", "title", "chat_name"):
        value = meta.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:240]
    for row in rows:
        if row.get("role") == "user":
            content = row.get("content")
            if isinstance(content, str) and content.strip():
                return " ".join(content.split())[:120]
    return None


def meta_value(meta: dict[str, Any], key: str) -> Any:
    value = meta.get(key)
    if isinstance(value, (dict, list)):
        return json.dumps(value)
    return value


def rebuild(hermes_home: Path, dry_run: bool = False) -> dict[str, int]:
    sessions_dir = hermes_home / "sessions"
    db_path = hermes_home / "state.db"
    if not sessions_dir.is_dir():
        raise SystemExit(f"No sessions directory found at {sessions_dir}")

    index = load_sessions_index(sessions_dir)
    jsonl_paths = sorted(
        p for p in sessions_dir.glob("*.jsonl")
        if not p.name.startswith("request_dump_")
    )
    session_ids = {p.stem for p in jsonl_paths} | set(index)

    if dry_run:
        total_messages = 0
        for path in jsonl_paths:
            total_messages += sum(1 for _ in path.open(encoding="utf-8"))
        return {
            "sessions_indexed": len(index),
            "jsonl_transcripts": len(jsonl_paths),
            "sessions_to_import": len(session_ids),
            "messages_seen": total_messages,
        }

    if db_path.exists():
        backup_path = db_path.with_name(
            f"state.db.pre-legacy-rebuild-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
        )
        shutil.copy2(db_path, backup_path)

    db = SessionDB(db_path)
    conn = db._conn

    conn.execute("DELETE FROM messages")
    conn.execute("DELETE FROM sessions")
    conn.execute("DELETE FROM messages_fts")
    conn.execute("DELETE FROM messages_fts_trigram")
    conn.execute("DELETE FROM sqlite_sequence WHERE name = 'messages'")
    conn.execute("DELETE FROM schema_version")
    conn.execute("INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,))

    imported_sessions = 0
    imported_messages = 0
    seen_titles: set[str] = set()

    for session_id in sorted(session_ids):
        entry = index.get(session_id)
        jsonl_path = sessions_dir / f"{session_id}.jsonl"
        rows = iter_jsonl(jsonl_path) if jsonl_path.exists() else []
        source = infer_source(entry, rows)
        started_at = infer_started_at(entry, rows)
        title = infer_title(entry, rows)
        if title:
            if title in seen_titles:
                title = None
            else:
                seen_titles.add(title)
        user_id = None
        model = None
        system_prompt = None
        parent_session_id = None
        model_config = None
        ended_at = None
        end_reason = None
        input_tokens = None
        output_tokens = None
        cache_read_tokens = None
        cache_write_tokens = None
        reasoning_tokens = None
        billing_provider = None
        billing_base_url = None
        billing_mode = None
        estimated_cost_usd = None
        actual_cost_usd = None
        cost_status = None
        cost_source = None
        pricing_version = None
        api_call_count = None
        handoff_state = None
        handoff_platform = None
        handoff_error = None

        if entry:
            user_id = entry.get("origin", {}).get("user_id") if isinstance(entry.get("origin"), dict) else None
            model = entry.get("model")
            parent_session_id = entry.get("parent_session_id")
            if parent_session_id not in session_ids:
                parent_session_id = None
        meta = session_meta(rows)
        model = model or meta.get("model")
        model_config = meta_value(meta, "model_config")
        system_prompt = system_prompt or meta.get("system_prompt")
        user_id = user_id or meta.get("user_id")
        if not parent_session_id:
            parent_session_id = meta.get("parent_session_id")
            if parent_session_id not in session_ids:
                parent_session_id = None
        ended_at = parse_ts(meta.get("ended_at"))
        end_reason = meta.get("end_reason")
        input_tokens = meta.get("input_tokens")
        output_tokens = meta.get("output_tokens")
        cache_read_tokens = meta.get("cache_read_tokens")
        cache_write_tokens = meta.get("cache_write_tokens")
        reasoning_tokens = meta.get("reasoning_tokens")
        billing_provider = meta.get("billing_provider")
        billing_base_url = meta.get("billing_base_url")
        billing_mode = meta.get("billing_mode")
        estimated_cost_usd = meta.get("estimated_cost_usd")
        actual_cost_usd = meta.get("actual_cost_usd")
        cost_status = meta.get("cost_status")
        cost_source = meta.get("cost_source")
        pricing_version = meta.get("pricing_version")
        api_call_count = meta.get("api_call_count")
        handoff_state = meta_value(meta, "handoff_state")
        handoff_platform = meta.get("handoff_platform")
        handoff_error = meta.get("handoff_error")

        conn.execute(
            """INSERT OR IGNORE INTO sessions
               (id, source, user_id, model, model_config, system_prompt,
                parent_session_id, started_at, ended_at, end_reason, input_tokens,
                output_tokens, cache_read_tokens, cache_write_tokens,
                reasoning_tokens, billing_provider, billing_base_url, billing_mode,
                estimated_cost_usd, actual_cost_usd, cost_status, cost_source,
                pricing_version, title, api_call_count, handoff_state,
                handoff_platform, handoff_error)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                session_id,
                source,
                user_id,
                model,
                model_config,
                system_prompt,
                parent_session_id,
                started_at,
                ended_at,
                end_reason,
                input_tokens,
                output_tokens,
                cache_read_tokens,
                cache_write_tokens,
                reasoning_tokens,
                billing_provider,
                billing_base_url,
                billing_mode,
                estimated_cost_usd,
                actual_cost_usd,
                cost_status,
                cost_source,
                pricing_version,
                title,
                api_call_count,
                handoff_state,
                handoff_platform,
                handoff_error,
            ),
        )
        imported_sessions += 1

        for row in rows:
            role = row.get("role")
            if not isinstance(role, str) or (role == "session_meta" and not row.get("db_message")):
                continue
            ts = parse_ts(row.get("timestamp")) or started_at
            tool_calls = row.get("tool_calls")
            conn.execute(
                """INSERT INTO messages
                   (session_id, role, content, tool_call_id, tool_calls,
                    tool_name, timestamp, token_count, finish_reason,
                    reasoning, reasoning_content, reasoning_details,
                    codex_reasoning_items, codex_message_items)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    session_id,
                    role,
                    encode_content(row.get("content")),
                    row.get("tool_call_id"),
                    json.dumps(tool_calls) if tool_calls else None,
                    row.get("tool_name"),
                    ts,
                    row.get("token_count"),
                    row.get("finish_reason"),
                    row.get("reasoning") if role == "assistant" else None,
                    row.get("reasoning_content") if role == "assistant" else None,
                    json.dumps(row.get("reasoning_details")) if row.get("reasoning_details") else None,
                    json.dumps(row.get("codex_reasoning_items")) if row.get("codex_reasoning_items") else None,
                    json.dumps(row.get("codex_message_items")) if row.get("codex_message_items") else None,
                ),
            )
            imported_messages += 1

    conn.execute(
        """UPDATE sessions
           SET message_count = (
             SELECT COUNT(*) FROM messages WHERE messages.session_id = sessions.id
           ),
           tool_call_count = (
             SELECT COUNT(*) FROM messages
             WHERE messages.session_id = sessions.id
               AND (messages.role = 'tool' OR messages.tool_calls IS NOT NULL)
           )"""
    )
    conn.commit()
    db.close()

    return {
        "sessions_indexed": len(index),
        "jsonl_transcripts": len(jsonl_paths),
        "sessions_imported": imported_sessions,
        "messages_imported": imported_messages,
    }


def existing_session_count(hermes_home: Path) -> int | None:
    db_path = hermes_home / "state.db"
    if not db_path.exists():
        return None
    try:
        conn = sqlite3.connect(str(db_path))
        try:
            row = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()
            return int(row[0]) if row else 0
        finally:
            conn.close()
    except sqlite3.Error:
        return None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--home", type=Path, default=get_hermes_home())
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--if-empty",
        action="store_true",
        help="Only rebuild when state.db has zero sessions or is unreadable.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Rebuild even when state.db already has sessions.",
    )
    args = parser.parse_args()
    home = args.home.expanduser().resolve()
    if args.if_empty and not args.force and not args.dry_run:
        count = existing_session_count(home)
        if count and count > 0:
            print(f"skipped: state.db already has {count} sessions")
            sys.exit(0)
    stats = rebuild(home, dry_run=args.dry_run)
    for key, value in stats.items():
        print(f"{key}: {value}")


if __name__ == "__main__":
    main()
