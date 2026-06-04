# Hermes Backup/Restore Audit - 2026-06-04

## Current Recovery Result

- Runtime fork cloned to `/Users/jameswenzel/dev/hermes-agent` from `emo-eth/hermes-agent`.
- Workspace backup cloned to `/Users/jameswenzel/dev/hermes-workspace-backup`;
  it was initially at `3aad33520`, then synced from live restored state and
  continues to be pushed by the restored five-minute scheduler.
- Beads tracker cloned to `/Users/jameswenzel/dev/hermes-beads`.
- Hermes installed via `./setup-hermes.sh`, which uses `uv` and Python 3.11.
- `~/.hermes` restored from `hermes-workspace-backup`; the pre-restore fresh home is preserved at `~/.hermes.pre-restore-20260604T180507Z`.
- `state.db` can be rebuilt from legacy session files with
  `scripts/rebuild_legacy_session_db.py`.
- SQLite-only and stale sessions are exported back to legacy JSONL with
  `scripts/export_state_sessions_to_legacy.py`; the live backup hook now runs
  this export before rsync/commit so the raw backup remains reconstructable
  without tracking `state.db`.
- After a successful backup push, the live backup hook now attempts a
  non-blocking `gh workflow run restore-drill.yml --repo emo-eth/hermes-agent`
  so fresh backups can immediately exercise the clean-container restore drill
  once the restore workflow is landed and `HERMES_RESTORE_TOKEN` is configured.
  The first post-hook push logged the expected GitHub 404 because
  `restore-drill.yml` is still only on the draft restore PR branch, not
  `main`; the backup push still completed successfully. The hook now rate
  limits restore-drill dispatch attempts with
  `HERMES_RESTORE_DRILL_MIN_INTERVAL_SECONDS` so periodic backup syncs do not
  spam GitHub/CI.
- A user LaunchAgent, `ai.hermes.workspace-backup`, now runs
  `~/.hermes/hooks/backup_push.sh` every 300 seconds on this Mac. The restore
  script auto-installs the same scheduler on macOS only for real default
  restores to `$HOME/.hermes`; temp/container restores skip it.
- Active restored path references are normalized with `scripts/normalize_legacy_paths.py`;
  historical session transcripts, logs, cron output, webhook captures, and
  snapshots are preserved as receipts.
- Local Obsidian is configured to open `/Users/jameswenzel/Documents/Sync`;
  the operational LLM wiki is `/Users/jameswenzel/Documents/Sync/wiki`.
  Live restored Hermes config, cron prompts, webhook subscriptions, and
  `OBSIDIAN_VAULT_PATH` were rewritten from `/Users/emo/Documents/Sync`.

## Verified State

- `hermes doctor` sees Python 3.11.15, full core packages, `~/.hermes/.env`, `config.yaml`, `SOUL.md`, memories, Discord tools, and `state.db`.
- `hermes status` shows OpenAI Codex logged in, Discord configured, 37 active cron jobs, and 689 active gateway sessions.
- A later live check on this recovery host showed Hermes installed at
  `/Users/jameswenzel/.local/bin/hermes`, running via launchd from
  `/Users/jameswenzel/dev/hermes-agent`, Discord connected as `bmo#1464`,
  37 active scheduled jobs, and 702 active sessions visible via
  `hermes status`.
- A later strict backup audit after live gateway/test activity showed the
  backup had drifted by one JSONL transcript plus 39 SQLite-only session rows.
  Running the restored backup hook exported 40 sessions and pushed backup commit
  `aa705ea61`. The follow-up strict audit passed with 905 live/backup JSONL
  transcripts, 702 `sessions.json` entries, 905 live SQLite sessions, 49,414
  live SQLite messages, no live DB sessions missing legacy files, no message
  drift, and no tracked/untracked backup `state.db`. Backup commit `84adf3c7f`
  then added the post-push restore-drill trigger hook; a follow-up strict audit
  still passed with the same session/message parity.
