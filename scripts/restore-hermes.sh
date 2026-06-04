#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
usage: restore-hermes.sh [options]

Clone/pull Hermes repos, install Hermes with uv, restore a raw ~/.hermes
workspace backup, rebuild legacy session search if needed, and write a
machine-readable restore report.

Options:
  --start-gateway          Install/start the default gateway service after restore.
  --install-backup-scheduler
                           Install/start the macOS workspace backup LaunchAgent.
  --skip-backup-scheduler  Do not install the workspace backup LaunchAgent.
  --skip-smoke             Skip the LLM smoke prompt. Use for no-credential CI.
  --skip-path-normalize    Keep legacy /Users/emo/.hermes references untouched.
  --obsidian-vault DIR     Rewrite active restored Obsidian vault paths to DIR.
  --prompt-missing-env     Prompt for missing required env vars and write .env.
  --force-session-rebuild  Rebuild state.db even if it already has sessions.
  --required-cron CSV      Comma-separated cron job names that must restore.
  --required-env CSV       Comma-separated env var names that must be present.
  --agent-dir DIR          Use an existing hermes-agent checkout instead of gh clone.
  --backup-dir DIR         Use an existing hermes-workspace-backup checkout.
  --beads-dir DIR          Use an existing hermes-beads checkout.
  --repos-dir DIR          Parent directory for cloned repos (default: ~/dev).
  --hermes-home DIR        Restore target (default: ~/.hermes).
  --report PATH            Restore report path.
  -h, --help               Show this help.

Environment defaults:
  HERMES_AGENT_REPO=emo-eth/hermes-agent
  HERMES_BACKUP_REPO=emo-eth/hermes-workspace-backup
  HERMES_BEADS_REPO=emo-eth/hermes-beads
  HERMES_LEGACY_HOME=/Users/emo/.hermes
  HERMES_LEGACY_BACKUP_DEST=/Users/emo/Backups/hermes-workspace-backup
  HERMES_LEGACY_BACKUP_RUNTIME_DIR="/Users/emo/Library/Application Support/hermes-workspace-backup"
  HERMES_LEGACY_AGENT_DIRS=/Users/emo/dev/hermes-agent,/Users/jameswenzel/dev/hermes-agent
  HERMES_LEGACY_OBSIDIAN_VAULT=/Users/emo/Documents/Sync
  HERMES_OBSIDIAN_VAULT_PATH=<auto-detected from local Obsidian/Documents/Sync>
  HERMES_BACKUP_RUNTIME_DIR="$HOME/Library/Application Support/hermes-workspace-backup"
  HERMES_INSTALL_BACKUP_SCHEDULER=auto
  HERMES_BACKUP_SCHEDULER_INTERVAL=300
  HERMES_RESTORE_DRILL_MIN_INTERVAL_SECONDS=86400
  HERMES_PROMPT_MISSING_ENV=0
  HERMES_REQUIRED_CRON="daily-bedtime-reminder,..."
  HERMES_REQUIRED_ENV=DISCORD_BOT_TOKEN,DISCORD_ALLOWED_USERS
EOF
}

AGENT_REPO="${HERMES_AGENT_REPO:-emo-eth/hermes-agent}"
BACKUP_REPO="${HERMES_BACKUP_REPO:-emo-eth/hermes-workspace-backup}"
BEADS_REPO="${HERMES_BEADS_REPO:-emo-eth/hermes-beads}"
REPOS_DIR="${HERMES_REPOS_DIR:-$HOME/dev}"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
START_GATEWAY="${START_GATEWAY:-0}"
SKIP_SMOKE="${HERMES_SKIP_SMOKE:-0}"
SKIP_PATH_NORMALIZE="${HERMES_SKIP_PATH_NORMALIZE:-0}"
PROMPT_MISSING_ENV="${HERMES_PROMPT_MISSING_ENV:-0}"
FORCE_SESSION_REBUILD="${HERMES_FORCE_SESSION_REBUILD:-0}"
LEGACY_HOME="${HERMES_LEGACY_HOME:-/Users/emo/.hermes}"
LEGACY_BACKUP_DEST="${HERMES_LEGACY_BACKUP_DEST:-/Users/emo/Backups/hermes-workspace-backup}"
LEGACY_BACKUP_RUNTIME_DIR="${HERMES_LEGACY_BACKUP_RUNTIME_DIR:-/Users/emo/Library/Application Support/hermes-workspace-backup}"
LEGACY_AGENT_DIRS="${HERMES_LEGACY_AGENT_DIRS:-/Users/emo/dev/hermes-agent,/Users/jameswenzel/dev/hermes-agent}"
LEGACY_OBSIDIAN_VAULT="${HERMES_LEGACY_OBSIDIAN_VAULT:-/Users/emo/Documents/Sync}"
OBSIDIAN_VAULT_PATH="${HERMES_OBSIDIAN_VAULT_PATH:-}"
BACKUP_RUNTIME_DIR="${HERMES_BACKUP_RUNTIME_DIR:-$HOME/Library/Application Support/hermes-workspace-backup}"
REQUIRED_CRON="${HERMES_REQUIRED_CRON:-daily-bedtime-reminder,daily-date-night-monitor,personal-standup,work-standup,bmo-daily-standup,codex-token-monitor,weekly-backup}"
REQUIRED_ENV="${HERMES_REQUIRED_ENV:-DISCORD_BOT_TOKEN,DISCORD_ALLOWED_USERS}"
AGENT_DIR="${HERMES_AGENT_DIR:-}"
BACKUP_DIR="${HERMES_BACKUP_DIR:-}"
BEADS_DIR="${HERMES_BEADS_DIR:-}"
REPORT_PATH="${HERMES_RESTORE_REPORT:-}"
INSTALL_BACKUP_SCHEDULER="${HERMES_INSTALL_BACKUP_SCHEDULER:-auto}"
BACKUP_SCHEDULER_INTERVAL="${HERMES_BACKUP_SCHEDULER_INTERVAL:-300}"
RESTORE_DRILL_MIN_INTERVAL_SECONDS="${HERMES_RESTORE_DRILL_MIN_INTERVAL_SECONDS:-86400}"

