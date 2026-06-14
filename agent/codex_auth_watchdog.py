"""No-agent watchdog entrypoint for OpenAI Codex auth health.

This module is intentionally small and side-effect-light so a cron/no-agent job can
run it without invoking an LLM. It emits a secret-free JSON packet every time and
uses the process exit code as the alert gate: healthy reports exit 0, degraded
reports exit 2, and watchdog-internal failures exit 1.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Optional, Sequence, TextIO

from agent.codex_auth_health import build_codex_auth_health_report


DEFAULT_STATE_PATH = Path("~/.hermes/state/codex_auth_watchdog.json").expanduser()


def build_codex_auth_watchdog_packet(*, now: Optional[float] = None) -> dict[str, Any]:
    """Build a cron-safe watchdog packet around the sanitized health report."""
    report = build_codex_auth_health_report(now=now)
    status = str(report.get("status") or "degraded")
    issues = report.get("issues")
    issue_list = [str(issue) for issue in issues] if isinstance(issues, list) else ["malformed_health_report"]
    if status not in {"healthy", "degraded"} and "malformed_health_report" not in issue_list:
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
    return message[:240]


def _operator_next_action(issues: list[str]) -> str:
    if any(issue in issues for issue in ("missing_hermes_refresh_token", "missing_hermes_access_token")):
        return "Run `hermes auth login openai-codex` for the Hermes profile, then rerun the Codex auth watchdog."
    if any(issue in issues for issue in ("missing_codex_cli_refresh_token", "missing_codex_cli_access_token")):
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
) -> int:
    """Write the latest packet to state and stdout; return cron-friendly code."""
    packet = build_codex_auth_watchdog_packet(now=now)
    encoded = json.dumps(packet, indent=2, sort_keys=True)
    output.write(encoded + "\n")
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(encoded + "\n", encoding="utf-8")
    return 2 if packet["alert"] else 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Emit a secret-free OpenAI Codex auth watchdog packet.")
    parser.add_argument(
        "--state-path",
        type=Path,
        default=DEFAULT_STATE_PATH,
        help="Path to write the latest watchdog packet (default: ~/.hermes/state/codex_auth_watchdog.json).",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        return run_codex_auth_watchdog(state_path=args.state_path.expanduser())
    except Exception as exc:  # pragma: no cover - exercised through CLI-safe helper tests
        packet = build_watchdog_internal_failure_packet(exc)
        sys.stdout.write(json.dumps(packet, indent=2, sort_keys=True) + "\n")
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