- A subsequent hook run exported new live session
  `20260604_144453_da1e1608`, pushed backup commit `35c167e23`, and confirmed
  the non-blocking restore-drill dispatch path. The dispatch failed with the
  expected workflow-not-on-`main` 404; the follow-up strict audit passed with
  906 live/backup JSONL transcripts, 703 `sessions.json` entries, 906 live
  SQLite sessions, 49,414 live SQLite messages, no live DB sessions missing
  legacy files, no message drift, and no tracked/untracked backup `state.db`.
- A later hook run refreshed `20260604_144453_da1e1608`, exported new live
  sessions `20260604_145013_268dcfc3` and `20260604_145044_e522949f`, pushed
  backup commit `e33db0c1e`, and again logged the expected non-blocking
  restore-drill 404 while the workflow is still not on `main`. The follow-up
  strict audit passed with 908 live/backup JSONL transcripts, 705
  `sessions.json` entries, 908 live SQLite sessions, 49,505 live SQLite
  messages, no live DB sessions missing legacy files, no message drift, and no
  tracked/untracked backup `state.db`.
- Backup freshness then drifted again while the live gateway was active, which
  proved the weekly cron was not enough. A new LaunchAgent was installed at
  `/Users/jameswenzel/Library/LaunchAgents/ai.hermes.workspace-backup.plist`
  with `StartInterval` 300. Its first run exposed a transient rsync race on
  `.skills_prompt_snapshot.json`; the hook now excludes that volatile file.
  Follow-up scheduled/manual hook runs pushed backup commits including
  `c324936a7`, `341d3eeea`, `a34d1d6b1`, `34029bdc5`, `f617db8e9`, and
  `1eb939f7c`. Strict audit reports such as
  `/tmp/hermes-backup-audit-1eb939f7c.json` pass with 915
  live/backup JSONL transcripts, 712 `sessions.json` entries, 915 live SQLite
  sessions, 50,440 live SQLite messages, no missing legacy session coverage, no
  JSONL message drift, and no tracked/untracked backup `state.db`.
- A clean restore from the original backup reconstructed 840 sessions and
  47,661 messages in SQLite. After exporting SQLite-only/stale live sessions
  and syncing backup commit `4020dd1f3`, a throwaway full host restore from the
  raw backup reconstructed 865 sessions and 49,297 messages. After later
  gateway/test activity, backup commit `aa705ea61` exported the remaining live
  SQLite-only rows; the latest container restore rebuilds 908 sessions and
  49,505 messages, matching the latest strict live/backup audit.
- `scripts/audit_hermes_backup.py --fail-on-state-legacy-gaps
  --fail-on-untracked-state-db` now verifies that live and backup have 908 JSONL
  transcripts, 705 `sessions.json` entries, no missing files, no JSONL message
  drift, no live DB sessions without legacy representation, no message-bearing
  DB sessions without JSONL, and no tracked/untracked backup `state.db`.
- `hermes -z 'Smoke test...' --ignore-rules` returned the expected response.
- A host-side throwaway restore to `/tmp/hermes-restore-host-check.../home`
  passed with `--obsidian-vault /Users/jameswenzel/Documents/Sync`. The report
  showed `missing_required_env` empty, `auth_json_present: yes`, and active
  restored Obsidian paths normalized from `/Users/emo/Documents/Sync` to the
  local vault path.
- A second throwaway restore passed with `--prompt-missing-env` and a fake
  `HERMES_RESTORE_PROMPT_TEST` required variable. The script prompted for the
  missing value, wrote it to the restored `.env`, and the final report showed
  `missing_required_env` empty without printing the value.
- After backup sync, `/tmp/hermes-restore-report-after-backup-sync.json` passed
  with `doctor_status`, `status_status`, `sessions_status`, `cron_status`, and
  `gateway_check_status` all 0; 37 active/38 total cron jobs; no missing
  required env values; no active legacy Hermes, backup-destination, backup
  runtime, or Obsidian paths after normalization. The restore script now also
  normalizes restored backup-hook agent checkout paths to the chosen
  `--agent-dir`/`--repos-dir` checkout.
