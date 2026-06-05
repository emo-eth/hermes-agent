#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
usage: test-hermes-restore-container.sh [options]

Run the Hermes restore flow inside a clean Docker container using mounted
local checkouts. This is intended to catch backup portability regressions
without touching the host's live ~/.hermes or launchd gateway.

Options:
  --agent-dir DIR    hermes-agent checkout (default: this repo)
  --backup-dir DIR   hermes-workspace-backup checkout (default: ~/dev/hermes-workspace-backup)
  --beads-dir DIR    hermes-beads checkout (default: ~/dev/hermes-beads)
  --image IMAGE      base image (default: debian:13.4)
  --report PATH      Copy the restore report JSON to this host path.
  --keep             Keep the container after failure for inspection.
  -h, --help         Show this help.
EOF
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGENT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
BACKUP_DIR="$HOME/dev/hermes-workspace-backup"
BEADS_DIR="$HOME/dev/hermes-beads"
IMAGE="${HERMES_RESTORE_TEST_IMAGE:-debian:13.4}"
KEEP=0
REPORT_PATH=""

while [ "$#" -gt 0 ]; do
  case "$1" in
    --agent-dir) AGENT_DIR="${2:?missing DIR for --agent-dir}"; shift ;;
    --backup-dir) BACKUP_DIR="${2:?missing DIR for --backup-dir}"; shift ;;
    --beads-dir) BEADS_DIR="${2:?missing DIR for --beads-dir}"; shift ;;
    --image) IMAGE="${2:?missing IMAGE for --image}"; shift ;;
    --report) REPORT_PATH="${2:?missing PATH for --report}"; shift ;;
    --keep) KEEP=1 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

need() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "missing required command: $1" >&2
    exit 1
  fi
}

need docker

for dir in "$AGENT_DIR" "$BACKUP_DIR" "$BEADS_DIR"; do
  if [ ! -d "$dir" ]; then
    echo "missing directory: $dir" >&2
    exit 1
  fi
done

git_head() {
  if [ -d "$1/.git" ] || [ -f "$1/.git" ]; then
    git -C "$1" rev-parse HEAD 2>/dev/null || true
  fi
}

agent_commit="$(git_head "$AGENT_DIR")"
backup_commit="$(git_head "$BACKUP_DIR")"
beads_commit="$(git_head "$BEADS_DIR")"

container_name="hermes-restore-test-$(date -u +%Y%m%d%H%M%S)"
rm_arg="--rm"
if [ "$KEEP" = "1" ]; then
  rm_arg=""
fi

report_mount_args=()
container_report_path="/tmp/hermes-restore-report.json"
if [ -n "$REPORT_PATH" ]; then
  mkdir -p "$(dirname "$REPORT_PATH")"
  report_path_abs="$(cd "$(dirname "$REPORT_PATH")" && pwd)/$(basename "$REPORT_PATH")"
  report_dir="$(dirname "$report_path_abs")"
  report_mount_args=(-v "$report_dir:/work/restore-report")
  container_report_path="/work/restore-report/$(basename "$REPORT_PATH")"
fi

docker run $rm_arg --name "$container_name" \
  -e HERMES_SKIP_SMOKE=1 \
  -e HERMES_AGENT_COMMIT="$agent_commit" \
  -e HERMES_BACKUP_COMMIT="$backup_commit" \
  -e HERMES_BEADS_COMMIT="$beads_commit" \
  -v "$AGENT_DIR:/work/hermes-agent-src:ro" \
  -v "$BACKUP_DIR:/work/hermes-workspace-backup:ro" \
  -v "$BEADS_DIR:/work/hermes-beads:ro" \
  "${report_mount_args[@]}" \
  -e HERMES_RESTORE_CONTAINER_REPORT="$container_report_path" \
  "$IMAGE" bash -lc '
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends \
  ca-certificates curl git openssh-client rsync build-essential pkg-config \
  python3-dev ffmpeg ripgrep nodejs npm
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"
cp -a /work/hermes-agent-src /tmp/hermes-agent
/tmp/hermes-agent/scripts/restore-hermes.sh \
  --agent-dir /tmp/hermes-agent \
  --backup-dir /work/hermes-workspace-backup \
  --beads-dir /work/hermes-beads \
  --hermes-home /tmp/hermes-home \
  --skip-smoke \
  --report "$HERMES_RESTORE_CONTAINER_REPORT"
