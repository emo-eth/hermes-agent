# Hindsight Memory Quality Gates SPEC

Status: proposed
Owner: BMO runtime / Hindsight integration
Last updated: 2026-06-24

## Problem

Hindsight currently consolidates a large backlog of Hermes conversation memory, but the system still lets low-value memory enter or survive too far into the pipeline. The observed junk classes include:

- conversation/session meta such as “conversation occurred” or “Hermes Agent and Emo had a conversation”;
- vague file references such as “Emo referenced files” without durable content;
- assistant/tool process meta such as “assistant reviewed docs”;
- malformed extraction artifacts such as `assistant_fact:` or schema-field echoes;
- duplicate or near-duplicate low-information facts.

The current state is partial:

- E2B consolidation is running with conservative settings.
- A dry-run cleanup script exists and writes JSONL receipts.
- Consolidation has a live rejection path that logs `[CONSOLIDATION_QUALITY]` for some low-value proposed observations.
- The full pre-retain and post-extraction gates are not live.
- Existing backlog deletion is not live beyond dry-run classification.

This spec covers the missing work.

## Goals

1. Prevent obvious junk from entering Hindsight retain queues.
2. Prevent low-value extracted facts from becoming memory units.
3. Prevent consolidation from creating/updating low-value observations.
4. Provide auditable receipts for every keep/drop/review decision.
5. Enable safe, bounded backlog cleanup with hash-checked deletion ledgers.
6. Keep Hindsight health user-promise-aligned: not only daemon liveness, but useful memory quality, visible failures, and owned burndown.

## Non-goals

- Do not replace Hindsight’s extraction model.
- Do not route around Gemma 4 E2B to the resident 26B server.
- Do not delete memory without a fresh backup, deterministic reason, model adjudication when required, and row-hash recheck.
- Do not use broad whole-system validation as a per-item completion gate.

## Pipeline requirements

### Stage A: pre-retain deterministic gate

Gate raw Hermes chat/session content before it enters Hindsight retain queues.

Primary touchpoints:

- `plugins/memory/hindsight/__init__.py`
  - `HindsightMemoryProvider.sync_turn()` before `_retain_queue.put(...)`
  - session-switch flush path before queuing `_flush`
  - explicit `hindsight_retain` tool path, with a looser policy for manual retains
- Hindsight server defense-in-depth:
  - `hindsight_api/engine/memory_engine.py` before `submit_async_retain()` creates async operation rows

Decision contract:

```python
@dataclass(frozen=True)
class MemoryQualityDecision:
    allowed: bool
    action: Literal["keep", "drop", "review"]
    score: float
    reason_codes: list[str]
    content_hash: str
    receipt_id: str
    stage: Literal["pre_retain", "post_extraction", "consolidation"]
```

Rules:

- Drop empty/generic content.
- Drop pure tool/process/session meta.
- Drop vague file-reference chunks unless they contain a durable decision, blocker, validation result, or artifact invariant.
- Drop duplicate normalized payloads within a short rolling window.
- Keep durable preference/correction, stable environment/config facts, project decisions, artifact states with meaning, validation results, blockers, and explicit “remember/from now on” instructions.

Atomicity:

- If blocked before enqueue, no async operation is created.
- If partial filtering occurs server-side, only kept items are queued.
- Parent operation metadata must include kept/filtered/review counts.

Receipts:

- Write JSONL decisions under `~/.hermes/logs/hindsight-quality-gates/pre-retain-YYYYMMDD.jsonl`.
- Include source session/thread identifiers when available, normalized hash, reason codes, preview, and whether the item was queued.

### Stage B: post-extraction fact quality gate

Gate extracted facts after LLM extraction and before embeddings, DB insert, and downstream consolidation.

Primary touchpoints:

- `hindsight_api/engine/retain/orchestrator.py`
  - after `extract_facts_from_contents(...)`
  - before embeddings and `insert_facts_batch(...)`