while [ "$#" -gt 0 ]; do
  case "$1" in
    --start-gateway) START_GATEWAY=1 ;;
    --install-backup-scheduler) INSTALL_BACKUP_SCHEDULER=1 ;;
    --skip-backup-scheduler) INSTALL_BACKUP_SCHEDULER=0 ;;
    --skip-smoke) SKIP_SMOKE=1 ;;
    --skip-path-normalize) SKIP_PATH_NORMALIZE=1 ;;
    --obsidian-vault) OBSIDIAN_VAULT_PATH="${2:?missing DIR for --obsidian-vault}"; shift ;;
    --prompt-missing-env) PROMPT_MISSING_ENV=1 ;;
    --force-session-rebuild) FORCE_SESSION_REBUILD=1 ;;
    --required-cron) REQUIRED_CRON="${2:?missing CSV for --required-cron}"; shift ;;
    --required-env) REQUIRED_ENV="${2:?missing CSV for --required-env}"; shift ;;
    --agent-dir) AGENT_DIR="${2:?missing DIR for --agent-dir}"; shift ;;
    --backup-dir) BACKUP_DIR="${2:?missing DIR for --backup-dir}"; shift ;;
    --beads-dir) BEADS_DIR="${2:?missing DIR for --beads-dir}"; shift ;;
    --repos-dir) REPOS_DIR="${2:?missing DIR for --repos-dir}"; shift ;;
    --hermes-home) HERMES_HOME="${2:?missing DIR for --hermes-home}"; shift ;;
    --report) REPORT_PATH="${2:?missing PATH for --report}"; shift ;;
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

clone_or_pull() {
  local repo="$1"
  local dir="$2"
  local update="${3:-1}"
  if [ "$update" != "1" ] && [ -d "$dir" ]; then
    echo "using existing directory: $dir"
  elif [ -d "$dir/.git" ]; then
    git -C "$dir" pull --ff-only
  elif [ -d "$dir" ]; then
    echo "using existing directory without git metadata: $dir"
  else
    need gh
    gh repo clone "$repo" "$dir"
  fi
}

git_head() {
  local dir="$1"
  local override="${2:-}"
  if [ -n "$override" ]; then
    printf '%s\n' "$override"
    return
  fi
  if [ -d "$dir/.git" ] || [ -f "$dir/.git" ]; then
    git -C "$dir" rev-parse HEAD 2>/dev/null || true
  fi
}

json_escape() {
  printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g'
}

count_sessions() {
  local db_path="$1/state.db"
  if [ ! -f "$db_path" ]; then
    printf '0'
    return
  fi
  UV_PROJECT_ENVIRONMENT="$2/venv" uv run --frozen --python 3.11 python - "$db_path" <<'PY' 2>/dev/null || printf '0'
import sqlite3
import sys
try:
    conn = sqlite3.connect(sys.argv[1])
    try:
        print(conn.execute("select count(*) from sessions").fetchone()[0])
    finally:
        conn.close()
except Exception:
    print(0)
PY
}

