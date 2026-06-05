import os
import stat
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RESTORE_SCRIPT = ROOT / "scripts" / "restore-hermes.sh"
BOOTSTRAP_SCRIPT = ROOT / "scripts" / "bootstrap-hermes-restore.sh"


def run_detect(home: Path) -> str:
    env = {**os.environ, "HOME": str(home)}
    result = subprocess.run(
        ["bash", str(RESTORE_SCRIPT), "--print-detected-obsidian-vault"],
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )
    return result.stdout.strip()


def write_obsidian_registry(home: Path, content: str) -> None:
    registry = home / "Library" / "Application Support" / "obsidian" / "obsidian.json"
    registry.parent.mkdir(parents=True)
    registry.write_text(content, encoding="utf-8")


def write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def test_detect_obsidian_vault_prefers_documents_sync(tmp_path: Path) -> None:
    vault = tmp_path / "Documents" / "Sync"
    (vault / ".obsidian").mkdir(parents=True)
    other = tmp_path / "Elsewhere" / "Notes"
    (other / ".obsidian").mkdir(parents=True)
    write_obsidian_registry(
        tmp_path,
        f'{{"vaults":{{"else":{{"path":"{other}","open":true}}}}}}',
    )

    assert run_detect(tmp_path) == str(vault)


def test_detect_obsidian_vault_from_pretty_registry(tmp_path: Path) -> None:
    vault = tmp_path / "External Drive" / "Obsidian Vault"
    (vault / ".obsidian").mkdir(parents=True)
    write_obsidian_registry(
        tmp_path,
        f"""
{{
  "cli": true,
  "vaults": {{
    "abc123": {{
      "open": true,
      "path": "{vault}",
      "ts": 1731693080568
    }}
  }}
}}
""",
    )

    assert run_detect(tmp_path) == str(vault)


def test_detect_obsidian_vault_from_compact_registry(tmp_path: Path) -> None:
    missing = tmp_path / "Missing Vault"
    vault = tmp_path / "Real Vault"
    (vault / ".obsidian").mkdir(parents=True)
    write_obsidian_registry(
        tmp_path,
        f'{{"vaults":{{"missing":{{"path":"{missing}"}},"real":{{"path":"{vault}"}}}}}}',
    )

    assert run_detect(tmp_path) == str(vault)


def test_restore_report_backup_audit_numeric_fields_default_to_zero() -> None:
    script = RESTORE_SCRIPT.read_text(encoding="utf-8")
    fields = [
        "backup_audit_missing_jsonl_count",
        "backup_audit_extra_jsonl_count",
        "backup_audit_missing_sessions_json_entries",
        "backup_audit_extra_sessions_json_entries",
        "backup_audit_jsonl_message_drift_count",
        "backup_audit_live_jsonl_message_mismatch_count",
        "backup_audit_live_state_sessions_without_legacy_files",
        "backup_audit_live_message_sessions_without_jsonl",
        "backup_state_db_size_bytes",
    ]

    for field in fields:
        line = next(line for line in script.splitlines() if f'"{field}"' in line)
        assert ":-0}" in line


def test_bootstrap_accepts_restore_token_without_gh_login(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in ("git", "rsync", "curl", "uv"):
        write_executable(bin_dir / name, "#!/usr/bin/env bash\nexit 0\n")
    write_executable(
        bin_dir / "gh",
        "#!/usr/bin/env bash\n"
        "if [ \"${1:-}\" = auth ] && [ \"${2:-}\" = status ]; then\n"
        "  echo 'gh auth status should not be called when HERMES_RESTORE_TOKEN is set' >&2\n"
        "  exit 42\n"
        "fi\n"
        "exit 0\n",
    )

    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "HERMES_BOOTSTRAP_NO_INSTALL": "1",
        "HERMES_RESTORE_TOKEN": "ghp_restore_token",
    }
    result = subprocess.run(
        ["bash", str(BOOTSTRAP_SCRIPT), "--check-only"],
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode == 0
    assert "Hermes restore bootstrap prerequisites are ready." in result.stdout
    assert "gh auth status should not be called" not in result.stderr


def test_setup_uses_locked_all_bundle_not_every_extra() -> None:
    script = (ROOT / "setup-hermes.sh").read_text(encoding="utf-8")

    assert "uv.lock for hash-verified installation" in script
    assert "sync --extra all --locked" in script
    assert "sync --all-extras --locked" not in script
