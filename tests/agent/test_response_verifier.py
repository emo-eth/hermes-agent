from pathlib import Path

from agent.response_verifier import (
    ResponseVerifierConfig,
    apply_response_verifier,
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
