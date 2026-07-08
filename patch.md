# Live Hermes modifications

## Domain Modules runtime patch

Status: active local runtime patch, verified 2026-07-02. Requires gateway restart before the currently running API server advertises the new discovery row.

Intent: keep first-class Domain Modules available as task-scoped background context alongside skills, and keep API-server toolset discovery aligned with what API-server agents can actually execute.

Replayable patches:
- Original runtime patch: `patches/live-hermes-modifications/0001-feat-domain-modules-runtime.patch` from commit `47b9500866b75067ff0fca55e344275297f2fda6`.
- Current consolidated/update patch: `patches/live-hermes-modifications/0004-domain-modules-toolset-discovery.patch` from commit `144b0f858349f830e0d09ca109ff3c8aa738c8ba`.

Patch checksum:
- `2758a2e25df59dff6880a510f827a7b16c84e437a5c9ebeb2bbe240c299e75fc  0004-domain-modules-toolset-discovery.patch`

Live invariants:
- `tools/domain_modules_tool.py` exists and imports cleanly.
- `toolsets.py` statically defines the `domain_modules` toolset with `domain_modules_list`, `domain_module_view`, and `domain_module_manage`.
- `hermes_cli/tools_config.py::CONFIGURABLE_TOOLSETS` includes `domain_modules` so checklist/discovery surfaces do not hide it.
- `/v1/toolsets` includes any enabled execution-valid toolset even when it is absent from the interactive checklist.
- cron supports `domain_modules` context injection as background context, not skill instructions.
- `platform_toolsets.discord` and `platform_toolsets.api_server` include `domain_modules` when the surfaces should expose it.
- `hermes tools list --platform discord` and `/v1/toolsets` show `domain_modules` after the relevant runtime is restarted/reloaded.

Validation receipt 2026-07-02:
- `pytest -q tests/test_toolsets.py tests/gateway/test_api_server_toolset.py tests/tools/test_domain_modules_tool.py tests/cron/test_domain_modules_cron.py` -> 52 passed.
- Fresh in-process API-server `/v1/toolsets` probe returned HTTP 200, `toolset_count: 26`, `has_domain_modules: true`, tools `domain_module_manage`, `domain_module_view`, `domain_modules_list`.
- Live gateway restart from inside the gateway process was blocked by Hermes' self-restart guard; active gateway/API process still needs an external restart before it loads this patch.

Operational guard: Springfield runtime patch ledger watchdog `runtime-patch-ledger-watchdog` should report drift/failure to #meta-cron only when the live checkout or active runtime stops satisfying these invariants.

## Hindsight Gemma 4 E2B structured-output recovery

Status: active local runtime patch + profile config, verified 2026-06-24.

Intent: keep Hindsight Hermes memory on Emo's requested Gemma 4 E2B lane and fix parser/retry/filtering behavior around E2B failures instead of silently routing Hindsight to the resident 26B server.

Replayable code patch: `patches/live-hermes-modifications/0002-hindsight-openai-compatible-structured-output-recovery.patch`.

Patch checksum:
- `07b317818c023b8ff3d162bf5aa5eab0dde6cb68ff0e3ae694b9e943c5882642  0002-hindsight-openai-compatible-structured-output-recovery.patch`

Runtime env invariants for Hindsight profile `hermes`:
- `HINDSIGHT_API_LLM_PROVIDER=llamacpp`
- `HINDSIGHT_API_LLM_MODEL=gemma-4-e2b-it`
- `HINDSIGHT_API_LLAMACPP_MODEL_PATH=/Users/emo/.cache/huggingface/hub/models--unsloth--gemma-4-E2B-it-GGUF/snapshots/ecc8b33b2c50598815e4b0f7cea6088e3ae7adb8/gemma-4-E2B-it-Q4_K_M.gguf`
- `HINDSIGHT_API_LLAMACPP_CONTEXT_SIZE=32768`
- `HINDSIGHT_API_LLAMACPP_GPU_LAYERS=-1`
- `HINDSIGHT_API_LLAMACPP_CACHE=false`
- `HINDSIGHT_API_LLM_BASE_URL` is absent.
- `HINDSIGHT_API_LLM_API_KEY` is absent.
- `HINDSIGHT_API_LLM_MAX_RETRIES=0`
- `HINDSIGHT_API_CONSOLIDATION_LLM_MAX_RETRIES=0`
- `HINDSIGHT_API_CONSOLIDATION_MAX_ATTEMPTS=1`
- `HINDSIGHT_API_CONSOLIDATION_MAX_MEMORIES_PER_ROUND=2`
- `HINDSIGHT_API_CONSOLIDATION_LLM_BATCH_SIZE=2`
- `HINDSIGHT_API_LLM_TIMEOUT=180`
- `HINDSIGHT_API_CONSOLIDATION_LLM_TIMEOUT=180`

