import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VALIDATOR = ROOT / "scripts" / "validate_hermes_restore_report.py"


def valid_report() -> dict[str, object]:
    return {
        "doctor_status": 0,
        "sessions_status": 0,
        "status_status": 0,
        "cron_status": 0,
        "backup_audit_status": 0,
        "session_count": "917",
        "message_count": "50527",
        "active_cron_count": "37",
        "total_cron_count": "38",
        "missing_required_cron": "",
        "missing_required_env": "",
        "auth_json_present": "yes",
        "active_legacy_path_files_after_normalize": "0",
        "active_legacy_backup_dest_paths_after_normalize": "0",
        "active_legacy_backup_runtime_paths_after_normalize": "0",
        "active_legacy_agent_dir_paths_after_normalize": "0",
        "path_normalize_replacements": "461",
        "path_normalize_skipped": "0",
        "smoke_output": "skipped",
        "backup_audit_missing_jsonl_count": "0",
        "backup_audit_extra_jsonl_count": "0",
        "backup_audit_missing_sessions_json_entries": "0",
        "backup_audit_extra_sessions_json_entries": "0",
        "backup_audit_jsonl_message_drift_count": "0",
        "backup_audit_live_jsonl_message_mismatch_count": "0",
        "backup_audit_live_state_sessions_without_legacy_files": "0",
        "backup_audit_live_message_sessions_without_jsonl": "0",
        "backup_state_db_size_bytes": "0",
        "backup_state_db_tracked": "False",
        "agent_commit": "a" * 40,
        "backup_commit": "b" * 40,
        "beads_commit": "c" * 40,
    }


def run_validator(tmp_path: Path, report: dict[str, object]) -> subprocess.CompletedProcess[str]:
    report_path = tmp_path / "report.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(VALIDATOR), str(report_path)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def test_validator_accepts_string_zero_backup_state_db_size(tmp_path: Path) -> None:
    result = run_validator(tmp_path, valid_report())

    assert result.returncode == 0
    assert '"backup_state_db_size_bytes": "0"' in result.stdout


def test_validator_rejects_backup_parity_drift(tmp_path: Path) -> None:
    report = valid_report()
    report["backup_audit_jsonl_message_drift_count"] = "1"

    result = run_validator(tmp_path, report)

    assert result.returncode == 1
    assert "backup_audit_jsonl_message_drift_count='1'" in result.stderr


def test_validator_rejects_tracked_backup_state_db(tmp_path: Path) -> None:
    report = valid_report()
    report["backup_state_db_tracked"] = "True"

    result = run_validator(tmp_path, report)

    assert result.returncode == 1
    assert "backup_state_db_tracked='True'" in result.stderr