- `hindsight_api/engine/retain/fact_storage.py`
  - defensive last-check in `insert_facts_batch()` so alternate callers cannot bypass the gate

Rules:

- Drop generic templates: “conversation occurred,” “user referenced files,” “assistant provided guidance,” etc.
- Drop malformed schema echoes.
- Drop low-density facts containing only vague verbs such as `discussed`, `mentioned`, `referenced`, `asked`, `helped`, unless a durable payload is present.
- Dedupe normalized fact text within the retain batch.
- Dedupe against recent same-bank memory hashes.
- Attach quality metadata to kept facts: gate version, score, reason codes, content hash.

Atomicity:

- If all extracted facts are rejected, insert no memory units.
- Mark the retain operation completed/filtered, not failed or pending.
- Do not enqueue consolidation for an operation that produced zero kept facts.

Receipts:

- Write JSONL under `~/.hermes/logs/hindsight-quality-gates/post-extraction-YYYYMMDD.jsonl`.
- Include operation ID, source content ID, candidate fact text hash, reason codes, and final action.

### Stage C: consolidation observation execution gate

Gate LLM-proposed observation creates/updates immediately before DB writes.

Current partial state:

- Some consolidation rejections already log `[CONSOLIDATION_QUALITY]`.

Required completion:

- Make the gate explicit, tested, and reusable rather than ad hoc.
- Apply it to both create and update actions.
- Treat rejected actions as skipped/no-durable-knowledge, not LLM failure.

Primary touchpoint:

- `hindsight_api/engine/consolidation/consolidator.py`
  - in create/update execution loops after source IDs are validated
  - before `_execute_create_action(...)` / `_execute_update_action(...)`

Rules:

- Drop conversation/session meta.
- Drop assistant/tool/process meta.
- Drop vague file-reference observations.
- Drop malformed extraction artifacts.
- Drop low-density vague action summaries unless a durable payload exists.
- Keep durable user preferences, operational/runtime facts, project decisions, concrete artifacts, and validation results.

Receipts/logging:

- Log a stable marker: `[CONSOLIDATION_QUALITY]`.
- Include action type, source fact IDs, reason codes, short preview, and decision.
- Write JSONL under `~/.hermes/logs/hindsight-quality-gates/consolidation-YYYYMMDD.jsonl`.

## Backlog cleanup requirements

Existing backlog cleanup must remain dry-run until all safety rails pass.

Required flow:

1. Verify fresh Hindsight backup.
2. Pause or bound consolidation and new auto-retain input.
3. Run deterministic dry-run classifier over target slices.
4. Send deterministic drop/review candidates to Gemma 4 E2B for adjudication.
5. Build deletion ledger with:
   - memory ID;
   - bank ID;
   - text preview;
   - text hash;
   - deterministic reasons;
   - Gemma decision/confidence;
   - source query/slice;
   - backup ID.
6. Delete only in bounded batches.
7. Recheck each row hash immediately before deletion.
8. Stop on hash mismatch, FK/orphan error, unexpected operation state, or model/adjudicator outage.
9. After each batch, report deleted/review/kept counts and examples.

Deletion policy:

- Deterministic keep always wins.
- Deterministic drop plus Gemma drop above threshold may delete.
- Deterministic drop plus Gemma review stays review.
- Gemma error/missing halts deletion or routes to review.
- No deletion path belongs in the dry-run binary.

## Health and observability

Health must expose more than `/health` daemon liveness.

Add a memory-quality health receipt containing:

- current `total_unconsolidated`;
- pending/active/failed retain and consolidation operations;
- latest pre-retain filtered count;
- latest post-extraction filtered count;
- latest consolidation rejected count;
- latest delete-ledger batch status;
- examples of recent drop/review/keep decisions;
- parse/schema/timeout/stuck error counts over the last run window;
- current model/provider path proving Gemma 4 E2B, not resident 26B fallback.

A green state means:

- daemon/database healthy;
- no orphan active task without retry owner;
- E2B route verified;
- no recent parse/schema/timeout/stuck storm;
- quality gates are writing receipts;
- burndown is moving or intentionally paused with a stated reason.