python3 - <<'"'"'PY'"'"'
import json
from pathlib import Path

report = json.loads(Path(__import__("os").environ["HERMES_RESTORE_CONTAINER_REPORT"]).read_text())
errors = []
if report.get("doctor_status") not in (0, 1):
    errors.append(f"unexpected doctor_status={report.get('doctor_status')}")
if report.get("sessions_status") != 0:
    errors.append(f"sessions_status={report.get('sessions_status')}")
if report.get("status_status") != 0:
    errors.append(f"status_status={report.get('status_status')}")
if report.get("cron_status") != 0:
    errors.append(f"cron_status={report.get('cron_status')}")
if report.get("backup_audit_status") != 0:
    errors.append(f"backup_audit_status={report.get('backup_audit_status')}")
try:
    sessions = int(report.get("session_count") or 0)
except ValueError:
    sessions = 0
if sessions <= 0:
    errors.append(f"session_count={report.get('session_count')!r}")
try:
    messages = int(report.get("message_count") or 0)
except ValueError:
    messages = 0
if messages <= 0:
    errors.append(f"message_count={report.get('message_count')!r}")
try:
    active_cron = int(report.get("active_cron_count") or 0)
except ValueError:
    active_cron = 0
if active_cron <= 0:
    errors.append(f"active_cron_count={report.get('active_cron_count')!r}")
try:
    total_cron = int(report.get("total_cron_count") or 0)
except ValueError:
    total_cron = 0
if total_cron <= 0:
    errors.append(f"total_cron_count={report.get('total_cron_count')!r}")
if report.get("missing_required_cron"):
    errors.append(f"missing_required_cron={report.get('missing_required_cron')!r}")
if report.get("missing_required_env"):
    errors.append(f"missing_required_env={report.get('missing_required_env')!r}")
if report.get("auth_json_present") != "yes":
    errors.append(f"auth_json_present={report.get('auth_json_present')!r}")
try:
    active_legacy_after = int(report.get("active_legacy_path_files_after_normalize") or 0)
except ValueError:
    active_legacy_after = -1
if active_legacy_after != 0:
    errors.append(f"active_legacy_path_files_after_normalize={report.get('active_legacy_path_files_after_normalize')!r}")
for key in (
    "active_legacy_backup_dest_paths_after_normalize",
    "active_legacy_backup_runtime_paths_after_normalize",
    "active_legacy_agent_dir_paths_after_normalize",
):
    value = report.get(key)
    if value not in ("", None):
        try:
            count = int(value or 0)
        except ValueError:
            count = -1
        if count != 0:
            errors.append(f"{key}={value!r}")
try:
    path_replacements = int(report.get("path_normalize_replacements") or 0)
except ValueError:
    path_replacements = 0
if path_replacements <= 0:
    errors.append(f"path_normalize_replacements={report.get('path_normalize_replacements')!r}")
if str(report.get("path_normalize_skipped")) != "0":
    errors.append(f"path_normalize_skipped={report.get('path_normalize_skipped')!r}")
if report.get("smoke_output") != "skipped":
    errors.append(f"smoke_output={report.get('smoke_output')!r}")
if report.get("backup_state_db_size_bytes"):
    errors.append(f"backup_state_db_size_bytes={report.get('backup_state_db_size_bytes')!r}")
for key in (
    "backup_audit_missing_jsonl_count",
    "backup_audit_extra_jsonl_count",
    "backup_audit_missing_sessions_json_entries",
    "backup_audit_extra_sessions_json_entries",
    "backup_audit_jsonl_message_drift_count",
    "backup_audit_live_jsonl_message_mismatch_count",
    "backup_audit_live_state_sessions_without_legacy_files",
    "backup_audit_live_message_sessions_without_jsonl",
):
    try:
        count = int(report.get(key) or 0)
    except ValueError:
        count = -1
    if count != 0:
        errors.append(f"{key}={report.get(key)!r}")
for key in ("agent_commit", "backup_commit", "beads_commit"):
    if not report.get(key):
        errors.append(f"{key}=missing")
if errors:
    raise SystemExit("restore test failed: " + "; ".join(errors))
print(json.dumps(report, indent=2))
PY
'

echo "restore container test passed: $container_name"
