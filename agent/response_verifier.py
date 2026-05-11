"""Jiminy-style final-response verification for Hermes.

This module provides a small, deterministic accountability gate that runs before
Hermes delivers a final assistant response.  It is intentionally conservative:
it does not try to prove every claim true, but it flags common trust failures
where the assistant claims it performed or verified work without matching tool
evidence in the current turn transcript.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import logging
import re
from pathlib import Path
from typing import Any, Mapping, Sequence

logger = logging.getLogger(__name__)

_DONE_PATTERNS: tuple[tuple[str, re.Pattern[str], tuple[str, ...]], ...] = (
    (
        "tests_passed",
        re.compile(r"\b(test(?:s|ed|ing)?|pytest|typecheck|lint|build|validation)\b[^\n]{0,80}\b(pass(?:ed|es)?|succeed(?:ed|s)?|green|validated)\b", re.I),
        ("exit_code", "passed", "success", "succeeded", "ok"),
    ),
    (
        "committed",
        re.compile(r"\b(commit(?:ted)?|git commit)\b", re.I),
        ("git commit", "commit ", "files changed", "create mode", "commit"),
    ),
    (
        "pushed",
        re.compile(r"\b(push(?:ed)?|git push|pushed to origin)\b", re.I),
        ("git push", "pushed to", "set up to track", "up-to-date", "origin/"),
    ),
    (
        "created_file",
        re.compile(r"\b(created|wrote|updated|modified|patched|implemented)\b[^\n]{0,80}\b(file|module|script|config|workflow|app|tests?)\b", re.I),
        ("write_file", "patch", "diff", "git status", "modified", "created", "Update File"),
    ),
    (
        "scheduled",
        re.compile(r"\b(scheduled|cron|reminder|job)\b[^\n]{0,80}\b(created|enabled|running|set up)\b", re.I),
        ("cronjob", "job_id", "created", "scheduled"),
    ),
)

_ACTION_PROMISE_RE = re.compile(
    r"\b(?:i(?:'ll| will)|let me|i’m going to|i am going to|i can)\s+"
    r"(?:check|run|create|write|update|commit|push|schedule|verify|test|look up)\b",
    re.I,
)

_TOOLISH_ROLES = {"tool", "function"}


@dataclass(frozen=True)
class ResponseVerifierConfig:
    enabled: bool = False
    mode: str = "warn"  # warn | block
    receipt_dir: str = "~/.hermes/response-verifier"
    max_evidence_chars: int = 50_000
    fail_closed: bool = False
    include_receipt_in_response: bool = False

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any] | None) -> "ResponseVerifierConfig":
        data = raw if isinstance(raw, Mapping) else {}
        return cls(
            enabled=bool(data.get("enabled", False)),
            mode=str(data.get("mode", "warn") or "warn").lower(),
            receipt_dir=str(data.get("receipt_dir", "~/.hermes/response-verifier") or "~/.hermes/response-verifier"),
            max_evidence_chars=int(data.get("max_evidence_chars", 50_000) or 50_000),
            fail_closed=bool(data.get("fail_closed", False)),
            include_receipt_in_response=bool(data.get("include_receipt_in_response", False)),
        )


@dataclass
class VerificationFinding:
    code: str
    severity: str
    message: str


@dataclass
class VerificationReceipt:
    ok: bool
    action: str
    findings: list[VerificationFinding] = field(default_factory=list)
    receipt_path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "action": self.action,
            "findings": [finding.__dict__ for finding in self.findings],
            "receipt_path": self.receipt_path,
        }


def load_response_verifier_config() -> ResponseVerifierConfig:
    try:
        from hermes_cli.config import load_config

        cfg = load_config()
        return ResponseVerifierConfig.from_mapping(cfg.get("response_verifier", {}))
    except Exception as exc:  # pragma: no cover - defensive runtime guard
        logger.warning("response_verifier config load failed: %s", exc)
        return ResponseVerifierConfig(enabled=False)


def _stringify_content(value: Any) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except Exception:
        return str(value)


def _collect_evidence(messages: Sequence[Mapping[str, Any]], max_chars: int) -> str:
    chunks: list[str] = []
    total = 0
    for msg in messages:
        if not isinstance(msg, Mapping):
            continue
        role = str(msg.get("role") or "")
        if role in _TOOLISH_ROLES or msg.get("tool_calls"):
            content = _stringify_content(msg)
            if not content:
                continue
            remaining = max_chars - total
            if remaining <= 0:
                break
            chunks.append(content[:remaining])
            total += min(len(content), remaining)
    return "\n".join(chunks).lower()


def _claim_has_evidence(response_text: str, evidence: str, pattern: re.Pattern[str], needles: tuple[str, ...]) -> bool:
    if not pattern.search(response_text):
        return True
    return any(needle.lower() in evidence for needle in needles)


def verify_response(
    *,
    response_text: str,
    messages: Sequence[Mapping[str, Any]],
    config: ResponseVerifierConfig | None = None,
) -> VerificationReceipt:
    """Return a deterministic accountability verdict for a final response."""
    cfg = config or ResponseVerifierConfig(enabled=True)
    findings: list[VerificationFinding] = []
    evidence = _collect_evidence(messages, cfg.max_evidence_chars)

    for code, pattern, needles in _DONE_PATTERNS:
        if not _claim_has_evidence(response_text, evidence, pattern, needles):
            findings.append(
                VerificationFinding(
                    code=code,
                    severity="warning",
                    message="Response claims completed/verifiable work without matching current-turn tool evidence.",
                )
            )

    if _ACTION_PROMISE_RE.search(response_text) and evidence.strip() == "":
        findings.append(
            VerificationFinding(
                code="promise_without_action",
                severity="warning",
                message="Response appears to promise action without any tool evidence in the turn.",
            )
        )

    action = "allow"
    if findings:
        action = "block" if cfg.mode == "block" else "warn"
    return VerificationReceipt(ok=not findings, action=action, findings=findings)


def _write_receipt(
    receipt: VerificationReceipt,
    *,
    cfg: ResponseVerifierConfig,
    session_id: str,
    model: str,
    platform: str,
) -> VerificationReceipt:
    try:
        root = Path(cfg.receipt_dir).expanduser()
        root.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        safe_session = re.sub(r"[^A-Za-z0-9_.-]+", "_", session_id or "unknown")[:80]
        path = root / f"{stamp}-{safe_session}.json"
        payload = receipt.to_dict()
        payload.update({
            "session_id": session_id,
            "model": model,
            "platform": platform,
            "created_at": stamp,
        })
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        receipt.receipt_path = str(path)
    except Exception as exc:
        logger.warning("response verifier receipt write failed: %s", exc)
        if cfg.fail_closed:
            receipt.ok = False
            receipt.action = "block"
            receipt.findings.append(
                VerificationFinding(
                    code="receipt_write_failed",
                    severity="error",
                    message=f"Response verifier receipt write failed: {exc}",
                )
            )
    return receipt


def apply_response_verifier(
    *,
    response_text: str,
    messages: list[dict[str, Any]],
    session_id: str = "",
    model: str = "",
    platform: str = "",
    config: ResponseVerifierConfig | None = None,
) -> tuple[str, VerificationReceipt | None]:
    """Apply the configured verifier and return possibly modified response text."""
    cfg = config or load_response_verifier_config()
    if not cfg.enabled or not response_text:
        return response_text, None

    receipt = verify_response(response_text=response_text, messages=messages, config=cfg)
    receipt = _write_receipt(receipt, cfg=cfg, session_id=session_id, model=model, platform=platform)

    if receipt.action == "allow":
        return response_text, receipt

    summary = "; ".join(f"{f.code}: {f.message}" for f in receipt.findings)
    suffix = f"\n\n[Jiminy: {receipt.action} — {summary}"
    if cfg.include_receipt_in_response and receipt.receipt_path:
        suffix += f" Receipt: {receipt.receipt_path}"
    suffix += "]"

    if receipt.action == "block":
        response_text = (
            "Jiminy blocked this response because it made accountability-sensitive claims "
            f"without enough current-turn evidence. {summary}"
        )
        if cfg.include_receipt_in_response and receipt.receipt_path:
            response_text += f"\nReceipt: {receipt.receipt_path}"
    else:
        response_text = response_text + suffix

    # Keep persisted session history aligned with delivered text.
    for msg in reversed(messages):
        if isinstance(msg, dict) and msg.get("role") == "assistant" and isinstance(msg.get("content"), str):
            msg["content"] = response_text
            break

    return response_text, receipt
