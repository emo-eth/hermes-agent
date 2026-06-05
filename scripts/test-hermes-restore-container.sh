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
cd /tmp/hermes-agent
uv run --python 3.11 python \
  scripts/validate_hermes_restore_report.py \
  "$HERMES_RESTORE_CONTAINER_REPORT"
'

echo "restore container test passed: $container_name"
