from pathlib import Path
from types import SimpleNamespace

from agent.response_verifier import (
    ResponseVerifierConfig,
    apply_response_verifier,
    build_verifier_evidence_bundle,
    verify_response,
)


def test_warns_on_completion_claim_without_tool_evidence():
    receipt = verify_response(
        response_text="Done. Tests passed and I pushed the commit.",
        messages=[{"role": "user", "content": "fix it"}],
        config=ResponseVerifierConfig(enabled=True, mode="warn"),
    )

    assert receipt.ok is False
    assert receipt.action == "warn"
    assert {finding.code for finding in receipt.findings} >= {"tests_passed", "pushed"}


def test_allows_completion_claim_with_current_turn_evidence():
    receipt = verify_response(
        response_text="Done. Tests passed and I pushed the commit.",
        messages=[
            {"role": "assistant", "tool_calls": [{"function": {"name": "terminal"}}]},
            {"role": "tool", "content": "git commit -m 'x'\n[main abc123] x\nbun test\n1 passed\ngit push\nset up to track origin/main\nexit_code: 0"},
        ],
        config=ResponseVerifierConfig(enabled=True, mode="warn"),
    )

    assert receipt.ok is True
    assert receipt.action == "allow"
    assert receipt.findings == []


def test_apply_response_verifier_warns_writes_receipt_and_updates_message(tmp_path: Path):
    messages = [
        {"role": "user", "content": "do it"},
        {"role": "assistant", "content": "Done. Tests passed."},
    ]

    updated, receipt = apply_response_verifier(
        response_text="Done. Tests passed.",
        messages=messages,
        session_id="sess/test",
        model="test-model",
        platform="cli",
        config=ResponseVerifierConfig(
            enabled=True,
            mode="warn",
            receipt_dir=str(tmp_path),
            include_receipt_in_response=True,
        ),
    )

    assert receipt is not None
    assert receipt.action == "warn"
    assert receipt.receipt_path is not None
    assert Path(receipt.receipt_path).exists()
    assert "[Jiminy: warn" in updated
    assert "Receipt:" in updated
    assert messages[-1]["content"] == updated


def test_block_mode_replaces_unsupported_response(tmp_path: Path):
    updated, receipt = apply_response_verifier(
        response_text="Done. I committed and pushed it.",
        messages=[{"role": "assistant", "content": "Done. I committed and pushed it."}],
        config=ResponseVerifierConfig(enabled=True, mode="block", receipt_dir=str(tmp_path)),
    )

    assert receipt is not None
    assert receipt.action == "block"
    assert updated.startswith("Jiminy blocked this response")


def test_retry_prompt_preserves_candidate_and_demands_evidence():
    from agent.response_verifier import build_verifier_retry_prompt

    receipt = verify_response(
        response_text="Done. Tests passed.",
        messages=[{"role": "user", "content": "ship it"}],
        config=ResponseVerifierConfig(enabled=True, mode="block"),
    )

    prompt = build_verifier_retry_prompt(
        candidate_response="Done. Tests passed.",
        receipt=receipt,
        attempt=1,
        max_repairs=2,
    )

    assert "Jiminy blocked" in prompt
    assert "Tests passed" in prompt
    assert "current-turn tool evidence" in prompt
    assert "Do not repeat" in prompt


def test_llm_judge_blocks_repair_verdict(monkeypatch):
    calls = []

    def fake_call_llm(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content='{"verdict":"repair","issues":[{"code":"unsupported_push","message":"No git push evidence"}]}'
                    )
                )
            ]
        )

    monkeypatch.setattr("agent.response_verifier.call_llm", fake_call_llm)

    receipt = verify_response(
        response_text="Done. I pushed it.",
        messages=[{"role": "user", "content": "ship it"}],
        config=ResponseVerifierConfig(enabled=True, mode="block", backend="llm"),
    )

    assert receipt.ok is False
    assert receipt.action == "block"
    assert [finding.code for finding in receipt.findings] == ["unsupported_push"]
    assert calls[0]["task"] == "response_verifier"
    assert "candidate response" in calls[0]["messages"][1]["content"].lower()


def test_llm_judge_failure_fail_closed_blocks(monkeypatch):
    def fake_call_llm(**kwargs):
        raise RuntimeError("judge unavailable")

    monkeypatch.setattr("agent.response_verifier.call_llm", fake_call_llm)

    receipt = verify_response(
        response_text="Looks fine.",
        messages=[{"role": "user", "content": "status"}],
        config=ResponseVerifierConfig(enabled=True, mode="block", backend="llm", fail_closed=True),
    )

    assert receipt.ok is False
    assert receipt.action == "block"
    assert receipt.findings[0].code == "llm_judge_failed"


def test_llm_judge_receives_current_turn_tool_evidence_and_not_stale_history(monkeypatch):
    calls = []

    def fake_call_llm(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content='{"verdict":"pass","issues":[],"confidence":0.99}'))]
        )

    monkeypatch.setattr("agent.response_verifier.call_llm", fake_call_llm)

    messages = [
        {"role": "user", "content": "old turn"},
        {"role": "tool", "name": "terminal", "content": "old stale failure", "tool_call_id": "old"},
        {"role": "user", "content": "status now"},
        {"role": "assistant", "tool_calls": [{"id": "tc1", "function": {"name": "terminal", "arguments": "{}"}}]},
        {"role": "tool", "name": "terminal", "content": "pytest tests/agent/test_response_verifier.py -q\n8 passed\nexit_code: 0", "tool_call_id": "tc1"},
        {"role": "assistant", "content": "Tests passed."},
    ]

    receipt = verify_response(
        response_text="Tests passed.",
        messages=messages,
        evidence_start_index=2,
        config=ResponseVerifierConfig(enabled=True, mode="block", backend="llm"),
    )

    assert receipt.action == "allow"
    prompt = calls[0]["messages"][1]["content"]
    assert "8 passed" in prompt
    assert "old stale failure" not in prompt