- `scripts/test-hermes-restore-container.sh` passed again in a clean `debian:13.4`
  container against backup commit `e60f7749d`. It restored into
  `/tmp/hermes-home`, rebuilt 915 sessions and 50,440 messages, ran `hermes doctor`, `hermes status`, `hermes sessions
  stats`, `hermes cron list`, and `hermes gateway status` with status 0, and
  validated the machine-readable restore report. Latest report:
  `/tmp/hermes-restore-report-with-commits.json`, timestamp
  `20260604T232353Z`, with exact agent, backup, and beads commit SHAs embedded.
  The restore report correctly shows
  `backup_scheduler_status: skipped` because this was a temp/container restore
  to `/tmp/hermes-home`, not a real macOS restore to `$HOME/.hermes`.

## Deficiencies Found

1. Raw Git restore is not a one-click Hermes import.
   `hermes-workspace-backup` is a raw `~/.hermes` tree, not a `hermes backup` zip. A fresh machine cannot use `hermes import` directly against it.

2. The backup includes large runtime/workspace artifacts.
   The raw backup is several GB and includes nested runtime copies, worktrees, logs, artifacts, caches, and media. That helps forensic recovery, but it is slow to clone and expensive as the default bootstrap path.

3. SQLite session index was not recoverable as-is.
   The backup had `sessions/sessions.json` and JSONL transcripts, but restored
   `state.db` had zero sessions/messages. The current CLI did not rebuild
   SQLite from legacy files. `scripts/rebuild_legacy_session_db.py` was added
   as a recovery utility.

4. Legacy transcript coverage was incomplete and stale.
   The first strict audit found 12 live SQLite sessions with no legacy
   representation and 13 existing JSONL transcripts whose message counts lagged
   `state.db`. `scripts/export_state_sessions_to_legacy.py` now writes missing
   JSONL files and refreshes stale transcripts from SQLite. It preserves DB
   `session_meta` messages with a `db_message` marker so rebuilds do not confuse
   real DB messages with synthetic JSONL metadata headers. This closed the
   observed session/message parity gap for backup commit `4020dd1f3`. The live
   backup hook now runs this exporter with `--refresh-mismatched` before rsync.

5. Restore depends on machine-local absolute paths.
   Several cron jobs and workspace entries referenced `/Users/emo/.hermes/...`
   and `/Users/emo/Documents/Sync/...`. The restore script now rewrites active
   Hermes-home paths to the target `HERMES_HOME` and can rewrite the restored
   Obsidian vault path with `--obsidian-vault DIR` or auto-detection from the
   local Obsidian app/`~/Documents/Sync/.obsidian`. Historical receipts still
   preserve old paths by design.

6. Fork reconciliation is pushed and reviewable, but not landed into the
   active runtime.
   The fork is based on an older upstream and currently carries four commits
   not present upstream:
   - `7c05bdb41` local wiki retrieval tools
   - `2c4f5a05c` gateway shutdown and Hindsight retain backpressure
   - `99050ffd2` Discord missed-message startup backfill
   - `d0db73807` Discord parent-message masking fix
   Current fork head is `origin/main` at `d0db73807`; current upstream head is
   `upstream/main` at `acce1a245`. The reconciliation is now pushed as
   `origin/reconcile/fork-on-upstream-2026-06-04`, ending at `0956ae607`. It
   carries the four fork commits onto upstream plus one test import adjustment.
   Wiki tools and Hindsight hardening cherry-picked cleanly; Discord backfill
   needed manual resolution in `plugins/platforms/discord/adapter.py` plus
   moving the YAML env bridge from old `gateway/config.py` into the plugin
   `_apply_yaml_config` hook. Focused tests passed after the port:
   `tests/gateway/test_discord_missed_message_backfill.py`,
   `tests/gateway/test_hindsight_retain_guardrails.py`, and
   `tests/tools/test_wiki_tool.py` (`24 passed` via `uv run --frozen --python
   3.11 --extra dev pytest ...`). This port is now open as draft upstream PR
   `NousResearch/hermes-agent#39316`. The active runtime checkout has not been
   switched to this branch because it still has restore work and unrelated
   local edits.

