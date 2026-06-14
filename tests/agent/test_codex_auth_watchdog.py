"""Tests for the Codex auth no-stranding watchdog entrypoint."""

from __future__ import annotations

import base64
import io
import json


def _jwt(*, exp: int) -> str:
    header = base64.urlsafe_b64encode(json.dumps({"alg": "none"}).encode()).rstrip(b"=").decode()
    payload = base64.urlsafe_b64encode(json.dumps({"exp": exp}).encode()).rstrip(b"=").decode()
    return f"{header}.{payload}.sig"


def _write_hermes_auth(tmp_path, payload: dict) -> None:
    hermes_home = tmp_path / "hermes"
    hermes_home.mkdir(parents=True, exist_ok=True)
    (hermes_home / "auth.json").write_text(json.dumps(payload, indent=2))


def _write_codex_cli_auth(tmp_path, tokens: dict) -> None:
    codex_home = tmp_path / "codex"
    codex_home.mkdir(parents=True, exist_ok=True)
    (codex_home / "auth.json").write_text(json.dumps({"tokens": tokens}, indent=2))


def test_watchdog_writes_secret_free_healthy_packet_and_exits_zero(tmp_path, monkeypatch):
    now = 1_800_000_000
    access = _jwt(exp=now + 3600)
    refresh = "refresh-secret-never-report"
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    _write_hermes_auth(
        tmp_path,
        {
            "version": 1,
            "providers": {"openai-codex": {"tokens": {"access_token": access, "refresh_token": refresh}}},
            "credential_pool": {"openai-codex": [{"access_token": access, "refresh_token": refresh}]},
        },
    )
    _write_codex_cli_auth(tmp_path, {"access_token": access, "refresh_token": refresh})

    from agent.codex_auth_watchdog import run_codex_auth_watchdog

    output = io.StringIO()
    state_path = tmp_path / "state" / "codex_auth_watchdog.json"
    exit_code = run_codex_auth_watchdog(state_path=state_path, now=now, output=output)

    assert exit_code == 0
    packet = json.loads(output.getvalue())
    assert packet == json.loads(state_path.read_text())
    assert packet["watchdog"] == "codex-auth-no-stranding"
    assert packet["status"] == "healthy"
    assert packet["alert"] is False
    serialized = json.dumps(packet)
    assert access not in serialized
    assert refresh not in serialized


def test_watchdog_degraded_packet_exits_two_with_operator_action(tmp_path, monkeypatch):
    now = 1_800_000_000
    cli_access = _jwt(exp=now + 3600)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    _write_hermes_auth(tmp_path, {"version": 1, "providers": {"openai-codex": {"tokens": {}}}})
    _write_codex_cli_auth(tmp_path, {"access_token": cli_access, "refresh_token": "cli-refresh-secret"})

    from agent.codex_auth_watchdog import run_codex_auth_watchdog

    output = io.StringIO()
    exit_code = run_codex_auth_watchdog(state_path=tmp_path / "state.json", now=now, output=output)

    packet = json.loads(output.getvalue())
    assert exit_code == 2
    assert packet["status"] == "degraded"
    assert packet["alert"] is True
    assert packet["alert_reason"] == "codex_auth_degraded"
    assert "hermes auth login openai-codex" in packet["operator_next_action"]
    assert "missing_hermes_access_token" in packet["report"]["issues"]
    assert "cli-refresh-secret" not in json.dumps(packet)


def test_watchdog_normalizes_inconsistent_healthy_report(monkeypatch):
    import agent.codex_auth_watchdog as watchdog

    monkeypatch.setattr(
        watchdog,
        "build_codex_auth_health_report",
        lambda *, now=None: {"status": "healthy", "issues": ["credential_pool_has_exhausted_entries"]},
    )

    packet = watchdog.build_codex_auth_watchdog_packet(now=1_800_000_000)

    assert packet["status"] == "degraded"
    assert packet["alert"] is True
    assert packet["alert_reason"] == "codex_auth_degraded"


def test_watchdog_internal_failure_packet_is_structured_and_sanitized():
    from pathlib import Path

    from agent.codex_auth_watchdog import build_watchdog_internal_failure_packet

    home = str(Path.home())
    error = OSError(f"cannot write {home}/.hermes/state/codex_auth_watchdog.json")

    packet = build_watchdog_internal_failure_packet(error)

    assert packet["status"] == "degraded"
    assert packet["alert"] is True
    assert packet["alert_reason"] == "watchdog_internal_failure"
    assert packet["error"]["type"] == "OSError"
    assert home not in packet["error"]["message"]


def test_watchdog_main_accepts_state_path(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "missing-hermes"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "missing-codex"))

    from agent.codex_auth_watchdog import main

    state_path = tmp_path / "custom" / "watchdog.json"
    exit_code = main(["--state-path", str(state_path)])

    assert exit_code == 2
    packet = json.loads(state_path.read_text())
    assert packet["alert"] is True
    assert packet["report"]["status"] == "degraded"