count_legacy_paths() {
  local root="$1"
  if command -v rg >/dev/null 2>&1; then
    rg --hidden --glob '!.git/**' -l --fixed-strings '/Users/emo/.hermes' "$root" 2>/dev/null | wc -l | tr -d ' '
  else
    grep -RIl '/Users/emo/.hermes' "$root" 2>/dev/null | wc -l | tr -d ' '
  fi
}

env_file_has_value() {
  local env_file="$1"
  local key="$2"
  [ -f "$env_file" ] || return 1
  awk -F= -v key="$key" '
    $1 == key {
      value = substr($0, length(key) + 2)
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", value)
      gsub(/^["'\'']|["'\'']$/, "", value)
      if (value != "") found = 1
    }
    END { exit found ? 0 : 1 }
  ' "$env_file"
}

env_quote() {
  printf "'%s'" "$(printf '%s' "$1" | sed "s/'/'\\\\''/g")"
}

set_env_file_value() {
  local env_file="$1"
  local key="$2"
  local value="$3"
  local line
  local tmp

  if ! printf '%s\n' "$key" | grep -Eq '^[A-Za-z_][A-Za-z0-9_]*$'; then
    echo "invalid env var name: $key" >&2
    return 1
  fi

  mkdir -p "$(dirname "$env_file")"
  touch "$env_file"
  chmod 600 "$env_file"
  line="$key=$(env_quote "$value")"
  tmp="$(mktemp "${env_file}.XXXXXX")"
  awk -v key="$key" -v line="$line" '
    BEGIN { done = 0 }
    $0 ~ "^[[:space:]]*" key "=" {
      if (!done) {
        print line
        done = 1
      }
      next
    }
    { print }
    END {
      if (!done) print line
    }
  ' "$env_file" >"$tmp"
  mv "$tmp" "$env_file"
  chmod 600 "$env_file"
}

install_backup_scheduler() {
  local plist="$HOME/Library/LaunchAgents/ai.hermes.workspace-backup.plist"
  local label="ai.hermes.workspace-backup"
  local uid

  if [ "$(uname -s)" != "Darwin" ]; then
    printf 'skipped:not-macos:%s\n' "$plist"
    return 0
  fi

  if [ ! -x "$HERMES_HOME/hooks/backup_push.sh" ]; then
    chmod +x "$HERMES_HOME/hooks/backup_push.sh" 2>/dev/null || true
  fi
  if [ ! -x "$HERMES_HOME/hooks/backup_push.sh" ]; then
    printf 'failed:missing-hook:%s\n' "$plist"
    return 0
  fi

  mkdir -p "$HOME/Library/LaunchAgents" "$BACKUP_RUNTIME_DIR"
  cat >"$plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$label</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>-lc</string>
    <string>exec "$HERMES_HOME/hooks/backup_push.sh"</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>HERMES_HOME</key>
    <string>$HERMES_HOME</string>
    <key>HERMES_TRIGGER_RESTORE_DRILL</key>
    <string>1</string>
    <key>HERMES_RESTORE_DRILL_MIN_INTERVAL_SECONDS</key>
    <string>$RESTORE_DRILL_MIN_INTERVAL_SECONDS</string>
    <key>PATH</key>
    <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH</string>
  </dict>
  <key>StartInterval</key>
  <integer>$BACKUP_SCHEDULER_INTERVAL</integer>
  <key>RunAtLoad</key>
  <true/>
  <key>StandardOutPath</key>
  <string>$BACKUP_RUNTIME_DIR/workspace-backup.launchd.out.log</string>
  <key>StandardErrorPath</key>
  <string>$BACKUP_RUNTIME_DIR/workspace-backup.launchd.err.log</string>
  <key>WorkingDirectory</key>
  <string>$HOME</string>
</dict>
</plist>
EOF
  if command -v plutil >/dev/null 2>&1; then
    if ! plutil -lint "$plist" >/dev/null 2>&1; then
      printf 'failed:plist-lint:%s\n' "$plist"
      return 0
    fi
  fi
  uid="$(id -u)"
  launchctl bootout "gui/$uid" "$plist" >/dev/null 2>&1 || true
  if launchctl bootstrap "gui/$uid" "$plist" >/dev/null 2>&1; then
    printf 'installed:%s\n' "$plist"
  else
    printf 'failed:launchctl-bootstrap:%s\n' "$plist"
  fi
}

join_csv() {
  if [ "$#" -eq 0 ]; then
    printf ''
    return
  fi
  local joined="$1"
  shift
  local item
  for item in "$@"; do
    joined="$joined,$item"
  done
  printf '%s' "$joined"
}