7. Optional runtime dependencies are missing.
   Doctor reports `agent-browser`, browser CDP, computer use, Home Assistant, MOA/RL keys, and Hindsight availability gaps. Cron also surfaced missing `qmd` and a Quartz rebuild command failure.
   Current live memory status reports Hindsight configured but unavailable until
   `HINDSIGHT_API_KEY` and `HINDSIGHT_LLM_API_KEY` are supplied.

8. Credential readiness was not explicit in restore reports.
   The restore report now includes `required_env`, `missing_required_env`, and
   `auth_json_present` so a fresh machine can fail fast with a named checklist
   instead of discovering missing Discord/Codex credentials later. The restore
   script also supports `--prompt-missing-env` to collect missing values during
   an interactive one-button restore.

9. Backup validation is not yet proven in CI.
   The workflow now exists and backup pushes now attempt to dispatch it after a
   successful mirror push, but it has not run successfully in GitHub Actions
   with the private restore token. The current drill also skips the LLM smoke
   test and does not start a real gateway platform session.

10. CI requires a private-repo restore token before it can run.
   `.github/workflows/restore-drill.yml` now defines the scheduled/manual
   restore drill, but it needs a `HERMES_RESTORE_TOKEN` repository secret with
   read access to the private `hermes-workspace-backup` and `hermes-beads`
   repositories.

10a. The repository's broad `test` job is red on the restore PR for base-suite
    failures that reproduce on `origin/main`.
    Restore-specific checks are green locally and on GitHub where applicable:
    attribution, Windows footguns, ruff, e2e, Nix macOS/Ubuntu, and supply-chain
    checks pass on PR #5. The remaining `test` job fails on unrelated existing
    tests, including auxiliary model selection, TTS media routing, update prompt
    fixture setup, gateway restart PID filtering, plugin web route auth, builtin
    tool discovery, and vision fast-path behavior. A representative subset was
    reproduced on a clean detached `origin/main` worktree with
    `uv run --frozen --python 3.11 --extra all --extra dev pytest ...` and noted
    on PR #5.

11. Backup freshness is not continuous.
    The backup repo at `3aad33520` was stale relative to the live restored
    agent on this machine: it was missing 12 June 4 JSONL transcripts and 10
    `sessions.json` entries. Later, after the gateway and test runs created more
    live sessions, backup commit `4020dd1f3` was missing one JSONL transcript and
    39 SQLite-only session rows. The live backup hook was also still pointed at
    `/Users/emo/Backups/hermes-workspace-backup` and
    `/Users/emo/Library/Application Support/hermes-workspace-backup`, so it
    could not keep this machine current. The hook paths were normalized to
    `/Users/jameswenzel/dev/hermes-workspace-backup` and
    `/Users/jameswenzel/Library/Application Support/hermes-workspace-backup`,
    the hook was fixed to exclude `state.db*`, and backup commits through
    `e33db0c1e` were pushed. A later live drift audit still found new active
    sessions and stale transcripts, so the backup hook is now run by a macOS
    LaunchAgent every 300 seconds. The hook also rate-limits restore-drill
    dispatch attempts and excludes volatile `.skills_prompt_snapshot.json` so a
    disappearing source file cannot abort the mirror. The scheduler keeps
    pushing fresh backup commits, and `scripts/audit_hermes_backup.py` now proves
    file freshness plus message-count parity between live SQLite, live JSONL,
    and backup JSONL.

12. Raw-backup parity is now proven for sessions/messages, but not every runtime
    surface is one-click proven.
    Strict audits of scheduled backup commits prove raw-backup parity for 915
    sessions and 50,440 live SQLite messages at audit time. The latest clean container
    restore proves 915 sessions and 50,440 messages from the raw backup snapshot
    at commit `e60f7749d` that it mounted. Remaining unproven surfaces are CI execution with private repo
    token, live Discord gateway startup with user-provided credentials, and a
    full OAuth/provider credential bootstrap on a brand-new machine.

## Proposed Robustness Test

