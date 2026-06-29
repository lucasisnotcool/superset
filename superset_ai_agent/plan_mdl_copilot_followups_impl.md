# MDL Copilot — Follow-ups: Token Telemetry · Targeted Read · Removal · Input Levers — Implementation Plan

**Status:** Item D (Phase 6) SHIPPED 2026-06-29 · Items C/E/B2 (Phases 1–5) DEFERRED ·
**Predecessor:** `plan_mdl_copilot_patch_tools_impl.md` (A+B, SHIPPED)

**Item D completion log (2026-06-29):** `remove_mdl_entity` tool added — name-keyed
removal of a model/relationship/metric/view/**calculated** column via new pure
helpers `remove_named`/`remove_manifest_entities` in `mdl_merge.py`; physical-column
removal refused (DC-D3); empties-file → file deleted (DC-D4); per-item rejects/missing
reported. New `remove` `ToolActionKind` (backend `copilot/schemas.py` + frontend
`api.ts` + `MdlProvenanceDialog.tsx` ACTION_VERBS/ORDER → "Removed N entit(ies)");
backend `action_summary` already generic so it counts `remove` with no change. Prompt
+ generate-mdl skill updated. Tests: 5 merge + 8 tool + 1 FE rollup; my suites green
(backend 74, FE 14). **Items C (telemetry), E (input levers), B2 (read_mdl_model)
remain DEFERRED — see Phases 1–5 below; build C first as the measurement gate.**

A resumable, source-backed checklist for the items deferred from the A+B work
(Phase 6 there). Each phase has a status box, explicit blockers/dependencies,
exact file:line touchpoints, requirements, risks, and decision points. Update the
status boxes as you go so a future session can resume mid-flight.

---

## 0. Goal & scope

A+B shipped `patch_mdl_file` (sparse name-keyed overlay) + a read-truncation fix to
cut **output tokens** on MDL edits. Four follow-ups remain, in priority order:

- **C — Token telemetry (do FIRST).** There is **no** token accounting in the agent
  today (verified: only stray mentions in `wren_upstream_skills/`). Without it, the
  A+B output-reduction win is unmeasured and B2/E2 below are un-gated guesses.
  Instrument first — this is the standard "measure before you optimize" discipline
  (e.g. [Token-usage prompting-strategy evaluation, arXiv 2505.14880](https://arxiv.org/pdf/2505.14880)).
- **E — Input-token levers (rides on C).** Surface OpenAI's `cached_tokens`
  (auto-cache visibility) and, if telemetry warrants, trim superseded tool results
  re-sent within a turn. Provider prompt-caching is otherwise automatic on OpenAI
  ([OpenAI prompt caching](https://platform.openai.com/docs/guides/prompt-caching))
  and unavailable to wire explicitly (no Anthropic client exists).
- **B2 — `read_mdl_model` (gated on C).** A per-entity read so the agent pulls one
  model instead of a whole file — shrinks **input**. Build only if telemetry shows
  input pressure on large files.
- **D — Name-keyed removal (product-driven, independent).** The additive merge
  can't remove; today removal goes through `write_mdl_file` (full re-emit — the
  expensive path A+B set out to kill). A `remove_mdl_entity` tool makes removal
  cheap too. This is the **one** item that needs a (trivial) frontend touch.

**Out of scope:** explicit Anthropic `cache_control` (no such client; revisit if one
lands), a fast-apply model (the deterministic merge already is the apply step).

### Why output came first, input now (source-backed)

Output tokens cost 4–8× input and are generated serially, so A+B (output) was the
higher-leverage first cut ([warehows](https://warehows.ai/blog/why-output-tokens-cost-more-than-input-tokens)).
Input is reclaimed differently: provider **prompt caching** discounts the repeated
static prefix ([OpenAI](https://platform.openai.com/docs/guides/prompt-caching),
[Anthropic prompt caching](https://docs.anthropic.com/en/docs/build-with-claude/prompt-caching)),
and **context trimming** removes dead weight the loop re-sends each step. Telemetry
(C) tells us which of those, if any, is worth the code.

---

## 1. Requirements

### Functional
- **R1 (C)** Each Copilot turn records summed `prompt_tokens`, `completion_tokens`,
  `cached_prompt_tokens`, `total_tokens` across every `model_client.chat` call in the
  loop (tool-calling turns + the finalize call), best-effort and cross-provider.
- **R2 (C)** Usage is carried on the returned `Changeset` so it flows to the SSE
  `complete` event and into `evaluation/eval_v2.py` with no extra plumbing.
- **R3 (C)** Telemetry never breaks a turn: a provider that omits usage yields
  `None`, not an error.
- **R4 (E3)** OpenAI's `usage.prompt_tokens_details.cached_tokens` is surfaced as
  `cached_prompt_tokens` so cache hit-rate is visible.
- **R5 (B2)** `read_mdl_model(name, path?)` returns one named entity's JSON (across
  models/relationships/metrics/views) from the working set, plus the file it lives
  in; read-only, not a mutation.
- **R6 (D)** `remove_mdl_entity` removes a named model / relationship / metric / view
  / **calculated** column from a file, validates the result, and stages it as a
  reviewable changeset item — never a physical column (hard contract).
- **R7 (D)** Removal that empties a file deletes the file (changeset `delete`),
  otherwise it is an `update`; either way the changeset diff is full-content (parity
  with A+B R6).
- **R8 (E2, optional)** Within a turn, a tool result superseded by a later edit to
  the same file may be replaced in history by a short pointer to cut re-sent input.

### Non-functional
- **R9** No new reverse-layer dependency: removal helpers live in
  `semantic_layer/mdl_merge.py` (same module as the merge); the usage extractor lives
  in `llm/base.py` (provider-neutral, beside `message_to_openai`).
- **R10** Telemetry is additive and provider-neutral: extend `ModelResult` handling,
  do not change any client's wire call.
- **R11 (D)** The new `remove` provenance verb is the only frontend-visible change;
  it mirrors the existing four verbs exactly.

---

## 2. Decision points (with recommendations)

| # | Decision | Recommendation | Rationale |
|---|----------|----------------|-----------|
| **DC-C1** | Where to put the usage extractor | **`llm/base.py`** (`extract_usage(raw)`) | Provider-neutral, beside `message_to_openai`/`tools_to_openai`; both OpenAI-style and Ollama raw dumps are parsed there. |
| **DC-C2** | Per-call usage as steps vs aggregate on Changeset | **Aggregate on `Changeset.token_usage`** + one summary `AgentStep` | The Changeset field is durable and flows to eval via SSE; per-call steps are noise. |
| **DC-C3** | Cross-provider key handling | **Small mapper**: OpenAI `usage.{prompt_tokens,completion_tokens,prompt_tokens_details.cached_tokens}`; Ollama `{prompt_eval_count,eval_count}`; unknown → `None` | Best-effort, never raises (R3). Loop uses `stream: False`, so usage is present on OpenAI ([openai_client.py:159](llm/openai_client.py)). |
| **DC-B2-0** | Build `read_mdl_model` now? | **Gate on C** | A+B's B1 (untruncated reads) + patch may already suffice; build only if telemetry shows input pressure on large files. |
| **DC-B2-1** | Lookup by `name` only vs `(path, name)` | **`name` required, `path` optional disambiguator** | Name-first matches the "edit this model" intent; `path` resolves collisions. |
| **DC-B2-2** | Which sections to search | **All named entities** (models/relationships/metrics/views) | The agent may read a metric/relationship to refine it, not just models. |
| **DC-B2-3** | Ambiguous name across files | **Return all matches with their paths** | Transparent; the agent picks the path for the follow-up patch. |
| **DC-D1** | Removal as a new tool vs a `patch_mdl_file` `remove` param | **New tool `remove_mdl_entity`** | Keeps patch's "additive" mental model clean; matches the granular-tool style (`propose_relationships`). |
| **DC-D2** | Removal provenance verb | **New `remove` `ToolActionKind`** (+ frontend label) | Reusing `write` says "Wrote N files" (hides intent); `delete` says "Deleted N **files**" (wrong — no file deleted). `remove` → "Removed N entit(ies)" is correct; the frontend add is one line per the existing 4 verbs. |
| **DC-D3** | Removing a physical (non-calculated) column | **Refuse with an error** citing physical authority | Upholds the hard contract; only `isCalculated` columns, relationships, metrics, views, and whole models are removable. |
| **DC-D4** | Removal empties a file | **Delete the file** (changeset `delete`) | Cleaner than staging an empty manifest (cf. relationships-only-activation empty-root handling). |
| **DC-E1** | Explicit cache_control vs rely on auto-cache | **Rely on OpenAI auto-cache now**; add explicit markers only if an Anthropic-compatible client lands | No Anthropic client exists; OpenAI auto-caches >1024-token prefixes with zero code. |
| **DC-E2** | Within-turn history trimming | **Gate on C/E3**; implement only if re-sent history dominates input | Real, controllable lever, but adds loop complexity; measure first. |

---

## 3. Touchpoints (verified file:line)

**Item C — telemetry**
- `superset_ai_agent/llm/base.py` — add `TokenUsage` model + `extract_usage(raw: dict) -> TokenUsage | None` (beside `message_to_openai`, ~115). Sources: OpenAI/Azure/compatible all set `raw=data` ([openai_client.py:191](llm/openai_client.py), [azure_openai.py:173](llm/azure_openai.py), [openai_compatible.py:162](llm/openai_compatible.py)); Ollama sets `raw=data` with `prompt_eval_count`/`eval_count` ([ollama.py:90-92](llm/ollama.py)).
- `superset_ai_agent/semantic_layer/copilot/schemas.py` — add `token_usage: TokenUsage | None = None` to `Changeset` (~124-147).
- `superset_ai_agent/semantic_layer/copilot/loop.py` — accumulate usage after each `model_client.chat` ([loop.py:160](semantic_layer/copilot/loop.py) and the finalize call ~255); set on the built changeset (`build_changeset` result, ~267). Optional summary `AgentStep` (kind `copilot_usage`).
- `superset_ai_agent/evaluation/eval_v2.py` — read `changeset.get("token_usage")` in the turn result (`changeset_from_events` ~137, run path ~392-470) to report prompt/completion + output ratio.

**Item B2 — read_mdl_model**
- `superset_ai_agent/semantic_layer/copilot/tools.py` — `specs()` add the tool (read-only, after `read_mdl_file` ~178); `dispatch()` map (~457-473); new `_read_mdl_model`; reuse the section-scan from `_working_model_names` (~879) / `_overlay_entity_names`. NOT in `_MUTATING_ACTIONS`.
- `tests/unit_tests/superset_ai_agent/test_copilot_tools.py` — surface test set (~61) + behavior tests.

**Item D — removal**
- `superset_ai_agent/semantic_layer/mdl_merge.py` — add `remove_named(items, names)` + `remove_manifest_entities(base, removals)` (name-keyed filter; column removal within a model).
- `superset_ai_agent/semantic_layer/copilot/tools.py` — `specs()` + `dispatch()` + `_remove_mdl_entity` (routes through `_stage_content`, or deletes the file per DC-D4); `_MUTATING_ACTIONS["remove_mdl_entity"] = "remove"` (~1160); `_summarize_mutation` branch.
- `superset_ai_agent/semantic_layer/copilot/schemas.py:57` — `ToolActionKind = Literal["write", "delete", "onboard", "relate", "remove"]`.
- **Frontend (the only FE change):**
  - `superset-frontend/src/SqlLab/components/AiAgentPanel/api.ts:1920` — `ToolActionKind` union add `'remove'`.
  - `superset-frontend/src/SqlLab/components/AiAgentPanel/SemanticLayerEditor/MdlProvenanceDialog.tsx:183-189` — `ACTION_VERBS` add `remove: n => t('Removed %s entit(ies)', n)`; `ACTION_ORDER` add `'remove'`.
- Prompt/skills: `prompts/mdl_copilot.md`, `skills/generate-mdl.md`, `skills/enrich-context.md` — replace "to remove, rewrite with write_mdl_file" with `remove_mdl_entity` guidance.

**Item E — input levers**
- E3 (cache visibility): folded into `extract_usage` (`cached_prompt_tokens`); no extra site.
- E2 (within-turn trim, optional): `superset_ai_agent/semantic_layer/copilot/loop.py` — after a write/patch to path P, rewrite earlier `role="tool"` read results for P in `messages` to a short pointer. Note: distinct from `wren_copilot_max_history_messages` ([config.py:219](config.py)), which windows *cross-turn* history.

---

## 4. Sequential checklist

> Legend: `[ ]` todo · `[~]` in progress · `[x]` done. **BLOCKER**/**DEP**/**GATE** call out ordering.

### Phase 0 — Prereqs (no code) — **no deps**
- [ ] **0.1** Confirm green baseline: `pytest tests/unit_tests/superset_ai_agent/ -q` (expect 1031+ passed).
- [ ] **0.2** Confirm the copilot loop is non-streamed (usage present): `grep -n '"stream": False' superset_ai_agent/llm/openai_client.py`.

### Phase 1 — Item C: token telemetry — **DEP: 0.x** · **BLOCKS the gate in Phase 3**
- [ ] **1.1** `llm/base.py`: add `TokenUsage(BaseModel)` with `prompt_tokens:int=0`, `completion_tokens:int=0`, `cached_prompt_tokens:int=0`, `total_tokens:int=0`, and an `add()` / `__add__` to accumulate.
- [ ] **1.2** `llm/base.py`: `extract_usage(raw: dict) -> TokenUsage | None` — OpenAI-style first (`usage.prompt_tokens`, `usage.completion_tokens`, `usage.prompt_tokens_details.cached_tokens`, `usage.total_tokens`), else Ollama (`prompt_eval_count`→prompt, `eval_count`→completion), else `None`. Pure, total-functions, never raises (R3).
- [ ] **1.3** `copilot/schemas.py`: add `token_usage: TokenUsage | None = None` to `Changeset`. Import `TokenUsage` from `llm.base`.
- [ ] **1.4** `copilot/loop.py`: keep a running `TokenUsage`; after each `model_client.chat(...)` (main loop + finalize) add `extract_usage(result.raw)`. After `build_changeset`, set `changeset.token_usage = usage` (or `None` if all calls lacked usage). Optionally `emit(AgentStep(kind="copilot_usage", summary=f"{usage.prompt_tokens} in / {usage.completion_tokens} out", status="ok"))`.
- [ ] **1.5** Tests (`tests/unit_tests/superset_ai_agent/test_copilot_loop.py`): `extract_usage` parses OpenAI shape, Ollama shape, and returns `None` on absent; a scripted multi-call turn sums usage onto `changeset.token_usage`; a provider with no usage leaves it `None` and the turn still succeeds.
- [ ] **1.6** `evaluation/eval_v2.py`: read `token_usage` from the turn's changeset and include `prompt/completion/cached/output_ratio` in the per-turn metrics. Add/extend an eval test asserting the field is surfaced.
- [ ] **1.7** `pre-commit run mypy --files` on `llm/base.py copilot/loop.py copilot/schemas.py evaluation/eval_v2.py`; `ruff check`/`ruff format`.

### Phase 2 — Item E3: cache visibility — **DEP: Phase 1** (rides on `extract_usage`)
- [ ] **2.1** Confirm `cached_prompt_tokens` is populated from OpenAI `usage.prompt_tokens_details.cached_tokens` (already in 1.2) and surfaced in eval (1.6).
- [ ] **2.2** Doc note (UPDATING.md or the agent README): OpenAI auto-caches the static prefix (system prompt + tool specs) for prompts >1024 tokens; openai-compatible/Azure depends on the endpoint; this is the input-side complement to A+B. No code beyond telemetry (DC-E1).

### Phase 3 — Decision gate (read telemetry) — **DEP: Phases 1–2** · **GATE for Phases 4 & 5**
- [ ] **3.1** Run the copilot eval (`evaluation/run_eval_v2.py`) and read prompt/completion/cached per turn. Record: (a) output-token drop vs the pre-A+B baseline; (b) whether **input** tokens are dominated by large reads / re-sent history.
- [ ] **3.2** **Decide & log here:** build B2 (Phase 4) only if reads dominate input; build E2 (Phase 5) only if re-sent within-turn history dominates input. If neither, mark 4 & 5 **WON'T DO** with the numbers that justified it (no silent drop — record the measurement).

### Phase 4 — Item B2: `read_mdl_model` — **GATE: 3.2 says reads dominate input**
- [ ] **4.1** `tools.py`: `_read_mdl_model(name, path?)` — scan `self._working` (parse each file) for a named entity across `models`/`relationships`/`metrics`/`views`; return `{matches:[{path, section, entity}]}` (all matches per DC-B2-3). Restrict to `path` when given.
- [ ] **4.2** Register `ToolSpec` (read-only) after `read_mdl_file`; add to `dispatch()` map; do NOT add to `_MUTATING_ACTIONS`.
- [ ] **4.3** Tests: reads one model by name; returns the right section for a metric/relationship (DC-B2-2); multiple files with the same name returns all matches (DC-B2-3); unknown name returns empty matches.
- [ ] **4.4** Prompt nudge (`generate-mdl.md`/`enrich-context.md`): "to refine one entity, `read_mdl_model` it, then `patch_mdl_file` — avoid reading the whole file." mypy/ruff.

### Phase 5 — Item E2: within-turn history trim — **GATE: 3.2 says re-sent history dominates input**
- [ ] **5.1** `loop.py`: after a successful `write_mdl_file`/`patch_mdl_file` to path P, walk earlier `role="tool"` messages whose result was a `read_mdl_file`/`read_mdl_model` of P and replace `content` with a short pointer (e.g. `{"note":"superseded by your edit to <P>"}`). Keep the most recent state authoritative.
- [ ] **5.2** Tests: a read→write→read sequence shrinks the stale read in history; the agent still sees the current content; correctness (final changeset) unchanged.
- [ ] **5.3** mypy/ruff. Re-run eval (3.1) to confirm the input drop and no regression in proposal quality.

### Phase 6 — Item D: name-keyed removal — **independent (no telemetry gate)**
- [ ] **6.1** `mdl_merge.py`: `remove_named(items, names) -> list` (drop by name, preserve order) and `remove_manifest_entities(base, removals) -> dict` where a removal is `{section, name, column?}`; column removal filters a model's `columns` by name. Pure functions; unit-test in `test_mdl_merge.py`.
- [ ] **6.2** `tools.py`: `_remove_mdl_entity(args)` — validate path exists; for each removal enforce DC-D3 (refuse a non-`isCalculated` physical column with a contract-citing error); apply via `remove_manifest_entities`; if the file still has entities → `_stage_content` (re-validates so a dangling metric/relationship reference self-corrects), else delete the working file (DC-D4).
- [ ] **6.3** Register `ToolSpec` + `dispatch()` map; `_MUTATING_ACTIONS["remove_mdl_entity"] = "remove"`; extend `_summarize_mutation` for it.
- [ ] **6.4** `copilot/schemas.py:57`: add `"remove"` to `ToolActionKind`.
- [ ] **6.5** **Frontend (only FE change):** `api.ts:1920` union add `'remove'`; `MdlProvenanceDialog.tsx` `ACTION_VERBS` + `ACTION_ORDER` add `remove`. (No changeset/diff change — removal is a normal `update`/`delete` item, R7.)
- [ ] **6.6** Prompt/skills: replace the "rewrite with write_mdl_file to remove" guidance in `mdl_copilot.md` (Hard contracts §"A join is NEVER a model"), `generate-mdl.md`, `enrich-context.md` with `remove_mdl_entity`.
- [ ] **6.7** Tests: remove a relationship/metric/whole model; refuse a physical column (DC-D3); removing the last entity deletes the file (DC-D4); removing a model still referenced by a relationship surfaces a validation error the loop can correct; surface test set (~61) + provenance verb test.
- [ ] **6.8** Frontend test: `MdlProvenanceDialog.test.tsx` renders "Removed N entit(ies)". mypy/ruff/eslint/prettier.

### Phase 7 — Ship
- [ ] **7.1** `git add -A && pre-commit run --all-files` (CLAUDE.md mandate). Fix mypy/ruff/eslint/prettier.
- [ ] **7.2** Full suites: `pytest tests/unit_tests/superset_ai_agent/ -q`; `npm run test -- MdlProvenanceDialog` (FE) for Phase 6.
- [ ] **7.3** `UPDATING.md`: note the new `wren_copilot` telemetry field (if surfaced in any API) and the `remove_mdl_entity` capability; commit (`feat(copilot): token telemetry + read_mdl_model + remove_mdl_entity`).

---

## 5. Risks & mitigations

| Risk | Mitigation |
|------|-----------|
| Provider omits usage (Ollama variants, proxies) | `extract_usage` returns `None`; `token_usage` is optional; turn never fails (R3). |
| Streamed responses drop usage | Copilot loop is `stream: False` (0.2); if a future path streams, set `stream_options={"include_usage": true}`. |
| Telemetry mis-sums across the finalize call | Accumulate at every `chat()` site including the tool-free finalize (1.4); test the multi-call sum (1.5). |
| B2 read-by-name collisions | Return all matches with paths (DC-B2-3); the follow-up patch carries the disambiguating `path`. |
| Removal breaks a cross-file reference (metric/relationship → removed model) | `_stage_content` re-validates the merged manifest; the loop's correction pass surfaces the error before the user applies. |
| Removal of a physical column violates the contract | Hard refuse non-`isCalculated` columns (DC-D3) with a contract-citing error. |
| Empty-file-after-removal edge | Delete the file (DC-D4); test it (6.7) — avoids an empty-root manifest (cf. relationships-only-activation). |
| New `remove` verb not handled by FE rollup | Add the verb in both places (6.5) + FE test (6.8); the rollup ignores zero-count verbs, so an un-updated FE would silently omit it — the test guards against that. |
| Within-turn trim hides content the agent still needs | Only trim a read **superseded** by a later edit to the same path; keep the latest state authoritative (5.1) + correctness test (5.2). |
| Building B2/E2 without evidence | Phase 3 gate: build only if telemetry shows the matching input pressure; record the numbers either way (3.2). |

---

## 6. Sources

- Measure-before-optimize for token strategies — [arXiv 2505.14880](https://arxiv.org/pdf/2505.14880)
- Output tokens 4–8× input, generated serially (why output came first) — [warehows](https://warehows.ai/blog/why-output-tokens-cost-more-than-input-tokens)
- OpenAI automatic prompt caching (>1024-token prefix, `cached_tokens`) — [OpenAI prompt caching](https://platform.openai.com/docs/guides/prompt-caching)
- Anthropic explicit prompt caching (future, if an Anthropic client lands) — [Anthropic prompt caching](https://docs.anthropic.com/en/docs/build-with-claude/prompt-caching)
- Name-keyed merge vs index-based JSON Patch (removal stays name-keyed, not index) — [JSON Patch vs Merge Patch](https://zuplo.com/learning-center/json-patch-vs-json-merge-patch)
