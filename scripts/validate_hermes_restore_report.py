#!/usr/bin/env python3
"""Validate a clean-container Hermes restore report."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


ZERO_COUNT_FIELDS = (
    "backup_audit_missing_jsonl_count",
    "backup_audit_extra_jsonl_count",
    "backup_audit_missing_sessions_json_entries",
    "backup_audit_extra_sessions_json_entries",
    "backup_audit_jsonl_message_drift_count",
    "backup_audit_live_jsonl_message_mismatch_count",
    "backup_audit_live_state_sessions_without_legacy_files",
    "backup_audit_live_message_sessions_without_jsonl",
    "backup_state_db_size_bytes",
)

ZERO_NORMALIZED_PATH_FIELDS = (
    "active_legacy_path_files_after_normalize",
    "active_legacy_backup_dest_paths_after_normalize",
    "active_legacy_backup_runtime_paths_after_normalize",
    "active_legacy_agent_dir_paths_after_normalize",
)


def as_int(report: dict[str, Any], key: str, default: int = 0) -> int:
    value = report.get(key)
    if value in ("", None):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return -1


def validate(report: dict[str, Any]) -> list[str]:
    errors: list[str] = []

    doctor_status = as_int(report, "doctor_status", default=-1)
    if doctor_status not in (0, 1):
        errors.append(f"unexpected doctor_status={report.get('doctor_status')}")

    for key in ("sessions_status", "status_status", "cron_status", "backup_audit_status"):
        if as_int(report, key, default=-1) != 0:
            errors.append(f"{key}={report.get(key)}")

    for key in ("session_count", "message_count", "active_cron_count", "total_cron_count"):
        if as_int(report, key) <= 0:
            errors.append(f"{key}={report.get(key)!r}")

    if report.get("missing_required_cron"):
        errors.append(f"missing_required_cron={report.get('missing_required_cron')!r}")
    if report.get("missing_required_env"):
        errors.append(f"missing_required_env={report.get('missing_required_env')!r}")
    if report.get("auth_json_present") != "yes":
        errors.append(f"auth_json_present={report.get('auth_json_present')!r}")

    for key in ZERO_NORMALIZED_PATH_FIELDS:
        if as_int(report, key) != 0:
            errors.append(f"{key}={report.get(key)!r}")

    if as_int(report, "path_normalize_replacements") <= 0:
        errors.append(f"path_normalize_replacements={report.get('path_normalize_replacements')!r}")
    if str(report.get("path_normalize_skipped")) != "0":
        errors.append(f"path_normalize_skipped={report.get('path_normalize_skipped')!r}")
    if report.get("smoke_output") != "skipped":
        errors.append(f"smoke_output={report.get('smoke_output')!r}")

    for key in ZERO_COUNT_FIELDS:
        if as_int(report, key) != 0:
            errors.append(f"{key}={report.get(key)!r}")

    if str(report.get("backup_state_db_tracked")) not in ("False", "false", "0", ""):
        errors.append(f"backup_state_db_tracked={report.get('backup_state_db_tracked')!r}")

    for key in ("agent_commit", "backup_commit", "beads_commit"):
        if not report.get(key):
            errors.append(f"{key}=missing")

    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path, help="restore report JSON path")
    args = parser.parse_args()

    report = json.loads(args.report.read_text(encoding="utf-8"))
    errors = validate(report)
    if errors:
        raise SystemExit("restore test failed: " + "; ".join(errors))
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