csv_missing_env() {
  local csv="$1"
  local env_file="$2"
  local missing=()
  local old_ifs="$IFS"
  IFS=','
  for name in $csv; do
    IFS="$old_ifs"
    name="$(printf '%s' "$name" | sed 's/^[[:space:]]*//; s/[[:space:]]*$//')"
    if [ -n "$name" ]; then
      if [ -z "${!name:-}" ] && ! env_file_has_value "$env_file" "$name"; then
        missing+=("$name")
      fi
    fi
    IFS=','
  done
  IFS="$old_ifs"
  if [ "${#missing[@]}" -eq 0 ]; then
    printf ''
  else
    join_csv "${missing[@]}"
  fi
}

prompt_missing_env_values() {
  local csv="$1"
  local env_file="$2"
  local old_ifs="$IFS"
  local name
  local value
  local prompted=0

  if [ "$PROMPT_MISSING_ENV" != "1" ]; then
    return
  fi
  if [ ! -t 0 ]; then
    echo "cannot prompt for missing env vars: stdin is not a TTY" >&2
    return
  fi

  IFS=','
  for name in $csv; do
    IFS="$old_ifs"
    name="$(printf '%s' "$name" | sed 's/^[[:space:]]*//; s/[[:space:]]*$//')"
    if [ -n "$name" ] && [ -z "${!name:-}" ] && ! env_file_has_value "$env_file" "$name"; then
      if [ "$prompted" = "0" ]; then
        echo "Missing required Hermes restore env values. Input is hidden; leave blank to keep missing."
        prompted=1
      fi
      printf '%s: ' "$name" >&2
      IFS= read -r -s value
      printf '\n' >&2
      if [ -n "$value" ]; then
        set_env_file_value "$env_file" "$name" "$value"
      fi
    fi
    IFS=','
  done
  IFS="$old_ifs"
}

detect_obsidian_vault() {
  if [ -d "$HOME/Documents/Sync/.obsidian" ]; then
    printf '%s\n' "$HOME/Documents/Sync"
    return
  fi

  local obsidian_json="$HOME/Library/Application Support/obsidian/obsidian.json"
  if [ -f "$obsidian_json" ]; then
    sed -nE 's/.*"path":"([^"]+)".*/\1/p' "$obsidian_json" | head -n 1
  fi
}

json_field() {
  local field="$1"
  UV_PROJECT_ENVIRONMENT="$agent_dir/venv" uv run --frozen --python 3.11 \
    python -c 'import json,sys; print(json.load(sys.stdin).get(sys.argv[1], ""))' "$field" 2>/dev/null || true
}

csv_missing_from_file() {
  local csv="$1"
  local file="$2"
  local missing=()
  local old_ifs="$IFS"
  IFS=','
  for name in $csv; do
    IFS="$old_ifs"
    name="$(printf '%s' "$name" | sed 's/^[[:space:]]*//; s/[[:space:]]*$//')"
    if [ -n "$name" ] && ! grep -Fq "Name:      $name" "$file"; then
      missing+=("$name")
    fi
    IFS=','
  done
  IFS="$old_ifs"
  if [ "${#missing[@]}" -eq 0 ]; then
    printf ''
  else
    join_csv "${missing[@]}"
  fi
}

need git
need rsync
need uv
export HERMES_HOME

mkdir -p "$REPOS_DIR"

agent_dir="${AGENT_DIR:-$REPOS_DIR/hermes-agent}"
backup_dir="${BACKUP_DIR:-$REPOS_DIR/hermes-workspace-backup}"
beads_dir="${BEADS_DIR:-$REPOS_DIR/hermes-beads}"

agent_update=1
backup_update=1
beads_update=1
[ -n "$AGENT_DIR" ] && agent_update=0
[ -n "$BACKUP_DIR" ] && backup_update=0
[ -n "$BEADS_DIR" ] && beads_update=0

clone_or_pull "$AGENT_REPO" "$agent_dir" "$agent_update"
clone_or_pull "$BACKUP_REPO" "$backup_dir" "$backup_update"
clone_or_pull "$BEADS_REPO" "$beads_dir" "$beads_update"

agent_commit="$(git_head "$agent_dir" "${HERMES_AGENT_COMMIT:-}")"
backup_commit="$(git_head "$backup_dir" "${HERMES_BACKUP_COMMIT:-}")"
beads_commit="$(git_head "$beads_dir" "${HERMES_BEADS_COMMIT:-}")"

cd "$agent_dir"
printf 'n\n' | ./setup-hermes.sh

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
pre_restore=""
if [ -d "$HERMES_HOME" ]; then
  pre_restore="${HERMES_HOME}.pre-restore-${stamp}"
  mv "$HERMES_HOME" "$pre_restore"