## Runtime configuration

Default safe rollout values for E2B consolidation while gates are young:

```text
HINDSIGHT_API_LLM_PROVIDER=llamacpp
HINDSIGHT_API_LLM_MODEL=gemma-4-e2b-it
HINDSIGHT_API_CONSOLIDATION_LLM_BATCH_SIZE=2
HINDSIGHT_API_CONSOLIDATION_MAX_MEMORIES_PER_ROUND=2
HINDSIGHT_API_LLM_MAX_RETRIES=0
HINDSIGHT_API_CONSOLIDATION_LLM_MAX_RETRIES=0
HINDSIGHT_API_CONSOLIDATION_MAX_ATTEMPTS=1
HINDSIGHT_API_LLM_TIMEOUT=180
HINDSIGHT_API_CONSOLIDATION_LLM_TIMEOUT=180
```

Do not set `HINDSIGHT_API_LLM_BASE_URL` to the resident 26B server for this workflow.

## Tests

### Unit tests

Add deterministic gate tests for each stage:

- rejects conversation meta;
- rejects assistant/tool process meta;
- rejects vague file reference;
- rejects malformed schema echo;
- keeps user preference;
- keeps operational/runtime fact;
- keeps project decision;
- keeps concrete artifact state;
- keeps validation result;
- dedupes normalized duplicate;
- manual retain path is looser but still blocks empty/malformed content.

### Integration tests

- Pre-retain blocked item creates no async operation.
- Pre-retain partial filter queues only kept content and records counts.
- Post-extraction all-drop path inserts zero memory units and completes/filters operation.
- Post-extraction mixed path inserts only kept facts and records metadata.
- Consolidation create gate rejects low-value observation and logs `[CONSOLIDATION_QUALITY]`.
- Consolidation update gate rejects degradation from durable observation to vague meta-summary.
- Dry-run cleanup writes JSONL receipts and never deletes.
- Delete runner refuses to run without backup ID and hash ledger.

### Runtime smoke

- Start Hindsight with E2B settings.
- Submit a fixture retain payload containing both junk and durable facts.
- Confirm:
  - junk is rejected at the earliest applicable stage;
  - durable fact survives;
  - receipts exist;
  - no parse/schema/timeout/stuck errors in the smoke window;
  - health endpoint/report shows quality-gate counts.

## Acceptance criteria

This work is complete only when:

- Pre-retain gate is live and tested.
- Post-extraction gate is live and tested.
- Consolidation create/update gate is live, tested, and emits receipts.
- Dry-run classifier has fixture coverage and sample receipts.
- Destructive cleanup requires backup ID, hash ledger, and bounded batch size.
- Health/reporting includes backlog, quality-gate, and E2B-route evidence.
- A live smoke proves junk is blocked and durable facts still land.
- Patch ledger and operational skill references are updated.

## Rollout plan

1. Implement shared deterministic classifier module with reason codes and hash normalization.
2. Wire pre-retain gate in Hermes plugin in dry-run/log-only mode.
3. Wire post-extraction gate in Hindsight retain path in dry-run/log-only mode.
4. Promote consolidation gate to shared classifier and cover create/update paths.
5. Enable enforcement for obvious drops only; review/borderline stays keep/log initially.
6. Run fixture retain smoke and compare inserted units.
7. Enable backlog dry-run slices and inspect receipts.
8. Enable bounded destructive cleanup only after backup and hash-ledger checks pass.
9. Add recurring quality-health report to the Hindsight monitor.

## Open questions

- Whether the pre-retain gate should live entirely in Hermes, entirely in Hindsight, or both with different strictness. This spec assumes both: Hermes for early drop, Hindsight for defense-in-depth.
- Whether review candidates should have a first-class DB state or remain receipt-only initially.
- Whether quality metadata belongs in memory-unit metadata, fact metadata, or a separate gate-decision table.
- How aggressively to pause auto-retain during initial backlog cleanup.
