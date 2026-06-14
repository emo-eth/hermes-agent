"""Tests for sanitized Codex auth health reports."""

from __future__ import annotations

import base64
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


def test_codex_auth_health_report_is_healthy_without_secrets(tmp_path, monkeypatch):
    now = 1_800_000_000
    access = _jwt(exp=now + 3600)
    refresh = "refresh-secret-never-report"
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    _write_hermes_auth(
        tmp_path,
        {
            "version": 1,
            "providers": {
                "openai-codex": {
                    "tokens": {"access_token": access, "refresh_token": refresh},
                    "last_refresh": "2026-06-14T10:00:00Z",
                }
            },
            "credential_pool": {
                "openai-codex": [
                    {
                        "id": "pool-1",
                        "label": "device",
                        "auth_type": "oauth",
                        "priority": 0,
                        "source": "device_code",
                        "access_token": access,
                        "refresh_token": refresh,
                    }
                ]
            },
        },
    )
    _write_codex_cli_auth(tmp_path, {"access_token": access, "refresh_token": refresh})

    from agent.codex_auth_health import build_codex_auth_health_report

    report = build_codex_auth_health_report(now=now)

    assert report["status"] == "healthy"
    assert report["issues"] == []
    assert report["hermes_auth_store"]["access_token"]["seconds_remaining"] == 3600
    assert report["credential_pool"]["entry_count"] == 1
    serialized = json.dumps(report)
    assert access not in serialized
    assert refresh not in serialized


def test_codex_auth_health_report_flags_divergence_and_exhausted_pool(tmp_path, monkeypatch):
    now = 1_800_000_000
    hermes_access = _jwt(exp=now + 3600)
    cli_access = _jwt(exp=now + 7200)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    _write_hermes_auth(
        tmp_path,
        {
            "version": 1,
            "providers": {
                "openai-codex": {
                    "tokens": {"access_token": hermes_access, "refresh_token": "hermes-refresh"},
                }
            },
            "credential_pool": {
                "openai-codex": [
                    {
                        "id": "pool-1",
                        "auth_type": "oauth",
                        "priority": 0,
                        "source": "device_code",
                        "access_token": hermes_access,
                        "refresh_token": "hermes-refresh",
                        "last_status": "exhausted",
                        "last_error_code": 401,
                        "last_error_reason": "invalid_grant",
                    }
                ]
            },
        },
    )
    _write_codex_cli_auth(tmp_path, {"access_token": cli_access, "refresh_token": "cli-refresh"})

    from agent.codex_auth_health import build_codex_auth_health_report

    report = build_codex_auth_health_report(now=now)

    assert report["status"] == "degraded"
    assert "hermes_codex_cli_access_token_diverged" in report["issues"]
    assert "hermes_codex_cli_refresh_token_diverged" in report["issues"]
    assert "credential_pool_has_exhausted_entries" in report["issues"]
    assert report["credential_pool"]["exhausted_reasons"] == ["invalid_grant"]


def test_codex_auth_health_report_flags_expiring_or_missing_tokens(tmp_path, monkeypatch):
    now = 1_800_000_000
    expiring_access = _jwt(exp=now + 30)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "missing-codex"))
    _write_hermes_auth(
        tmp_path,
        {
            "version": 1,
            "providers": {
                "openai-codex": {
                    "tokens": {"access_token": expiring_access},
                }
            },
        },
    )

    from agent.codex_auth_health import build_codex_auth_health_report

    report = build_codex_auth_health_report(now=now)

    assert report["status"] == "degraded"
    assert "hermes_access_token_expiring" in report["issues"]
    assert "missing_hermes_refresh_token" in report["issues"]
    assert "missing_codex_cli_access_token" in report["issues"]
    assert report["codex_cli_auth_store"]["path_exists"] is False


def test_codex_auth_health_report_flags_malformed_tokens_and_redacts_error_reasons(tmp_path, monkeypatch):
    now = 1_800_000_000
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    _write_hermes_auth(
        tmp_path,
        {
            "version": 1,
            "providers": {
                "openai-codex": {
                    "tokens": {"access_token": "not-a-jwt-secret-token", "refresh_token": "shared-refresh"},
                }
            },
            "credential_pool": {
                "openai-codex": [
                    {
                        "id": "pool-1",
                        "auth_type": "oauth",
                        "priority": 0,
                        "source": "device_code",
                        "access_token": "not-a-jwt-secret-token",
                        "refresh_token": "shared-refresh",
                        "last_status": "exhausted",
                        "last_error_reason": "token leaked ***",
                    }
                ]
            },
        },
    )
    _write_codex_cli_auth(tmp_path, {"access_token": "not-a-jwt-secret-token", "refresh_token": "shared-refresh"})

    from agent.codex_auth_health import build_codex_auth_health_report

    report = build_codex_auth_health_report(now=now)

    assert report["status"] == "degraded"
    assert "hermes_access_token_not_decodable" in report["issues"]
    assert "codex_cli_access_token_not_decodable" in report["issues"]
    assert report["credential_pool"]["exhausted_reasons"] == ["unknown"]
    serialized = json.dumps(report)
    assert "not-a-jwt-secret-token" not in serialized
    assert "***" not in serialized


def test_codex_auth_health_report_flags_expiring_codex_cli_token(tmp_path, monkeypatch):
    now = 1_800_000_000
    hermes_access = _jwt(exp=now + 3600)
    cli_access = _jwt(exp=now + 30)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    _write_hermes_auth(
        tmp_path,
        {
            "version": 1,
            "providers": {
                "openai-codex": {
                    "tokens": {"access_token": hermes_access, "refresh_token": "shared-refresh"},
                }
            },
        },
    )
    _write_codex_cli_auth(tmp_path, {"access_token": cli_access, "refresh_token": "shared-refresh"})

    from agent.codex_auth_health import build_codex_auth_health_report

    report = build_codex_auth_health_report(now=now)

    assert report["status"] == "degraded"
    assert "codex_cli_access_token_expiring" in report["issues"]
