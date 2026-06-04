import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RESTORE_SCRIPT = ROOT / "scripts" / "restore-hermes.sh"


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
