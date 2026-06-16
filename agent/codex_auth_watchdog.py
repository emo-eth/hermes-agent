"""No-agent watchdog entrypoint for OpenAI Codex auth health.

This module is intentionally small and side-effect-light so a cron/no-agent job can
run it without invoking an LLM. It emits a secret-free JSON packet every time and
uses the process exit code as the alert gate: healthy reports exit 0, degraded
reports exit 2, and watchdog-internal failures exit 1.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional, Sequence, TextIO

from agent.codex_auth_health import build_codex_auth_health_report


DEFAULT_STATE_PATH = Path("~/.hermes/state/codex_auth_watchdog.json").expanduser()


def build_codex_auth_watchdog_packet(
    *,
    now: Optional[float] = None,
    standalone_codex_smoke: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Build a cron-safe watchdog packet around the sanitized health report."""
    report = build_codex_auth_health_report(now=now)
    status = str(report.get("status") or "degraded")
    issues = report.get("issues")
    issue_list = (
        [str(issue) for issue in issues]
        if isinstance(issues, list)
        else ["malformed_health_report"]
    )
    if (
        status not in {"healthy", "degraded"}
        and "malformed_health_report" not in issue_list
    ):
        issue_list.append("malformed_health_report")
    if status != "healthy" or issue_list:
        status = "degraded"
    alert = status != "healthy"
    return {
        "watchdog": "codex-auth-no-stranding",
        "version": 1,
        "status": status,
        "alert": alert,
        "alert_reason": "codex_auth_degraded" if alert else None,
        "operator_next_action": _operator_next_action(issue_list) if alert else None,
        "operator_runbook": _operator_runbook(
            report, issue_list, standalone_codex_smoke
        )
        if alert
        else None,
        "fallback_lane": _fallback_lane(report, standalone_codex_smoke),
        "report": report,
    }


def build_watchdog_internal_failure_packet(error: BaseException) -> dict[str, Any]:
    """Build a structured packet for watchdog-internal failures."""
    return {
        "watchdog": "codex-auth-no-stranding",
        "version": 1,
        "status": "degraded",
        "alert": True,
        "alert_reason": "watchdog_internal_failure",
        "operator_next_action": "Inspect the Codex auth watchdog runtime error, fix the local state/output path, then rerun the watchdog.",
        "error": {
            "type": type(error).__name__,
            "message": _safe_error_message(error),
        },
    }


def _safe_error_message(error: BaseException) -> str:
    message = str(error).replace(str(Path.home()), "~")
    if "/" in message:
        message = "path redacted"
    return message[:240]