fi

mkdir -p "$HERMES_HOME"
rsync -a --exclude='/.git/' "$backup_dir/" "$HERMES_HOME/"
chmod 700 "$HERMES_HOME"
for secret in "$HERMES_HOME/.env" "$HERMES_HOME/auth.json" "$HERMES_HOME/state.db"; do
  [ -e "$secret" ] && chmod 600 "$secret"
done

if [ "$SKIP_PATH_NORMALIZE" = "1" ]; then
  cat >/tmp/hermes-restore-path-normalize.json <<EOF
{
  "old_path": "$(json_escape "$LEGACY_HOME")",
  "new_path": "$(json_escape "$HERMES_HOME")",
  "total_legacy_path_files": "$(count_legacy_paths "$HERMES_HOME")",
  "active_legacy_path_files_before": "",
  "active_text_files_considered": "",
  "active_binary_files_skipped": "",
  "rewritten_files": "0",
  "replacements": "0",
  "active_legacy_path_files_after": "",
  "dry_run": false,
  "skipped": true
}
EOF
else
  UV_PROJECT_ENVIRONMENT="$agent_dir/venv" uv run --extra all --python 3.11 \
    python "$agent_dir/scripts/normalize_legacy_paths.py" \
      --home "$HERMES_HOME" \
      --old "$LEGACY_HOME" \
      --new "$HERMES_HOME" \
    >/tmp/hermes-restore-path-normalize.json
fi

if [ "$SKIP_PATH_NORMALIZE" = "1" ]; then
  cat >/tmp/hermes-restore-backup-dest-normalize.json <<EOF
{
  "old_path": "$(json_escape "$LEGACY_BACKUP_DEST")",
  "new_path": "$(json_escape "$backup_dir")",
  "rewritten_files": "0",
  "replacements": "0",
  "active_legacy_path_files_after": "",
  "skipped": true
}
EOF
  cat >/tmp/hermes-restore-backup-runtime-normalize.json <<EOF
{
  "old_path": "$(json_escape "$LEGACY_BACKUP_RUNTIME_DIR")",
  "new_path": "$(json_escape "$BACKUP_RUNTIME_DIR")",
  "rewritten_files": "0",
  "replacements": "0",
  "active_legacy_path_files_after": "",
  "skipped": true
}
EOF
else
  UV_PROJECT_ENVIRONMENT="$agent_dir/venv" uv run --extra all --python 3.11 \
    python "$agent_dir/scripts/normalize_legacy_paths.py" \
      --home "$HERMES_HOME" \
      --old "$LEGACY_BACKUP_DEST" \
      --new "$backup_dir" \
    >/tmp/hermes-restore-backup-dest-normalize.json
  UV_PROJECT_ENVIRONMENT="$agent_dir/venv" uv run --extra all --python 3.11 \
    python "$agent_dir/scripts/normalize_legacy_paths.py" \
      --home "$HERMES_HOME" \
      --old "$LEGACY_BACKUP_RUNTIME_DIR" \
      --new "$BACKUP_RUNTIME_DIR" \
    >/tmp/hermes-restore-backup-runtime-normalize.json
fi

if [ "$SKIP_PATH_NORMALIZE" = "1" ]; then
  cat >/tmp/hermes-restore-agent-dir-normalize.json <<EOF
{
  "old_paths": "$(json_escape "$LEGACY_AGENT_DIRS")",
  "new_path": "$(json_escape "$agent_dir")",
  "replacements": "0",
  "active_legacy_path_files_after": "",
  "skipped": true
}
EOF
else
  agent_dir_replacements=0
  agent_dir_active_after=0
  old_ifs="$IFS"
  IFS=','
  for old_agent_dir in $LEGACY_AGENT_DIRS; do
    IFS="$old_ifs"
    old_agent_dir="$(printf '%s' "$old_agent_dir" | sed 's/^[[:space:]]*//; s/[[:space:]]*$//')"
    if [ -n "$old_agent_dir" ] && [ "$old_agent_dir" != "$agent_dir" ]; then
      UV_PROJECT_ENVIRONMENT="$agent_dir/venv" uv run --extra all --python 3.11 \
        python "$agent_dir/scripts/normalize_legacy_paths.py" \
          --home "$HERMES_HOME" \
          --old "$old_agent_dir" \
          --new "$agent_dir" \
        >/tmp/hermes-restore-agent-dir-normalize-one.json
      one_replacements="$(json_field replacements </tmp/hermes-restore-agent-dir-normalize-one.json)"
      one_active_after="$(json_field active_legacy_path_files_after </tmp/hermes-restore-agent-dir-normalize-one.json)"
      agent_dir_replacements=$((agent_dir_replacements + ${one_replacements:-0}))
      agent_dir_active_after=$((agent_dir_active_after + ${one_active_after:-0}))
    fi
    IFS=','
  done
  IFS="$old_ifs"
  cat >/tmp/hermes-restore-agent-dir-normalize.json <<EOF
{
  "old_paths": "$(json_escape "$LEGACY_AGENT_DIRS")",
  "new_path": "$(json_escape "$agent_dir")",
  "replacements": "$agent_dir_replacements",
  "active_legacy_path_files_after": "$agent_dir_active_after",
  "skipped": false
}
EOF
fi

