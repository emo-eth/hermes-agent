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

from agent.auxiliary_client import call_llm

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
    max_repairs: int = 1
    fail_closed: bool = False
    include_receipt_in_response: bool = False
    backend: str = "deterministic"  # deterministic | llm | hybrid
    llm_provider: str = "auto"
    llm_model: str = ""
    llm_timeout: float = 30.0

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any] | None) -> "ResponseVerifierConfig":
        data = raw if isinstance(raw, Mapping) else {}
        return cls(
            enabled=bool(data.get("enabled", False)),
            mode=str(data.get("mode", "warn") or "warn").lower(),
            receipt_dir=str(data.get("receipt_dir", "~/.hermes/response-verifier") or "~/.hermes/response-verifier"),
            max_evidence_chars=int(data.get("max_evidence_chars", 50_000) or 50_000),
            max_repairs=max(0, int(data.get("max_repairs", 1) or 0)),
            fail_closed=bool(data.get("fail_closed", False)),
            include_receipt_in_response=bool(data.get("include_receipt_in_response", False)),
            backend=str(data.get("backend", "deterministic") or "deterministic").lower(),
            llm_provider=str(data.get("llm_provider", data.get("provider", "auto")) or "auto"),
            llm_model=str(data.get("llm_model", data.get("model", "")) or ""),
            llm_timeout=float(data.get("llm_timeout", data.get("timeout", 30.0)) or 30.0),
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
    evidence_message_count: int = 0
    evidence_tool_chars: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "action": self.action,
            "findings": [finding.__dict__ for finding in self.findings],
            "receipt_path": self.receipt_path,
            "evidence_message_count": self.evidence_message_count,
            "evidence_tool_chars": self.evidence_tool_chars,
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


def _is_internal_jiminy_message(msg: Mapping[str, Any]) -> bool:
    if msg.get("_jiminy_retry") or msg.get("_jiminy_blocked_candidate"):
        return True
    content = msg.get("content")
    if isinstance(content, str) and content.startswith("[System: Jiminy blocked your previous candidate response"):
        return True
    return False


def _evidence_messages(
    messages: Sequence[Mapping[str, Any]],
    *,
    evidence_start_index: int = 0,
) -> list[Mapping[str, Any]]:
    start = max(0, int(evidence_start_index or 0))
    scoped = list(messages[start:])
    return [msg for msg in scoped if isinstance(msg, Mapping) and not _is_internal_jiminy_message(msg)]


def build_verifier_evidence_bundle(
    *,
    messages: Sequence[Mapping[str, Any]],
    max_chars: int,
    evidence_start_index: int = 0,
) -> str:
    """Build the exact scoped evidence packet used by Jiminy."""
    evidence_msgs = _evidence_messages(messages, evidence_start_index=evidence_start_index)
    transcript = _stringify_content(evidence_msgs)[:max_chars]
    tool_evidence = _collect_evidence(evidence_msgs, max_chars)
    return (
        "Current-turn transcript/evidence:\n"
        "```json\n"
        f"{transcript}\n"
        "```\n\n"
        "Collected tool evidence:\n"
        "```text\n"
        f"{tool_evidence}\n"
        "```"
    )


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


def _deterministic_findings(response_text: str, messages: Sequence[Mapping[str, Any]], cfg: ResponseVerifierConfig) -> list[VerificationFinding]:
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
    return findings


def _receipt_from_findings(findings: list[VerificationFinding], cfg: ResponseVerifierConfig) -> VerificationReceipt:
    action = "allow"
    if findings:
        action = "block" if cfg.mode in {"block", "strict"} else "warn"
    return VerificationReceipt(ok=not findings, action=action, findings=findings)


def _extract_message_text(response: Any) -> str:
    content = response.choices[0].message.content
    if isinstance(content, str):
        return content
    return _stringify_content(content)


def _parse_json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", stripped, re.S | re.I)
    if fenced:
        stripped = fenced.group(1)
    else:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start != -1 and end != -1 and end > start:
            stripped = stripped[start : end + 1]
    parsed = json.loads(stripped)
    if not isinstance(parsed, dict):
        raise ValueError("LLM judge did not return a JSON object")
    return parsed


def _sanitize_issue_code(value: Any, fallback: str) -> str:
    raw = str(value or fallback).strip().lower()
    code = re.sub(r"[^a-z0-9_]+", "_", raw).strip("_")
    return code or fallback


def _llm_judge_receipt(
    *,
    response_text: str,
    messages: Sequence[Mapping[str, Any]],
    cfg: ResponseVerifierConfig,
    deterministic_findings: list[VerificationFinding],
    evidence_start_index: int = 0,
) -> VerificationReceipt:
    evidence_msgs = _evidence_messages(messages, evidence_start_index=evidence_start_index)
    evidence = _collect_evidence(evidence_msgs, cfg.max_evidence_chars)
    transcript = _stringify_content(evidence_msgs)[: cfg.max_evidence_chars]
    deterministic_summary = [finding.__dict__ for finding in deterministic_findings]
    system = (
        "You are Jiminy, Hermes' pre-delivery response judge. Evaluate the candidate assistant response "
        "against the current-turn transcript/tool evidence. Return ONLY JSON with keys: "
        "verdict (pass|repair|block|escalate), issues (array of {code,message,quote?}), confidence. "
        "Use repair/block for unsupported action, completion, validation, current-fact, safety, or source claims. "
        "Use pass only when the candidate is safe to deliver as-is."
    )
    user = (
        "Current-turn transcript/evidence:\n"
        "```json\n"
        f"{transcript}\n"
        "```\n\n"
        "Collected tool evidence:\n"
        "```text\n"
        f"{evidence}\n"
        "```\n\n"
        "Deterministic pre-check findings:\n"
        "```json\n"
        f"{json.dumps(deterministic_summary, ensure_ascii=False)}\n"
        "```\n\n"
        "Candidate response:\n"
        "```text\n"
        f"{response_text}\n"
        "```"
    )
    response = call_llm(
        task="response_verifier",
        provider=None if cfg.llm_provider == "auto" else cfg.llm_provider,
        model=cfg.llm_model or None,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        temperature=0,
        max_tokens=1200,
        timeout=cfg.llm_timeout,
    )
    payload = _parse_json_object(_extract_message_text(response))
    verdict = str(payload.get("verdict", "") or "").strip().lower()
    if verdict == "pass":
        return VerificationReceipt(
            ok=True,
            action="allow",
            findings=[],
            evidence_message_count=len(evidence_msgs),
            evidence_tool_chars=len(evidence),
        )

    issues = payload.get("issues")
    findings: list[VerificationFinding] = []
    if isinstance(issues, list):
        for idx, issue in enumerate(issues, 1):
            if isinstance(issue, Mapping):
                findings.append(
                    VerificationFinding(
                        code=_sanitize_issue_code(issue.get("code"), f"llm_judge_issue_{idx}"),
                        severity="error" if verdict in {"block", "escalate"} else "warning",
                        message=str(issue.get("message") or issue.get("quote") or "LLM judge flagged the response."),
                    )
                )
    if not findings:
        findings.append(
            VerificationFinding(
                code=_sanitize_issue_code(verdict, "llm_judge_blocked"),
                severity="error" if verdict in {"block", "escalate"} else "warning",
                message="LLM judge did not pass the candidate response.",
            )
        )
    receipt = _receipt_from_findings(findings, cfg)
    receipt.evidence_message_count = len(evidence_msgs)
    receipt.evidence_tool_chars = len(evidence)
    return receipt


def verify_response(
    *,
    response_text: str,
    messages: Sequence[Mapping[str, Any]],
    config: ResponseVerifierConfig | None = None,
    evidence_start_index: int = 0,
) -> VerificationReceipt:
    """Return an accountability verdict for a final response."""
    cfg = config or ResponseVerifierConfig(enabled=True)
    evidence_msgs = _evidence_messages(messages, evidence_start_index=evidence_start_index)
    deterministic_findings = _deterministic_findings(response_text, evidence_msgs, cfg)
    if cfg.backend in {"llm", "hybrid", "jiminy"}:
        try:
            return _llm_judge_receipt(
                response_text=response_text,
                messages=messages,
                cfg=cfg,
                deterministic_findings=deterministic_findings,
                evidence_start_index=evidence_start_index,
            )
        except Exception as exc:
            logger.warning("LLM response verifier failed: %s", exc)
            if cfg.fail_closed:
                return VerificationReceipt(
                    ok=False,
                    action="block" if cfg.mode in {"block", "strict"} else "warn",
                    findings=[
                        VerificationFinding(
                            code="llm_judge_failed",
                            severity="error",
                            message=f"LLM response verifier failed: {exc}",
                        )
                    ],
                )

    receipt = _receipt_from_findings(deterministic_findings, cfg)
    evidence = _collect_evidence(evidence_msgs, cfg.max_evidence_chars)
    receipt.evidence_message_count = len(evidence_msgs)
    receipt.evidence_tool_chars = len(evidence)
    return receipt


def _finding_summary(receipt: VerificationReceipt) -> str:
    return "; ".join(f"{f.code}: {f.message}" for f in receipt.findings)


def build_verifier_retry_prompt(
    *,
    candidate_response: str,
    receipt: VerificationReceipt,
    attempt: int,
    max_repairs: int,
    evidence_bundle: str | None = None,
) -> str:
    """Build the private retry instruction inserted after a blocked candidate."""
    summary = _finding_summary(receipt)
    return (
        f"[System: Jiminy blocked your previous candidate response "
        f"(repair attempt {attempt}/{max_repairs}).\n"
        f"Issues: {summary}\n\n"
        "Blocked candidate response:\n"
        "```text\n"
        f"{candidate_response}\n"
        "```\n\n"
        + (
            "Current evidence available to repair against:\n"
            f"{evidence_bundle}\n\n"
            if evidence_bundle else ""
        )
        + "Revise the final answer now. Do not repeat unsupported completion, "
        "action, current-fact, or validation claims unless the current-turn tool evidence above proves them. "
        "If evidence is missing, say exactly what is unverified or continue by calling the needed tools. "
        "Return only the corrected user-facing answer or the required tool calls.]"
    )


def render_blocked_response(receipt: VerificationReceipt) -> str:
    summary = _finding_summary(receipt)
    return (
        "Jiminy blocked this response after exhausting repair attempts. "
        "I’m not going to send the unsupported answer as if it were true. "
        f"Issues: {summary}"
    )


def evaluate_response_verifier(
    *,
    response_text: str,
    messages: list[dict[str, Any]],
    session_id: str = "",
    model: str = "",
    platform: str = "",
    config: ResponseVerifierConfig | None = None,
    evidence_start_index: int = 0,
) -> tuple[ResponseVerifierConfig, VerificationReceipt | None]:
    """Run the configured verifier and write a receipt without changing text."""
    cfg = config or load_response_verifier_config()
    if not cfg.enabled or not response_text:
        return cfg, None
    receipt = verify_response(
        response_text=response_text,
        messages=messages,
        config=cfg,
        evidence_start_index=evidence_start_index,
    )
    receipt = _write_receipt(receipt, cfg=cfg, session_id=session_id, model=model, platform=platform)
    return cfg, receipt


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
    evidence_start_index: int = 0,
) -> tuple[str, VerificationReceipt | None]:
    """Apply the configured verifier and return possibly modified response text."""
    cfg = config or load_response_verifier_config()
    if not cfg.enabled or not response_text:
        return response_text, None

    cfg, receipt = evaluate_response_verifier(
        response_text=response_text,
        messages=messages,
        session_id=session_id,
        model=model,
        platform=platform,
        config=cfg,
        evidence_start_index=evidence_start_index,
    )
    if receipt is None:
        return response_text, None

    if receipt.action == "allow":
        return response_text, receipt

    summary = _finding_summary(receipt)
    suffix = f"\n\n[Jiminy: {receipt.action} — {summary}"
    if cfg.include_receipt_in_response and receipt.receipt_path:
        suffix += f" Receipt: {receipt.receipt_path}"
    suffix += "]"

    if receipt.action == "block":
        response_text = render_blocked_response(receipt)
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