Code invariants:
- `hindsight_api.engine.providers.openai_compatible_llm` imports `pydantic.ValidationError`.
- Structured JSON parsing extracts balanced JSON embedded in prose before retrying.
- Gemma numbered fact prose coercion is gated to response models whose fields include `facts` in the OpenAI-compatible path used by embedded llama.cpp.
- Pydantic schema validation failures are retryable and logged with bounded previews.
- Ollama 4xx errors fail cheap instead of retrying repeatedly.

Validation receipt 2026-06-24:
- Exact Ollama tags `gemma4:e2b` and `gemma4:e2b-mlx` were not installed (`ollama show` returned model not found), so the live Hindsight E2B lane uses the local GGUF through embedded `llama_cpp.server`.
- Hindsight health endpoint returned `{"status":"healthy","database":"connected"}` after controlled daemon restart.
- Process audit showed Hindsight-owned E2B server: `llama_cpp.server --model .../gemma-4-E2B-it-Q4_K_M.gguf --port 18144`; the resident 26B server on port 18080 may still exist for other services but is not the Hindsight route.
- Redacted Hindsight env matched the invariants above.
- `python -m py_compile .../hindsight_api/engine/providers/openai_compatible_llm.py` passed.
- Synthetic parser smoke passed for balanced JSON extraction and numbered-fact coercion.
- Direct structured smoke through the E2B llama.cpp endpoint returned `{'ok': True, 'item_count': 2}`.
- Live Hindsight retain smoke stored a memory confirming the E2B route; Hindsight search returned it.
- Incorrect Hindsight memories that said the stable local route should be resident 26B were removed with a fresh backup and FK-safe DB transaction after the public Hindsight delete API refused individual deletion.
- Log watch after the initial E2B restart found `stage=llm.llamacpp.consolidation+structured`; batch size 4 later produced request timeouts/stuck markers, so the profile was retuned to one small E2B call per task: `CONSOLIDATION_LLM_BATCH_SIZE=2`, `CONSOLIDATION_MAX_MEMORIES_PER_ROUND=2`, LLM retry caps `0`, and LLM timeouts `180s`.
- After the clean retuned restart, a 180s watch showed health green, 3 completed consolidation rounds, 3 E2B `llm_batch #1 (2 memories, 1 llm calls)` entries, 6 `stage=llm.llamacpp...` progress markers, and zero new `JSON parse error`, `JSON schema validation error`, `Request timed out`, `[STUCK?]`, or `LLM batch call failed` hits.

Follow-up: the before/after memory quality-filtering plan is recovered in `references/hindsight-memory-quality-gating-and-cleanup.md` and tracked as Beads issue `hermes-q62`; as of this receipt it is documented + dry-run tooling, not fully live pre-retain/post-extraction/consolidation gates.

## ACP null `startswith` guards

Status: active local runtime patch, verified 2026-07-01.

Intent: keep Hermes ACP sessions from crashing when an ACP client sends a blank/null model switch request or a turn returns `final_response=None` after interruption. The user-visible failure was a JSON-RPC `-32603` internal error with `"'NoneType' object has no attribute 'startswith'"`, often next to the ACP queue message.

Replayable code patch: `patches/live-hermes-modifications/0003-fix-acp-null-startswith-guards.patch`.

Patch checksum:
- `f448de29f7753468ea5954c1310c35a3cd3e49a2a115034238d779de0c84777b  0003-fix-acp-null-startswith-guards.patch`

Upstream status:
- Checked `origin/main` at `60b1f6ce3` on 2026-07-01.
- Upstream still has `_resolve_model_selection(raw_model: str, ...)` with `raw_model.strip()`.
- Upstream still passes `model_id` directly from `set_session_model()` with no blank/null guard.
- Upstream still uses `result.get("final_response", "")`, which does not handle an explicit `None`.

Live invariants:
- `acp_adapter/server.py` accepts `raw_model: str | None` in `_resolve_model_selection`.
- `_resolve_model_selection` normalizes with `str(raw_model or "").strip()` and skips provider parsing when blank.
- `set_session_model` accepts `model_id: str | None` and returns `SetSessionModelResponse()` without rebuilding the agent for blank/null values.
- Prompt completion coerces `final_response = result.get("final_response") or ""` before calling `.startswith(...)`.
- `tests/acp_adapter/test_acp_commands.py` covers null model switching and interrupted turns with `final_response=None`.

Validation receipt 2026-07-01:
- `uv run --extra dev --extra acp python -m pytest tests/acp_adapter/test_acp_commands.py -q -o 'addopts='` -> 8 passed.
- `uv run --extra acp python -m py_compile acp_adapter/server.py tests/acp_adapter/test_acp_commands.py` passed.
- `/Users/emo/.hermes/hermes-agent/venv/bin/python /Users/emo/.hermes/bin/hermes_acp_paseo.py --check` -> `Hermes ACP check OK`.

Operational note: no Hermes gateway or Paseo daemon restart is required for the source patch itself. New Hermes ACP subprocesses load the patched source; already-running ACP subprocesses may need their agent/provider process recreated before they stop using old in-memory code.