if [ -z "$OBSIDIAN_VAULT_PATH" ]; then
  OBSIDIAN_VAULT_PATH="$(detect_obsidian_vault)"
fi
if [ -n "$OBSIDIAN_VAULT_PATH" ]; then
  OBSIDIAN_VAULT_PATH="${OBSIDIAN_VAULT_PATH%/}"
fi

if [ "$SKIP_PATH_NORMALIZE" = "1" ] || [ -z "$OBSIDIAN_VAULT_PATH" ]; then
  cat >/tmp/hermes-restore-obsidian-normalize.json <<EOF
{
  "old_path": "$(json_escape "$LEGACY_OBSIDIAN_VAULT")",
  "new_path": "$(json_escape "$OBSIDIAN_VAULT_PATH")",
  "total_legacy_path_files": "",
  "active_legacy_path_files_before": "",
  "active_text_files_considered": "",
  "active_binary_files_skipped": "",
  "rewritten_files": "0",
  "replacements": "0",
  "active_legacy_path_files_after": "",
  "dry_run": false,
  "skipped": true
}
EOF
else
  UV_PROJECT_ENVIRONMENT="$agent_dir/venv" uv run --extra all --python 3.11 \
    python "$agent_dir/scripts/normalize_legacy_paths.py" \
      --home "$HERMES_HOME" \
      --old "$LEGACY_OBSIDIAN_VAULT" \
      --new "$OBSIDIAN_VAULT_PATH" \
    >/tmp/hermes-restore-obsidian-normalize.json
fi

prompt_missing_env_values "$REQUIRED_ENV" "$HERMES_HOME/.env"

session_count_before="$(count_sessions "$HERMES_HOME" "$agent_dir")"
rebuild_args=(--home "$HERMES_HOME" --if-empty)
if [ "$FORCE_SESSION_REBUILD" = "1" ]; then
  rebuild_args=(--home "$HERMES_HOME" --force)
fi
UV_PROJECT_ENVIRONMENT="$agent_dir/venv" uv run --extra all --python 3.11 \
  python "$agent_dir/scripts/rebuild_legacy_session_db.py" "${rebuild_args[@]}" \
  >/tmp/hermes-restore-rebuild.log 2>&1
session_count_after="$(count_sessions "$HERMES_HOME" "$agent_dir")"

doctor_status=0
sessions_status=0
status_status=0
cron_status=0
gateway_check_status=0
smoke_status=0

hermes doctor >/tmp/hermes-restore-doctor.log 2>&1 || doctor_status=$?
hermes sessions stats >/tmp/hermes-restore-sessions.log 2>&1 || sessions_status=$?
hermes status >/tmp/hermes-restore-status.log 2>&1 || status_status=$?
hermes cron list >/tmp/hermes-restore-cron.log 2>&1 || cron_status=$?
if [ "$SKIP_SMOKE" = "1" ]; then
  printf 'skipped\n' >/tmp/hermes-restore-smoke.log
else
  hermes -z 'Smoke test only: reply with exactly HERMES_SMOKE_OK.' --ignore-rules \
    >/tmp/hermes-restore-smoke.log 2>&1 || smoke_status=$?
fi

gateway_status="not_started"
if [ "$START_GATEWAY" = "1" ]; then
  hermes --accept-hooks gateway install
  hermes --accept-hooks gateway start
  gateway_status="started"
fi
hermes gateway status >/tmp/hermes-restore-gateway-status.log 2>&1 || gateway_check_status=$?

backup_scheduler_status="skipped"
backup_scheduler_plist=""
if [ "$INSTALL_BACKUP_SCHEDULER" = "auto" ]; then
  if [ "$HERMES_HOME" = "$HOME/.hermes" ]; then
    INSTALL_BACKUP_SCHEDULER=1
  else
    INSTALL_BACKUP_SCHEDULER=0
  fi