Create a restore harness that runs on demand and in CI. The local harness now
exists as `scripts/test-hermes-restore-container.sh`; it mounts local checkouts
read-only, copies the runtime into the container, restores into `/tmp/hermes-home`,
skips the LLM smoke test, and fails if sessions/messages are not restored.

The CI harness now exists as `.github/workflows/restore-drill.yml`. It runs
weekly, on manual dispatch, when restore scripts change on `main`, and as a
PR preflight when restore scripts or the workflow change. Full restore runs
check out:

- `emo-eth/hermes-agent`
- `emo-eth/hermes-workspace-backup`
- `emo-eth/hermes-beads`

Then it runs `scripts/test-hermes-restore-container.sh` with `--report` and
uploads `hermes-restore-report.json` as a workflow artifact. Before the private
backup token gate, it also runs
`scripts/bootstrap-hermes-restore.sh --check-only` with `GH_TOKEN` from the
workflow token so bootstrap regressions are caught even when the private restore
secret is missing. Pull requests without the private token stop after that
public bootstrap preflight and record that the full clean-container drill was
skipped; scheduled/manual/main runs still fail loudly without the token. To
enable the full restore drill, add a repository secret:

```text
HERMES_RESTORE_TOKEN=<fine-grained GitHub token with read access to all three repos>
```

Backup-repo pushes now attempt to trigger this workflow from
`hooks/backup_push.sh` after a successful push:

```bash
gh workflow run restore-drill.yml --repo emo-eth/hermes-agent --ref main
```

The trigger is non-blocking so a missing workflow, missing GitHub auth, or
missing `HERMES_RESTORE_TOKEN` does not make the backup push fail. The weekly
schedule still catches stale backups after the fact.

Freshness audit:

```bash
UV_PROJECT_ENVIRONMENT=/Users/jameswenzel/dev/hermes-agent/venv \
  uv run --frozen --python 3.11 scripts/audit_hermes_backup.py \
  --live-home /Users/jameswenzel/.hermes \
  --backup-dir /Users/jameswenzel/dev/hermes-workspace-backup \
  --report /tmp/hermes-backup-freshness.json \
  --fail-on-untracked-state-db
```

Strict parity audit:

```bash
UV_PROJECT_ENVIRONMENT=/Users/jameswenzel/dev/hermes-agent/venv \
  uv run --frozen --python 3.11 scripts/audit_hermes_backup.py \
  --live-home /Users/jameswenzel/.hermes \
  --backup-dir /Users/jameswenzel/dev/hermes-workspace-backup \
  --report /tmp/hermes-backup-freshness-strict.json \
  --fail-on-untracked-state-db \
  --fail-on-state-legacy-gaps \
  --max-extra-jsonl 0 \
  --max-extra-index 0
```

Local drill:

```bash
scripts/test-hermes-restore-container.sh \
  --backup-dir /Users/jameswenzel/dev/hermes-workspace-backup \
  --beads-dir /Users/jameswenzel/dev/hermes-beads \
  --report /tmp/hermes-restore-report.json
```

Latest local container result:

- container image: `debian:13.4`
- report timestamp: `20260604T232353Z`
- report: `/tmp/hermes-restore-report-with-commits.json`
- agent commit: `0e59a2d6ea0fa822ba1ba82461e4dc3d2951fdab`
- backup commit: `e60f7749dc42a7b944967e02f0154f67e1395ed3`
- beads commit: `86d4cdc140ca76922000c9afe025aaf06a16aa86`
- `doctor_status`: 0
- `status_status`: 0
- `sessions_status`: 0
- `cron_status`: 0
- `gateway_check_status`: 0
- `session_count`: 915
- `message_count`: 50,440
- cron jobs: 37 active, 38 total
- required cron jobs missing: none
- required restore env vars: `DISCORD_BOT_TOKEN,DISCORD_ALLOWED_USERS`
- required restore env vars missing: none
- `auth.json`: present
- active legacy `/Users/emo/.hermes` path references after normalize: expected 0
- active legacy `/Users/emo/.hermes` path references before normalize: 31 active
- active legacy `/Users/emo/.hermes` path references after normalize: 0
- restored `/Users/emo/.hermes` replacements: 461 across 31 active files
- restored legacy agent-dir replacements: 1
- active legacy agent-dir paths after normalize: 0
- Obsidian vault path normalization: skipped in clean container because no host
  Obsidian vault is mounted and no `--obsidian-vault` override was passed. On
  this Mac, Obsidian's app registry lists a single open vault at
  `/Users/jameswenzel/Documents/Sync`, and `/Users/emo/Documents/Sync` was
  rewritten to that path in active live state and in a throwaway host restore.
