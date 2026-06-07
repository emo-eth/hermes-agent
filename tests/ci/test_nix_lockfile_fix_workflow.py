from pathlib import Path

WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "nix-lockfile-fix.yml"


def _workflow_text() -> str:
    return WORKFLOW.read_text()


def _pr_fix_job_text() -> str:
    text = _workflow_text()
    start = text.index("  # ── PR fix")
    return text[start:]


def test_pr_fixer_does_not_run_pr_controlled_local_action_or_cache_secret() -> None:
    job = _pr_fix_job_text()

    assert "uses: ./.github/actions/nix-setup" not in job
    assert "secrets.CACHIX_AUTH_TOKEN" not in job


def test_pr_fixer_checks_out_pinned_head_sha_without_persisted_credentials() -> None:
    job = _pr_fix_job_text()

    assert "head_sha" in job
    assert "ref: ${{ steps.resolve.outputs.head_sha }}" in job
    assert "persist-credentials: false" in job


def test_pr_fixer_runs_untrusted_nix_without_github_tokens() -> None:
    job = _pr_fix_job_text()

    assert "GITHUB_TOKEN: ''" in job
    assert "GH_TOKEN: ''" in job


def test_pr_fixer_rechecks_head_sha_before_push() -> None:
    job = _pr_fix_job_text()

    assert "EXPECTED_HEAD_SHA=\"${{ needs.fix-generate.outputs.head_sha }}\"" in job
    assert "CURRENT_HEAD_SHA=\"$(git rev-parse HEAD)\"" in job
    assert "HEAD changed before push" in job


def test_pr_fixer_validates_and_quotes_push_ref() -> None:
    job = _pr_fix_job_text()

    assert 'TARGET_REF: ${{ needs.fix-generate.outputs.ref }}' in job
    assert 'TARGET_REF="${{ steps.resolve.outputs.ref }}"' not in job
    assert '[[ ! "$TARGET_REF" =~ ^[A-Za-z0-9._/-]+$ ]]' in job
    assert 'git -c core.hooksPath=/dev/null push origin "HEAD:${TARGET_REF}"' in job
    assert 'git push origin HEAD:${{ steps.resolve.outputs.ref }}' not in job


def test_pr_fixer_clears_and_checks_staged_changes_before_commit() -> None:
    job = _pr_fix_job_text()

    assert "git reset --mixed HEAD" in job
    assert "staged_unexpected=\"$(git diff --cached --name-only | grep -Ev '^nix/(tui|web)\\.nix$' || true)\"" in job
    assert "Unexpected staged files" in job


def test_pr_fixer_disables_git_hooks_for_privileged_commit_and_push() -> None:
    job = _pr_fix_job_text()

    assert "git -c core.hooksPath=/dev/null commit" in job
    assert "git -c core.hooksPath=/dev/null push" in job


def test_pr_fixer_uses_separate_untrusted_and_trusted_checkouts() -> None:
    job = _pr_fix_job_text()

    assert "path: untrusted-pr-head" in job
    assert "path: trusted-push" in job
    assert "working-directory: untrusted-pr-head" in job
    assert "cp generated-lockfiles/extracted/nix/tui.nix trusted-push/nix/tui.nix" in job
    assert "cp generated-lockfiles/extracted/nix/web.nix trusted-push/nix/web.nix" in job
    assert "cd trusted-push" in job
    assert "cd untrusted-pr-head" not in job
    assert "git remote set-url origin" in job
    assert "https://x-access-token:${{ secrets.GITHUB_TOKEN }}@github.com" in job


def test_pr_fixer_rejects_symlinked_lockfile_copy_paths() -> None:
    job = _pr_fix_job_text()

    assert 'for file in nix/tui.nix nix/web.nix; do' in job
    assert '[ ! -f "untrusted-pr-head/$file" ] || [ -L "untrusted-pr-head/$file" ]' in job
    assert '[ ! -f "generated-lockfiles/extracted/$file" ] || [ -L "generated-lockfiles/extracted/$file" ]' in job
    assert '[ ! -f "trusted-push/$file" ] || [ -L "trusted-push/$file" ]' in job
    assert 'Refusing unsafe lockfile path: $file' in job
    assert "tar -tf generated-lockfiles/nix-lockfile-fix-outputs.tar | sort" in job
    assert "Unexpected lockfile artifact contents" in job


def test_pr_fixer_separates_untrusted_generation_from_privileged_push_job() -> None:
    job = _pr_fix_job_text()

    assert "fix-generate:" in job
    assert "fix-push:" in job
    assert "needs: fix-generate" in job
    assert "changed: ${{ steps.apply.outputs.changed }}" in job
    assert "if: needs.fix-generate.outputs.changed == 'true'" in job
    assert "ref: ${{ needs.fix-generate.outputs.head_sha }}" in job
    assert "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02" in job
    assert "actions/download-artifact@d3f86a106a0bac45b974a628896c90dbdf5c8093" in job


def test_untrusted_generation_job_has_no_write_permissions_or_sticky_comment_actions() -> None:
    job = _pr_fix_job_text()
    generate = job[job.index("  fix-generate:"):job.index("  fix-current:")]

    assert "contents: read" in generate
    assert "pull-requests: write" not in generate
    assert "sticky-pull-request-comment" not in generate