def _fallback_lane(
    report: dict[str, Any],
    standalone_codex_smoke: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Describe whether standalone Codex CLI auth is a candidate rescue lane."""
    cli_store = report.get("codex_cli_auth_store") if isinstance(report, dict) else {}
    cli_store = cli_store if isinstance(cli_store, dict) else {}
    access = cli_store.get("access_token") if isinstance(cli_store, dict) else {}
    access = access if isinstance(access, dict) else {}
    auth_store_ready = bool(
        cli_store.get("path_exists")
        and access.get("present")
        and access.get("jwt_claims_decodable")
        and not access.get("expiring")
        and cli_store.get("refresh_token_present")
    )
    smoke_status = str((standalone_codex_smoke or {}).get("status") or "not_run")
    available = auth_store_ready and smoke_status == "passed"
    return {
        "name": "standalone-codex-cli",
        "available": available,
        "auth_store_ready": auth_store_ready,
        "live_smoke": standalone_codex_smoke or {"status": "not_run"},
        "operator_action": (
            "Standalone Codex CLI live smoke passed; it is available as a temporary rescue lane while Hermes auth is repaired."
            if available
            else (
                "Standalone Codex CLI auth store looks ready; run a live Codex CLI smoke before using it as a temporary rescue lane."
                if auth_store_ready
                else "Standalone Codex CLI auth is not a candidate fallback; repair CLI auth before treating it as a rescue lane."
            )
        ),
    }


def _operator_runbook(
    report: dict[str, Any],
    issues: list[str],
    standalone_codex_smoke: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Return structured operator guidance without secrets or local-only paths."""
    fallback = _fallback_lane(report, standalone_codex_smoke)
    repair_steps = [_operator_next_action(issues)]
    if fallback["available"]:
        repair_steps.append(
            "Use the live-smoked standalone Codex CLI only as a temporary rescue lane while Hermes auth is repaired."
        )
    elif fallback["auth_store_ready"]:
        repair_steps.append(
            "Run a live standalone Codex CLI smoke; only then use it to keep urgent work moving while Hermes auth is repaired."
        )
    else:
        repair_steps.append(
            "Do not assume the standalone Codex CLI can rescue this incident; its auth is missing, expiring, malformed, or incomplete."
        )
    repair_steps.append(
        "Rerun `python -m agent.codex_auth_watchdog` and require status=healthy before closing the incident."
    )
    return {
        "severity": "blocking_with_verified_fallback"
        if fallback["available"]
        else (
            "blocking_candidate_fallback"
            if fallback["auth_store_ready"]
            else "blocking"
        ),
        "repair_steps": repair_steps,
        "fallback_available": fallback["available"],
        "fallback_auth_store_ready": fallback["auth_store_ready"],
        "fallback_live_smoke_status": fallback["live_smoke"]["status"],
    }


def run_standalone_codex_smoke(
    command: Sequence[str], *, timeout_seconds: int = 45
) -> dict[str, Any]:
    """Run an optional standalone Codex CLI smoke and return secret-free evidence."""
    if not command:
        return {"status": "not_run"}
    try:
        completed = subprocess.run(
            list(command),
            capture_output=True,
            check=False,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        return {"status": "failed", "exit_code": None, "evidence": "timeout"}
    except OSError as exc:
        return {
            "status": "failed",
            "exit_code": None,
            "evidence": _safe_error_message(exc),
        }

    combined = f"{completed.stdout}\n{completed.stderr}"
    if _contains_secret_shape(combined):
        evidence = "output redacted"
    elif completed.returncode == 0 and "OK" in completed.stdout:
        evidence = "stdout contained OK"
    else:
        evidence = (
            "completed without expected OK"
            if completed.returncode == 0
            else "command failed"
        )
    return {
        "status": "passed"
        if completed.returncode == 0 and "OK" in completed.stdout
        else "failed",
        "exit_code": completed.returncode,
        "evidence": evidence,
    }


def _contains_secret_shape(value: str) -> bool:
    return any(
        marker in value
        for marker in ("Bearer ", "sk-", "refresh_token", "access_token")
    )


def _operator_next_action(issues: list[str]) -> str:
    if any(
        issue in issues
        for issue in ("missing_hermes_refresh_token", "missing_hermes_access_token")
    ):
        return "Run `hermes auth login openai-codex` for the Hermes profile, then rerun the Codex auth watchdog."
    if any(
        issue in issues
        for issue in (
            "missing_codex_cli_refresh_token",
            "missing_codex_cli_access_token",
        )
    ):
        return "Run the standalone Codex CLI login flow, then rerun the Codex auth watchdog."
    if "credential_pool_has_exhausted_entries" in issues:
        return "Inspect Hermes OpenAI Codex credential-pool exhaustion reasons and relogin any exhausted account before agent work strands."
    if any(issue.endswith("_expiring") for issue in issues):
        return "Refresh OpenAI Codex auth before expiry; if refresh fails, relogin Hermes and standalone Codex CLI."
    if any(issue.endswith("_diverged") for issue in issues):
        return "Refresh/reconcile Hermes and standalone Codex CLI auth stores; divergence can hide single-use refresh-token failures."
    return "Inspect the sanitized report issues and rerun the watchdog after repair."


def run_codex_auth_watchdog(
    *,
    state_path: Path = DEFAULT_STATE_PATH,
    now: Optional[float] = None,
    output: TextIO = sys.stdout,
    standalone_codex_smoke: Optional[dict[str, Any]] = None,
) -> int:
    """Write the latest packet to state and stdout; return cron-friendly code."""
    packet = build_codex_auth_watchdog_packet(
        now=now, standalone_codex_smoke=standalone_codex_smoke
    )
    encoded = json.dumps(packet, indent=2, sort_keys=True)
    output.write(encoded + "\n")
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(encoded + "\n", encoding="utf-8")
    return 2 if packet["alert"] else 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Emit a secret-free OpenAI Codex auth watchdog packet."
    )
    parser.add_argument(
        "--state-path",
        type=Path,
        default=DEFAULT_STATE_PATH,
        help="Path to write the latest watchdog packet (default: ~/.hermes/state/codex_auth_watchdog.json).",
    )
    parser.add_argument(
        "--standalone-codex-smoke-command",
        nargs=argparse.REMAINDER,
        help="Optional standalone Codex CLI smoke command; must print OK on success.",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        smoke = run_standalone_codex_smoke(args.standalone_codex_smoke_command or [])
        return run_codex_auth_watchdog(
            state_path=args.state_path.expanduser(), standalone_codex_smoke=smoke
        )
    except (
        Exception
    ) as exc:  # pragma: no cover - exercised through CLI-safe helper tests
        packet = build_watchdog_internal_failure_packet(exc)
        sys.stdout.write(json.dumps(packet, indent=2, sort_keys=True) + "\n")
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