fi
if [ "$INSTALL_BACKUP_SCHEDULER" = "1" ]; then
  backup_scheduler_result="$(install_backup_scheduler)"
  backup_scheduler_status="${backup_scheduler_result%%:*}"
  backup_scheduler_plist="${backup_scheduler_result#*:}"
  if [ "$backup_scheduler_plist" = "$backup_scheduler_status" ]; then
    backup_scheduler_plist=""
  else
    backup_scheduler_plist="${backup_scheduler_plist#*:}"
  fi
fi

session_count="$(awk '/^Total sessions:/ {print $3}' /tmp/hermes-restore-sessions.log 2>/dev/null || true)"
message_count="$(awk '/^Total messages:/ {print $3}' /tmp/hermes-restore-sessions.log 2>/dev/null || true)"
smoke_output="$(tr -d '\r' </tmp/hermes-restore-smoke.log | tail -n 1)"
legacy_path_file_count="$(count_legacy_paths "$HERMES_HOME")"
cron_jobs_line="$(grep -E 'Jobs:[[:space:]]+[0-9]+ active, [0-9]+ total' /tmp/hermes-restore-status.log 2>/dev/null | tail -n 1 || true)"
active_cron_count="$(printf '%s\n' "$cron_jobs_line" | sed -nE 's/.*Jobs:[[:space:]]+([0-9]+) active, ([0-9]+) total.*/\1/p')"
total_cron_count="$(printf '%s\n' "$cron_jobs_line" | sed -nE 's/.*Jobs:[[:space:]]+([0-9]+) active, ([0-9]+) total.*/\2/p')"
gateway_running="unknown"
if grep -Eq 'Status:[[:space:]]+.*running|Gateway service is loaded|Gateway running' /tmp/hermes-restore-gateway-status.log /tmp/hermes-restore-status.log 2>/dev/null; then
  gateway_running="yes"
elif grep -Eq 'not installed|not running|stopped|No gateway' /tmp/hermes-restore-gateway-status.log /tmp/hermes-restore-status.log 2>/dev/null; then
  gateway_running="no"
fi
missing_required_cron="$(csv_missing_from_file "$REQUIRED_CRON" /tmp/hermes-restore-cron.log)"
missing_required_env="$(csv_missing_env "$REQUIRED_ENV" "$HERMES_HOME/.env")"
auth_json_present="no"
[ -s "$HERMES_HOME/auth.json" ] && auth_json_present="yes"
path_normalize_json="$(cat /tmp/hermes-restore-path-normalize.json)"
obsidian_normalize_json="$(cat /tmp/hermes-restore-obsidian-normalize.json)"
path_total_legacy="$(printf '%s\n' "$path_normalize_json" | json_field total_legacy_path_files)"
path_active_before="$(printf '%s\n' "$path_normalize_json" | json_field active_legacy_path_files_before)"
path_active_after="$(printf '%s\n' "$path_normalize_json" | json_field active_legacy_path_files_after)"
path_rewritten_files="$(printf '%s\n' "$path_normalize_json" | json_field rewritten_files)"
path_replacements="$(printf '%s\n' "$path_normalize_json" | json_field replacements)"
backup_dest_normalize_json="$(cat /tmp/hermes-restore-backup-dest-normalize.json)"
backup_runtime_normalize_json="$(cat /tmp/hermes-restore-backup-runtime-normalize.json)"
agent_dir_normalize_json="$(cat /tmp/hermes-restore-agent-dir-normalize.json)"
backup_dest_replacements="$(printf '%s\n' "$backup_dest_normalize_json" | json_field replacements)"
backup_dest_active_after="$(printf '%s\n' "$backup_dest_normalize_json" | json_field active_legacy_path_files_after)"
backup_runtime_replacements="$(printf '%s\n' "$backup_runtime_normalize_json" | json_field replacements)"
backup_runtime_active_after="$(printf '%s\n' "$backup_runtime_normalize_json" | json_field active_legacy_path_files_after)"
agent_dir_replacements="$(printf '%s\n' "$agent_dir_normalize_json" | json_field replacements)"
agent_dir_active_after="$(printf '%s\n' "$agent_dir_normalize_json" | json_field active_legacy_path_files_after)"
obsidian_active_before="$(printf '%s\n' "$obsidian_normalize_json" | json_field active_legacy_path_files_before)"
obsidian_active_after="$(printf '%s\n' "$obsidian_normalize_json" | json_field active_legacy_path_files_after)"
obsidian_rewritten_files="$(printf '%s\n' "$obsidian_normalize_json" | json_field rewritten_files)"
obsidian_replacements="$(printf '%s\n' "$obsidian_normalize_json" | json_field replacements)"
obsidian_skipped="$(printf '%s\n' "$obsidian_normalize_json" | json_field skipped)"
if [ "$obsidian_skipped" = "True" ] || [ "$obsidian_skipped" = "true" ]; then
  obsidian_skipped="1"
