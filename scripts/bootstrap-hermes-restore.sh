#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
usage: bootstrap-hermes-restore.sh [restore-hermes.sh options]

Install/check the small host prerequisites for a Hermes restore, then run
restore-hermes.sh. This is the paste-on-a-fresh-machine entrypoint; all Python
installation and dependency work remains delegated to uv through
restore-hermes.sh/setup-hermes.sh.

Options:
  --check-only                 Verify prerequisites and exit without restoring.

Default restore options:
  --start-gateway --prompt-missing-env

Environment:
  HERMES_RESTORE_SCRIPT_URL  Raw restore-hermes.sh URL to download when this
                             script is not running from a repo checkout.
  HERMES_BOOTSTRAP_NO_INSTALL=1
                             Only check prerequisites; do not install them.
EOF
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOCAL_RESTORE_SCRIPT="$SCRIPT_DIR/restore-hermes.sh"
RESTORE_SCRIPT_URL="${HERMES_RESTORE_SCRIPT_URL:-https://raw.githubusercontent.com/emo-eth/hermes-agent/main/scripts/restore-hermes.sh}"
NO_INSTALL="${HERMES_BOOTSTRAP_NO_INSTALL:-0}"
CHECK_ONLY=0

if [ "${1:-}" = "-h" ] || [ "${1:-}" = "--help" ]; then
  usage
  exit 0
fi
if [ "${1:-}" = "--check-only" ]; then
  CHECK_ONLY=1
  shift
fi

have() {
  command -v "$1" >/dev/null 2>&1
}

run_as_root() {
  if [ "$(id -u)" -eq 0 ]; then
    "$@"
  elif have sudo; then
    sudo "$@"
  else
    echo "missing required command: sudo (rerun as root or install prerequisites manually)" >&2
    exit 1
  fi
}

install_uv() {
  if have uv; then
    return
  fi
  if [ "$NO_INSTALL" = "1" ]; then
    echo "missing required command: uv" >&2
    exit 1
  fi
  echo "Installing uv..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
  if ! have uv; then
    echo "uv install did not place uv on PATH" >&2
    exit 1
  fi
}

install_with_homebrew() {
  if [ "$NO_INSTALL" = "1" ]; then
    return 1
  fi
  if have brew; then
    brew install "$@"
    return 0
  fi
  return 1
}

install_macos_prereqs() {
  missing=()
  for cmd in git rsync curl; do
    have "$cmd" || missing+=("$cmd")
  done
  if ! have gh; then
    missing+=(gh)
  fi
  if [ "${#missing[@]}" -gt 0 ]; then
    if ! install_with_homebrew "${missing[@]}"; then
      cat >&2 <<EOF
Missing required commands: ${missing[*]}

Install Apple's command line tools and GitHub CLI, then rerun:
  xcode-select --install
  brew install gh
EOF
      exit 1
    fi
  fi
}

install_linux_prereqs() {
  base_missing=()
  for cmd in git rsync curl; do
    have "$cmd" || base_missing+=("$cmd")
  done
  if [ "${#base_missing[@]}" -eq 0 ] && have gh; then
    return
  fi
  if [ "$NO_INSTALL" = "1" ]; then
    missing=("${base_missing[@]}")
    have gh || missing+=(gh)
    echo "missing required commands: ${missing[*]}" >&2
    exit 1
  fi
  if have apt-get; then
    run_as_root apt-get update
    if [ "${#base_missing[@]}" -gt 0 ]; then
      run_as_root apt-get install -y git rsync curl ca-certificates
    fi
    if ! have gh; then
      run_as_root install -m 0755 -d /etc/apt/keyrings
      curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
        | run_as_root tee /etc/apt/keyrings/githubcli-archive-keyring.gpg >/dev/null
      run_as_root chmod go+r /etc/apt/keyrings/githubcli-archive-keyring.gpg
      arch="$(dpkg --print-architecture)"
      echo "deb [arch=$arch signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
        | run_as_root tee /etc/apt/sources.list.d/github-cli.list >/dev/null
      run_as_root apt-get update
      run_as_root apt-get install -y gh
    fi
  elif have dnf; then
    run_as_root dnf install -y git rsync curl gh
  elif have yum; then
    run_as_root yum install -y git rsync curl gh
  else
    missing=("${base_missing[@]}")
    have gh || missing+=(gh)
    echo "missing required commands: ${missing[*]}" >&2
    echo "Install git, rsync, curl, and gh with your package manager, then rerun." >&2
    exit 1
  fi
}

ensure_prereqs() {
  case "$(uname -s)" in
    Darwin) install_macos_prereqs ;;
    Linux) install_linux_prereqs ;;
    *)
      for cmd in git rsync curl gh; do
        if ! have "$cmd"; then
          echo "missing required command: $cmd" >&2
          exit 1
        fi
      done
      ;;
  esac
  install_uv
  if ! gh auth status >/dev/null 2>&1; then
    cat >&2 <<'EOF'
GitHub CLI is installed but not authenticated.

Run:
  gh auth login

Then rerun this bootstrap command.
EOF
    exit 1
  fi
}

restore_script_path() {
  if [ -x "$LOCAL_RESTORE_SCRIPT" ]; then
    printf '%s\n' "$LOCAL_RESTORE_SCRIPT"
    return
  fi
  tmpdir="$(mktemp -d)"
  script="$tmpdir/restore-hermes.sh"
  curl -fsSL "$RESTORE_SCRIPT_URL" -o "$script"
  chmod +x "$script"
  printf '%s\n' "$script"
}

ensure_prereqs

if [ "$CHECK_ONLY" = "1" ]; then
  echo "Hermes restore bootstrap prerequisites are ready."
  exit 0
fi

restore_script="$(restore_script_path)"
if [ "$#" -eq 0 ]; then
  set -- --start-gateway --prompt-missing-env
fi

exec "$restore_script" "$@"
