"""Sanitized OpenAI Codex auth health reporting helpers."""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, Optional

from hermes_cli.auth import (
    CODEX_ACCESS_TOKEN_REFRESH_SKEW_SECONDS,
    _decode_jwt_claims,
    _load_auth_store,
)


_SAFE_EXHAUSTION_REASONS = frozenset({
    "invalid_grant",
    "unauthorized",
    "expired",
    "rate_limited",
    "quota_exceeded",
})


def _codex_cli_auth_path() -> Path:
    codex_home = os.getenv("CODEX_HOME", "").strip()
    if not codex_home:
        codex_home = str(Path.home() / ".codex")
    return Path(codex_home).expanduser() / "auth.json"


def _read_codex_cli_tokens() -> Optional[Dict[str, Any]]:
    path = _codex_cli_auth_path()
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text())
    except Exception:
        return None
    tokens = payload.get("tokens")
    return dict(tokens) if isinstance(tokens, dict) else None


def _token_summary(token: Any, *, now: Optional[float] = None) -> Dict[str, Any]:
    raw = str(token or "").strip()
    if not raw:
        return {"present": False}
    now = time.time() if now is None else float(now)
    claims = _decode_jwt_claims(raw)
    exp = claims.get("exp")
    expires_at = int(exp) if isinstance(exp, (int, float)) else None
    seconds_remaining = int(expires_at - now) if expires_at is not None else None
    expiring = (
        seconds_remaining is not None
        and seconds_remaining <= CODEX_ACCESS_TOKEN_REFRESH_SKEW_SECONDS
    )
    return {
        "present": True,
        "jwt_claims_decodable": bool(claims),
        "expires_at": expires_at,
        "seconds_remaining": seconds_remaining,
        "expiring": expiring,
    }


def build_codex_auth_health_report(*, now: Optional[float] = None) -> Dict[str, Any]:
    """Build a secret-free report for Hermes vs standalone Codex auth state.

    The report intentionally never includes access or refresh token material.  It
    is safe to persist in cron receipts and Discord alerts.  It distinguishes
    Hermes' auth store, the credential pool, and the standalone Codex CLI file
    so watchdogs can flag the exact no-stranding boundary instead of only saying
    "Codex login works".
    """
    now = time.time() if now is None else float(now)
    try:
        store = _load_auth_store()
    except Exception:
        store = {}

    providers = store.get("providers") if isinstance(store, dict) else {}
    codex_state = providers.get("openai-codex") if isinstance(providers, dict) else {}
    codex_tokens = codex_state.get("tokens") if isinstance(codex_state, dict) else {}
    if not isinstance(codex_tokens, dict):
        codex_tokens = {}

    pool_root = store.get("credential_pool") if isinstance(store, dict) else {}
    pool_entries = pool_root.get("openai-codex") if isinstance(pool_root, dict) else []
    pool_entries = pool_entries if isinstance(pool_entries, list) else []
    exhausted_entries = [entry for entry in pool_entries if isinstance(entry, dict) and entry.get("last_status") == "exhausted"]

    cli_tokens = _read_codex_cli_tokens() or {}

    hermes_access = str(codex_tokens.get("access_token") or "")
    cli_access = str(cli_tokens.get("access_token") or "")
    hermes_refresh = str(codex_tokens.get("refresh_token") or "")
    cli_refresh = str(cli_tokens.get("refresh_token") or "")

    issues = []
    if not hermes_access:
        issues.append("missing_hermes_access_token")
    if not hermes_refresh:
        issues.append("missing_hermes_refresh_token")
    hermes_summary = _token_summary(hermes_access, now=now)
    cli_summary = _token_summary(cli_access, now=now)
    if hermes_access and hermes_summary.get("expiring"):
        issues.append("hermes_access_token_expiring")
    if hermes_access and not hermes_summary.get("jwt_claims_decodable"):
        issues.append("hermes_access_token_not_decodable")
    if not cli_access:
        issues.append("missing_codex_cli_access_token")
    if not cli_refresh:
        issues.append("missing_codex_cli_refresh_token")
    if cli_access and cli_summary.get("expiring"):
        issues.append("codex_cli_access_token_expiring")
    if cli_access and not cli_summary.get("jwt_claims_decodable"):
        issues.append("codex_cli_access_token_not_decodable")
    if hermes_access and cli_access and hermes_access != cli_access:
        issues.append("hermes_codex_cli_access_token_diverged")
    if hermes_refresh and cli_refresh and hermes_refresh != cli_refresh:
        issues.append("hermes_codex_cli_refresh_token_diverged")
    if exhausted_entries:
        issues.append("credential_pool_has_exhausted_entries")

    return {
        "provider": "openai-codex",
        "checked_at_epoch": int(now),
        "status": "healthy" if not issues else "degraded",
        "issues": issues,
        "hermes_auth_store": {
            "present": bool(codex_tokens),
            "access_token": hermes_summary,
            "refresh_token_present": bool(hermes_refresh),
            "last_refresh": _safe_timestamp(codex_state.get("last_refresh") if isinstance(codex_state, dict) else None),
        },
        "codex_cli_auth_store": {
            "path_exists": _codex_cli_auth_path().is_file(),
            "access_token": cli_summary,
            "refresh_token_present": bool(cli_refresh),
        },
        "credential_pool": {
            "entry_count": len(pool_entries),
            "exhausted_count": len(exhausted_entries),
            "available_count": max(0, len(pool_entries) - len(exhausted_entries)),
            "exhausted_reasons": sorted(
                _safe_exhaustion_reason(entry) for entry in exhausted_entries if isinstance(entry, dict)
            ),
        },
    }


def _safe_timestamp(value: Any) -> Optional[str]:
    raw = str(value or "").strip()
    if not raw:
        return None
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z", raw):
        return raw
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?[+-]\d{2}:\d{2}", raw):
        return raw
    return "redacted_non_timestamp"


def _safe_exhaustion_reason(entry: Dict[str, Any]) -> str:
    reason = str(entry.get("last_error_reason") or "").strip().lower()
    if reason in _SAFE_EXHAUSTION_REASONS:
        return reason
    code = entry.get("last_error_code")
    if isinstance(code, int):
        return f"http_{code}"
    if isinstance(code, str) and code.isdigit():
        return f"http_{code}"
    return "unknown"