elif [ -z "$obsidian_skipped" ] || [ "$obsidian_skipped" = "False" ] || [ "$obsidian_skipped" = "false" ]; then
  obsidian_skipped="0"
fi

report="${REPORT_PATH:-$HERMES_HOME/restore-report-${stamp}.json}"
mkdir -p "$(dirname "$report")"
cat >"$report" <<EOF
{
  "timestamp": "$(json_escape "$stamp")",
  "agent_repo": "$(json_escape "$AGENT_REPO")",
  "backup_repo": "$(json_escape "$BACKUP_REPO")",
  "beads_repo": "$(json_escape "$BEADS_REPO")",
  "agent_commit": "$(json_escape "$agent_commit")",
  "backup_commit": "$(json_escape "$backup_commit")",
  "beads_commit": "$(json_escape "$beads_commit")",
  "agent_dir": "$(json_escape "$agent_dir")",
  "backup_dir": "$(json_escape "$backup_dir")",
  "beads_dir": "$(json_escape "$beads_dir")",
  "hermes_home": "$(json_escape "$HERMES_HOME")",
  "pre_restore_backup": "$(json_escape "$pre_restore")",
  "doctor_status": $doctor_status,
  "sessions_status": $sessions_status,
  "status_status": $status_status,
  "cron_status": $cron_status,
  "gateway_check_status": $gateway_check_status,
  "smoke_status": $smoke_status,
  "smoke_output": "$(json_escape "$smoke_output")",
  "session_count_before_rebuild": "$session_count_before",
  "session_count_after_rebuild": "$session_count_after",
  "session_count": "${session_count:-}",
  "message_count": "${message_count:-}",
  "active_cron_count": "${active_cron_count:-}",
  "total_cron_count": "${total_cron_count:-}",
  "required_cron": "$(json_escape "$REQUIRED_CRON")",
  "missing_required_cron": "$(json_escape "$missing_required_cron")",
  "required_env": "$(json_escape "$REQUIRED_ENV")",
  "missing_required_env": "$(json_escape "$missing_required_env")",
  "auth_json_present": "$(json_escape "$auth_json_present")",
  "legacy_hermes_path_file_count": "$legacy_path_file_count",
  "legacy_path_file_count_before_normalize": "${path_total_legacy:-}",
  "active_legacy_path_files_before_normalize": "${path_active_before:-}",
  "active_legacy_path_files_after_normalize": "${path_active_after:-}",
  "path_normalize_rewritten_files": "${path_rewritten_files:-}",
  "path_normalize_replacements": "${path_replacements:-}",
  "path_normalize_skipped": "$SKIP_PATH_NORMALIZE",
  "backup_dest_path_normalize_replacements": "${backup_dest_replacements:-}",
  "active_legacy_backup_dest_paths_after_normalize": "${backup_dest_active_after:-}",
  "backup_runtime_path_normalize_replacements": "${backup_runtime_replacements:-}",
  "active_legacy_backup_runtime_paths_after_normalize": "${backup_runtime_active_after:-}",
  "agent_dir_path_normalize_replacements": "${agent_dir_replacements:-}",
  "active_legacy_agent_dir_paths_after_normalize": "${agent_dir_active_after:-}",
  "legacy_obsidian_vault": "$(json_escape "$LEGACY_OBSIDIAN_VAULT")",
  "obsidian_vault_path": "$(json_escape "$OBSIDIAN_VAULT_PATH")",
  "active_legacy_obsidian_path_files_before_normalize": "${obsidian_active_before:-}",
  "active_legacy_obsidian_path_files_after_normalize": "${obsidian_active_after:-}",
  "obsidian_path_normalize_rewritten_files": "${obsidian_rewritten_files:-}",
  "obsidian_path_normalize_replacements": "${obsidian_replacements:-}",
  "obsidian_path_normalize_skipped": "${obsidian_skipped:-}",
  "gateway_status": "$(json_escape "$gateway_status")",
  "gateway_running": "$(json_escape "$gateway_running")",
  "backup_scheduler_status": "$(json_escape "$backup_scheduler_status")",
  "backup_scheduler_plist": "$(json_escape "$backup_scheduler_plist")",
  "backup_scheduler_interval_seconds": "$(json_escape "$BACKUP_SCHEDULER_INTERVAL")",
  "restore_drill_min_interval_seconds": "$(json_escape "$RESTORE_DRILL_MIN_INTERVAL_SECONDS")"
}
EOF

cat "$report"
echo
echo "restore report: $report"
