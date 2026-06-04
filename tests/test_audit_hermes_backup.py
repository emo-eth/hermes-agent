import json
import sqlite3
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
AUDIT_SCRIPT = ROOT / "scripts" / "audit_hermes_backup.py"


def write_session(root: Path, session_id: str, messages: int = 1) -> None:
    sessions = root / "sessions"
    sessions.mkdir(parents=True, exist_ok=True)
    with (sessions / f"{session_id}.jsonl").open("w", encoding="utf-8") as handle:
        for index in range(messages):
            handle.write(json.dumps({"role": "user", "content": f"message {index}"}) + "\n")


def write_index(root: Path, *session_ids: str) -> None:
    sessions = root / "sessions"
    sessions.mkdir(parents=True, exist_ok=True)
    data = {
        session_id: {"session_id": session_id, "title": session_id}
        for session_id in session_ids
    }
    (sessions / "sessions.json").write_text(json.dumps(data), encoding="utf-8")


def write_state_db(root: Path, *session_ids: str) -> None:
    conn = sqlite3.connect(root / "state.db")
    try:
        conn.execute("create table sessions (id text primary key)")
        conn.execute("create table messages (session_id text, role text, content text)")
        for session_id in session_ids:
            conn.execute("insert into sessions (id) values (?)", (session_id,))
            conn.execute(
                "insert into messages (session_id, role, content) values (?, ?, ?)",
                (session_id, "user", "message 0"),
            )
        conn.commit()
    finally:
        conn.close()


def run_audit(live: Path, backup: Path, *extra_args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(AUDIT_SCRIPT),
            "--live-home",
            str(live),
            "--backup-dir",
            str(backup),
            *extra_args,
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def test_audit_can_fail_on_extra_backup_jsonl(tmp_path: Path) -> None:
    live = tmp_path / "live"
    backup = tmp_path / "backup"
    write_session(live, "current")
    write_session(backup, "current")
    write_session(backup, "stale")
    write_index(live, "current")
    write_index(backup, "current")
    write_state_db(live, "current")

    result = run_audit(live, backup, "--max-extra-jsonl", "0")

    assert result.returncode == 1
    assert "extra_jsonl_count=1 > max_extra_jsonl=0" in result.stderr


def test_audit_can_fail_on_extra_backup_index_entry(tmp_path: Path) -> None:
    live = tmp_path / "live"
    backup = tmp_path / "backup"
    write_session(live, "current")
    write_session(backup, "current")
    write_index(live, "current")
    write_index(backup, "current", "stale")
    write_state_db(live, "current")

    result = run_audit(live, backup, "--max-extra-index", "0")

    assert result.returncode == 1
    assert "extra_sessions_json_entries=1 > max_extra_index=0" in result.stderr