- historical legacy `/Users/emo/.hermes` path references remain in receipts
- gateway: status command works, but the service is not started by this drill
- backup scheduler: skipped, because the drill restores to `/tmp/hermes-home`
  rather than a real macOS `$HOME/.hermes`
- host report export: `/tmp/hermes-restore-report-with-commits.json` was
  written and parsed successfully

Strict backup audit after scheduler sync:

- audited backup commits: `1eb939f7c`, `0767d1bc6`, `e60f7749d`
- strict audit reports: `/tmp/hermes-backup-audit-1eb939f7c.json`,
  `/tmp/hermes-backup-audit-0767d1bc6.json`,
  `/tmp/hermes-backup-audit-e60f7749.json`
- latest container restore report: `/tmp/hermes-restore-report-with-commits.json`
- `doctor_status`: 0
- `status_status`: 0
- `sessions_status`: 0
- `cron_status`: 0
- `gateway_check_status`: 0
- `smoke_status`: 0, skipped intentionally for no-LLM restore verification
- `session_count`: 915
- `message_count`: 50,440 in both the latest container restore and latest
  strict audit
- real restored `session_meta` DB messages: 13
- cron jobs: 37 active, 38 total
- required cron jobs missing: none
- required env vars missing: none
- `auth.json`: present
- active legacy Hermes paths after normalize: 0
- active legacy backup-destination paths after normalize: 0
- active legacy backup-runtime paths after normalize: 0
- active legacy agent-dir paths after normalize: 0
- active legacy Obsidian paths after normalize: 0
- live/backup JSONL transcript count: 915/915
- live/backup `sessions.json` entry count: 712/712
- missing backup JSONL files: 0
- missing backup `sessions.json` entries: 0
- live JSONL message mismatches against `state.db`: 0
- backup JSONL message drift from live JSONL: 0
- live message-bearing DB sessions without JSONL: 0
- live DB sessions without legacy representation: 0
- backup `state.db`: absent and not tracked, by design

## Fork Reconciliation Drill

Do not merge upstream into the live checked-out runtime in place. The active
worktree has local restore work and separate runtime edits. The durable tested
branch is:

```bash
git fetch origin --prune
git switch reconcile/fork-on-upstream-2026-06-04
```

It is pushed at `origin/reconcile/fork-on-upstream-2026-06-04`, ending at
`0956ae607`. To recreate the branch from scratch, the tested safe path is:

```bash
git fetch origin --prune
git fetch upstream --prune
git worktree add --detach /tmp/hermes-port upstream/main
cd /tmp/hermes-port
git cherry-pick 7c05bdb41
git cherry-pick 2c4f5a05c
git cherry-pick 99050ffd2
# Resolve:
# - gateway/config.py: keep upstream plugin dispatch; do not re-add old
#   centralized Discord config block.
# - plugins/platforms/discord/adapter.py: keep upstream _last_self_message_id
#   cache and also record durable Discord response state; add
#   missed_message_backfill env bridging to _apply_yaml_config.
git cherry-pick d0db73807
```

After resolving the import path in the Discord backfill test to
`plugins.platforms.discord.adapter`, this focused verification passed:

```bash
UV_PROJECT_ENVIRONMENT=/tmp/hermes-port/.venv \
  uv run --frozen --python 3.11 --extra dev pytest \
  tests/gateway/test_discord_missed_message_backfill.py \
  tests/gateway/test_hindsight_retain_guardrails.py \
  tests/tools/test_wiki_tool.py
```

Result: `24 passed in 1.22s`.