def test_retry_prompt_carries_evidence_bundle_for_repair_model():
    messages = [
        {"role": "user", "content": "status now"},
        {"role": "assistant", "tool_calls": [{"id": "tc1", "function": {"name": "terminal", "arguments": "{}"}}]},
        {"role": "tool", "name": "terminal", "content": "git status --short --branch\n## main...origin/main [ahead 1]\nexit_code: 0", "tool_call_id": "tc1"},
    ]
    receipt = verify_response(
        response_text="Pushed and upstream merged.",
        messages=messages,
        config=ResponseVerifierConfig(enabled=True, mode="block", backend="deterministic"),
    )
    evidence = build_verifier_evidence_bundle(messages=messages, max_chars=10_000)

    from agent.response_verifier import build_verifier_retry_prompt

    prompt = build_verifier_retry_prompt(
        candidate_response="Pushed and upstream merged.",
        receipt=receipt,
        attempt=1,
        max_repairs=2,
        evidence_bundle=evidence,
    )

    assert "Current evidence available to repair against" in prompt
    assert "git status --short --branch" in prompt
    assert "ahead 1" in prompt


def test_adaptive_backend_passes_grounded_source_claim_without_llm(monkeypatch):
    def fail_if_called(**kwargs):
        raise AssertionError("adaptive verifier should not call LLM when source/action evidence is present")

    monkeypatch.setattr("agent.response_verifier.call_llm", fail_if_called)

    receipt = verify_response(
        response_text=(
            "I checked the official Anthropic docs. Claude Code legal/compliance says "
            "Pro and Max limits assume ordinary individual usage."
        ),
        messages=[
            {"role": "user", "content": "check Anthropic terms"},
            {"role": "assistant", "tool_calls": [{"id": "web1", "function": {"name": "web_extract", "arguments": "{}"}}]},
            {
                "role": "tool",
                "name": "web_extract",
                "tool_call_id": "web1",
                "content": "https://code.claude.com/docs/en/legal-and-compliance\nAdvertised usage limits for Pro and Max plans assume ordinary, individual usage of Claude Code and the Agent SDK.",
            },
        ],
        config=ResponseVerifierConfig(enabled=True, mode="block", backend="adaptive"),
    )

    assert receipt.ok is True
    assert receipt.action == "allow"


def test_adaptive_backend_blocks_source_claim_without_source_evidence():
    receipt = verify_response(
        response_text="I checked the official docs. They say this is compliant.",
        messages=[{"role": "user", "content": "is this compliant?"}],
        config=ResponseVerifierConfig(enabled=True, mode="block", backend="adaptive"),
    )

    assert receipt.ok is False
    assert receipt.action == "block"
    assert {finding.code for finding in receipt.findings} >= {"source_claim_without_evidence"}


def test_adaptive_backend_does_not_treat_unrelated_tool_output_as_source_evidence():
    receipt = verify_response(
        response_text="I checked the official docs. They say this is compliant.",
        messages=[
            {"role": "user", "content": "is this compliant?"},
            {"role": "assistant", "tool_calls": [{"id": "tc1", "function": {"name": "terminal", "arguments": "{}"}}]},
            {"role": "tool", "name": "terminal", "tool_call_id": "tc1", "content": "pwd\n/tmp/project\nexit_code: 0"},
        ],
        config=ResponseVerifierConfig(enabled=True, mode="block", backend="adaptive"),
    )

    assert receipt.ok is False
    assert receipt.action == "block"
    assert {finding.code for finding in receipt.findings} >= {"source_claim_without_evidence"}


def test_adaptive_backend_passes_casual_design_opinion_without_llm(monkeypatch):
    def fail_if_called(**kwargs):
        raise AssertionError("adaptive verifier should not LLM-judge casual design framing")

    monkeypatch.setattr("agent.response_verifier.call_llm", fail_if_called)

    receipt = verify_response(
        response_text="My preferred design is a small local Claude Code worker with bounded tools. Cleaner and less spooky.",
        messages=[{"role": "user", "content": "what design should we use?"}],
        config=ResponseVerifierConfig(enabled=True, mode="block", backend="adaptive"),
    )

    assert receipt.ok is True
    assert receipt.action == "allow"


def test_adaptive_backend_uses_llm_only_after_deterministic_findings(monkeypatch):
    calls = []

    def fake_call_llm(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content='{"verdict":"repair","issues":[{"code":"unsupported_tests","message":"No test evidence"}]}'))]
        )

    monkeypatch.setattr("agent.response_verifier.call_llm", fake_call_llm)

    receipt = verify_response(
        response_text="Done. Tests passed.",
        messages=[{"role": "user", "content": "fix it"}],
        config=ResponseVerifierConfig(enabled=True, mode="block", backend="adaptive"),
    )

    assert receipt.ok is False
    assert receipt.action == "block"
    assert calls