Latest pushed branch verification:

- branch: `reconcile/fork-on-upstream-2026-06-04`
- pushed remote: `origin/reconcile/fork-on-upstream-2026-06-04`
- head: `0956ae607`
- base: `upstream/main` at `acce1a245`
- command:
  `UV_PROJECT_ENVIRONMENT=/private/tmp/hermes-port.vVXFb5/.venv uv run --frozen --python 3.11 --extra dev pytest tests/gateway/test_discord_missed_message_backfill.py tests/gateway/test_hindsight_retain_guardrails.py tests/tools/test_wiki_tool.py`
- result: `24 passed in 0.64s`

The full procedure should become:

1. Build the Hermes fork image:
   `docker build -t hermes-restore-test .`

2. Start from an empty temp volume:
   `docker volume create hermes-restore-test`

3. Clone or mount `hermes-workspace-backup` into a helper container.

4. Restore using a single script:
   - install/verify Hermes runtime from `emo-eth/hermes-agent`
   - copy backup state into `HERMES_HOME`
   - normalize active host paths from `/Users/emo/.hermes` to the target `HERMES_HOME` while preserving historical receipts
   - normalize active Obsidian paths from `/Users/emo/Documents/Sync` to the
     host vault path, if provided or detected
   - require secrets via `.env` paste or mounted secret file
   - prompt for missing required env vars when `--prompt-missing-env` is passed
   - rebuild `state.db` from legacy transcripts if `sessions` table is empty

5. Run gates:
   - `hermes doctor`
   - `hermes status`
   - `hermes sessions stats` must be nonzero
   - `hermes cron list` must include required parity jobs
   - `hermes -z 'reply HERMES_SMOKE_OK' --ignore-rules`
   - `hermes gateway status` must report installed/runnable state, or foreground `timeout 20 hermes gateway run` must reach platform initialization without config errors

6. Emit a machine-readable report:
   `restore-report.json` with pass/fail, exact agent/backup/beads commit SHAs,
   session counts, status/doctor/cron/gateway command statuses, missing env
   keys, required cron misses, auth-file presence, and
   Hermes-home/backup/agent/Obsidian path-rewrite counts.

## Target One-Button Flow

The paste-on-a-fresh-machine entrypoint now exists as
`scripts/bootstrap-hermes-restore.sh`. It checks or installs `git`, `rsync`,
`curl`, `gh`, and `uv`, verifies `gh auth status`, then delegates to
`scripts/restore-hermes.sh`. On Linux it handles root or `sudo` shells and
installs GitHub CLI from GitHub's apt repository when `gh` is not already
available.

From a checked-out runtime repo:

```bash
scripts/bootstrap-hermes-restore.sh
```

To verify the host is ready without starting a restore:

```bash
scripts/bootstrap-hermes-restore.sh --check-only
```

Default restore options are:

```bash
scripts/restore-hermes.sh --start-gateway --prompt-missing-env
```

Once the script is landed on GitHub, the desired remote bootstrap form is:

```bash
curl -fsSL https://raw.githubusercontent.com/emo-eth/hermes-agent/main/scripts/bootstrap-hermes-restore.sh | bash
```

To pass non-default restore options through the bootstrap:

```bash
curl -fsSL https://raw.githubusercontent.com/emo-eth/hermes-agent/main/scripts/bootstrap-hermes-restore.sh | bash -s -- --skip-smoke --prompt-missing-env
```

The script should prompt for or accept:

- GitHub auth already present via `gh`
- Discord token and allowed users if not present in backup
- OpenAI Codex/OAuth bootstrap or provider API keys
- optional GitHub token for Skills Hub/API rate limits

Then it should leave the machine with:

- `hermes` on PATH
- restored `~/.hermes`
- active restored paths normalized to the target `~/.hermes`
- active restored Obsidian paths normalized to the local vault path when
  detected or passed with `--obsidian-vault`
- missing required `.env` values prompted for and written when
  `--prompt-missing-env` is passed
- rebuilt session index
- gateway service installed but not necessarily started unless `--start-gateway` is passed
- a restore report proving parity
