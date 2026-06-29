# MDL Copilot — Sparse Patch Write + Read-Truncation Fix (A+B) — Implementation Plan

**Status:** SHIPPED (Phases 0–5, 7) · Phase 6 deferred (B2 read_mdl_model, v2 remove,
telemetry, prompt caching) · **Created:** 2026-06-29

**Completion log (2026-06-29):** merge engine lifted to `semantic_layer/mdl_merge.py`
(enrichment path re-imports via aliases, 40 enrichment tests green); `patch_mdl_file`
tool added (sparse name-keyed overlay → shared merge → `_stage_content`; provenance
verb `write`; D8 appended-name note; **plus** a structural-no-op guard surfacing
overlay column type/expression edits the additive merge drops); read-truncation fix
in `loop.py` with `wren_copilot_tool_result_max_chars` config knob (reads/validation
exempt); prompt + both skills reframed patch-first. Tests: `test_mdl_merge.py` (8),
patch tests in `test_copilot_tools.py` (9), B1 truncation tests in
`test_copilot_loop.py` (2). Full suite **1031 passed, 11 skipped**. No frontend
change needed — UI absorbs patch via D4 (`write` verb) + R6 (identical changeset).

A resumable, source-backed checklist. Each phase has a status box, explicit
blockers/dependencies, exact file:line touchpoints, and acceptance criteria.
Update the status boxes as you go so a future session can resume mid-flight.

---

## 0. Goal & scope

Today the copilot's only content-mutation primitive is `write_mdl_file`, a
**full-content overwrite** ([copilot/tools.py:547-577](semantic_layer/copilot/tools.py)),
and the system prompt explicitly tells the model to *"re-emit the **whole** file
each time"* ([prompts/mdl_copilot.md:41-43](prompts/mdl_copilot.md)). This burns
**output tokens** (the expensive, latency-bound kind — 4–8× input cost,
generated serially: <https://warehows.ai/blog/why-output-tokens-cost-more-than-input-tokens>)
and causes silent-omission ("lazy coding") of `properties`, which the code
already fights with a defensive guard and ~40 lines of prompt.

**A — `patch_mdl_file`:** a sparse, **name-keyed overlay** write. The model emits
only the changed entities/columns; the server merges them into the working copy
with the **existing, tested** structure-preserving merge engine. Output drops
~5–20× on enrichment sweeps with no change to the human review artifact.

**B — read-truncation fix:** every tool result (incl. `read_mdl_file`) is cut to
4000 chars before returning to the model ([copilot/loop.py:46](semantic_layer/copilot/loop.py),
[copilot/loop.py:196](semantic_layer/copilot/loop.py)). A model file >~4 KB is
truncated on read while the agent is told to reproduce it whole — a correctness
hazard. Fix: per-tool result limits so reads/validation are not silently cut.

**Out of scope (deferred, tracked in Phase 6):** removal-via-patch (v2), a
per-model `read_mdl_model` tool (B2), prompt caching (input lever), token
telemetry. `write_mdl_file` stays as the create/restructure/remove escape hatch.

### Why this approach (vs. the alternatives we rejected)

- **Not unified-diff / V4A** ([aider](https://aider.chat/docs/unified-diffs.html),
  [OpenAI apply_patch](https://developers.openai.com/api/docs/guides/tools-apply-patch)):
  strict diff grammars are fragile on the small default models
  (`qwen2.5-coder:7b`, `gpt-4.1-mini` — [config.py:67-77](config.py)). Aider's own
  data: weaker models do far worse on diffs; whole-file is "more stable" multi-turn.
- **Not JSON Patch / Merge Patch verbatim** ([RFC 6902 vs 7386](https://zuplo.com/learning-center/json-patch-vs-json-merge-patch)):
  Merge Patch can't target array elements by identity; JSON Patch targets arrays
  by **index** (brittle for an LLM). MDL arrays are **name-keyed**, so a
  name-keyed merge is the correct primitive — and it already exists.
- **Not a Morph/Cursor fast-apply *model*** ([Cursor](https://cursor.com/blog/instant-apply),
  [Morph](https://www.morphllm.com/fast-apply-model)): the fast-apply insight is
  "let the smart model emit only the decision; materialize deterministically." Our
  name-keyed merge **is** that deterministic apply step — no second model needed.

---

## 1. Requirements

### Functional
- **R1** `patch_mdl_file(path, overlay, summary?)` merges a **partial** MDL doc
  (only changed entities/columns, keyed by `name`) onto the file's **current
  working-copy** content, then validates and stages it — same staging + validation
  contract as `write_mdl_file`.
- **R2** Merge is **additive & structure-preserving**: omitted entities/columns
  are kept; colliding entities merge by `name` (models merge column-level); new
  named entities append; existing order is preserved.
- **R3** `properties` (displayName/alias/synonyms + catalog provenance) are
  preserved **by construction** (merge never drops them) — the
  `_preserve_superset_properties` guard becomes belt-and-suspenders, not load-bearing.
- **R4** Patch on a **non-existent file** is an error that points to
  `write_mdl_file` (patch refines; it does not create). See **D7**.
- **R5** `write_mdl_file` is unchanged and remains the path for create, restructure
  (move a model between files), and **removal** (re-emit without the entity).
- **R6** The reviewable `Changeset` is **byte-for-byte identical** whether the edit
  was produced by `write_mdl_file` or `patch_mdl_file` — `build_changeset`
  ([tools.py:1082-1137](semantic_layer/copilot/tools.py)) still diffs full
  `current_content` vs `proposed_content`. Output savings are purely on the
  model→tool path; the human sees the same full diff.
- **R7** Provenance ledger records the patch (verb `write`, tool `patch_mdl_file`).
- **R8 (B1)** `read_mdl_file`, `read_document`, and `validate_project` results are
  **not** truncated at 4000 chars; reads return enough for the agent to decide a
  correct patch.

### Non-functional
- **R9** No new reverse-layer dependency. The merge engine lives in
  `semantic_layer/` (which `integrations.wren.llm_client` already imports from);
  `semantic_layer` must **not** start importing from `integrations.wren`. (Verified:
  `integrations.wren.llm_client` → `semantic_layer` is the existing direction;
  `semantic_layer` → `integrations.wren` does not exist today.)
- **R10** Behavior-preserving refactor: the enrichment apply path
  ([llm_client.py:547](integrations/wren/llm_client.py)) keeps identical behavior;
  its tests (`test_llm_wren_client.py`) stay green.
- **R11** Weak-model robustness: lenient overlay parsing (accept JSON string or
  dict); malformed input returns a clear, correctable error the loop feeds back
  (same self-correction path as today).

---

## 2. Decision points (with recommendations)

| # | Decision | Recommendation | Rationale |
|---|----------|----------------|-----------|
| **D1** | `overlay` as JSON **string** vs structured object arg | **JSON string, dict-tolerant** | Matches `write_mdl_file`'s `content` string ([tools.py:193-196](semantic_layer/copilot/tools.py)); deeply-nested object schemas are unreliable on weak models. Parse leniently like [`_coerce_arguments`](llm/base.py). |
| **D2** | Support **removal** in v1? | **No — defer to v2** | Merge is additive; removal semantics (null-as-delete vs explicit op) are where merge-patch designs get subtle. `write_mdl_file` already removes by re-emit. Revisit if telemetry shows agents needing it. |
| **D3** | Lift merge to shared module vs import from `integrations.wren` | **Lift to `semantic_layer/mdl_merge.py`** | Correct layer + dedups two merge impls. Importing from `integrations.wren` would create a `semantic_layer → integrations.wren` reverse edge and risk a cycle (R9). |
| **D4** | New provenance verb `patch` vs reuse `write` | **Reuse `write`** | Avoids touching the persisted `ToolActionKind` enum ([copilot/schemas.py:57](semantic_layer/copilot/schemas.py)) + any frontend that renders it. `tool` field still says `patch_mdl_file`. Reversible. |
| **D5** | Ship `read_mdl_model` (B2) now? | **Defer** | B1 (untruncated reads) + patch already capture the win. Add B2 only if input tokens stay high on large files. |
| **D6** | Truncation fix: raise global vs per-tool map vs config | **Per-tool map + a config default** | Targeted: never truncate content the model must reproduce; keep a cap on `get_physical_schema`/`find_tables`. |
| **D7** | `patch_mdl_file` on missing file | **Error → point to `write_mdl_file`** | Keeps create vs refine semantics clean; avoids accidental file creation from a typo'd path. |
| **D8** | Typo'd overlay entity name (no base match) | **Append + return a `note`** listing matched-vs-appended names | Transparent; downstream `validate_project` (`strict_models=True`) catches a structurally-invalid appended model. |

---

## 3. Touchpoints (verified file:line)

**New files**
- `superset_ai_agent/semantic_layer/mdl_merge.py` — lifted merge engine (Phase 1)
- `tests/unit_tests/superset_ai_agent/test_mdl_merge.py` — merge unit tests (Phase 5)

**Edit — `semantic_layer/integrations/wren/llm_client.py`** (move-out, re-import)
- Merge fns to MOVE: `_merge_column_preserving_structure` (714), `_merge_model_preserving_structure` (737), `_merge_cube_preserving_structure` (771), `_merge_columns_preserving_structure` (829), `_merge_named` (864), `_merge_manifest_sections` (1037); constants `_MERGE_SECTIONS` (699), `_COLUMN_SEMANTIC_FIELDS` (711), `_CUBE_ENTITY_SECTIONS` (768), type alias `_MergeEntry` (861).
- Callers to keep working: 547, 760, 789, 985, 1062-1065. Replace local defs with `from superset_ai_agent.semantic_layer.mdl_merge import (...)` (re-export under the same private names, or update call sites to public names — see Phase 1).

**Edit — `semantic_layer/copilot/tools.py`**
- `specs()` add `patch_mdl_file` spec after `write_mdl_file` (~204).
- `dispatch()` handler map add `"patch_mdl_file": self._patch_mdl_file` (457-473).
- Extract `_stage_content(path, content, summary)` from `_write_mdl_file` (547-577); both `_write_mdl_file` and new `_patch_mdl_file` call it.
- `_MUTATING_ACTIONS` add `"patch_mdl_file": "write"` (1160-1166).
- `_summarize_mutation` include `patch_mdl_file` in the `("write_mdl_file","delete_mdl_file")` path branch (1195-1198).
- Keep `_preserve_superset_properties`/`_restore_*` (1299-1338) for the write path.

**Edit — `semantic_layer/copilot/loop.py`** (B1)
- `_truncate` (46-47) and its sole call site (196): introduce a per-tool result-limit map; reads/validation uncapped (or high cap). Default from config (D6).

**Edit — `config.py`** (B1)
- Add `wren_copilot_tool_result_max_chars: int = 4000` near the MDL Copilot block (197-225) + env wiring in `from_env`.

**Edit — prompts/skills** (Phase 4)
- `prompts/mdl_copilot.md`: tools list (18-29), "How you work" step 2 (35-47, esp. 41-43), "Carry properties forward" (72-90).
- `skills/generate-mdl.md`: tool table (48-49), "Editing means re-emitting" (57-58), properties notes (267-268, 303).
- `skills/enrich-context.md`: "Only add… re-emit" (36-40), "always re-emit the full preserved" (288).

**No change needed (verify only)**
- `build_inspector` enumerates tools via `MdlToolset([]).specs()` ([service.py:391-415](semantic_layer/copilot/service.py)) → new tool auto-appears.
- `build_changeset` / apply path unchanged (R6).

---

## 4. Sequential checklist

> Legend: `[ ]` todo · `[~]` in progress · `[x]` done. **BLOCKER**/**DEP** call out ordering.

### Phase 0 — Prereqs & baseline (no code) — **no deps**
- [ ] **0.1** Run the copilot test suite to capture a green baseline:
  `pytest tests/unit_tests/superset_ai_agent/test_copilot_tools.py tests/unit_tests/superset_ai_agent/test_llm_wren_client.py -q`
- [ ] **0.2** Re-confirm layering (R9): `grep -rn "from superset_ai_agent.integrations.wren" superset_ai_agent/semantic_layer --include='*.py'` returns **nothing**.
- [ ] **0.3** Skim `test_llm_wren_client.py` for tests that import the `_merge_*` names directly (they may need an import-path update in Phase 1).

### Phase 1 — Shared merge module — **DEP: 0.x** · **BLOCKS: Phase 2, Phase 5**
- [ ] **1.1** Create `semantic_layer/mdl_merge.py` with ASF header; move the merge fns + constants listed in §3. Promote to **public** names (drop leading `_`): `merge_manifest_sections`, `merge_named`, `merge_model_preserving_structure`, etc. Keep docstrings (they encode E4/H5.1 invariants — do not lose them).
- [ ] **1.2** In `integrations/wren/llm_client.py`, delete the moved defs and add `from superset_ai_agent.semantic_layer.mdl_merge import (...)`. Either alias to old private names (`merge_manifest_sections as _merge_manifest_sections`) for a minimal diff, or update the 6 call sites (547, 760, 789, 985, 1062-1065). **Recommend aliasing** to keep this phase behavior-only.
- [ ] **1.3** `pytest tests/unit_tests/superset_ai_agent/test_llm_wren_client.py -q` — must stay green (R10). **BLOCKER if red:** the move changed behavior; diff carefully.
- [ ] **1.4** `pre-commit run mypy --files superset_ai_agent/semantic_layer/mdl_merge.py superset_ai_agent/integrations/wren/llm_client.py`.

### Phase 2 — `patch_mdl_file` tool — **DEP: Phase 1**
- [ ] **2.1** Refactor `_write_mdl_file` (547-577): extract `_stage_content(self, path, content, summary) -> dict` doing the `_preserve_superset_properties` guard + `self._working[path] = content` + summary + `validate_mdl(..., strict_models=True)` + result dict. `_write_mdl_file` becomes: validate non-empty string → `_stage_content`.
- [ ] **2.2** Add `_patch_mdl_file(self, args)`:
  1. `path = normalize_mdl_path(require_path)`; if `path not in self._working` → error per **D7/R4**.
  2. Parse `overlay` leniently (dict or JSON string per **D1**); empty/invalid → error (**R11**).
  3. `base = json.loads(self._working[path])`; `merged = merge_manifest_sections(base, overlay)`.
  4. Compute matched-vs-appended entity names for the **D8** `note`.
  5. `result = self._stage_content(path, json.dumps(merged, indent=2), summary)`; attach `note`; return.
- [ ] **2.3** Register the `ToolSpec` in `specs()` after `write_mdl_file`. Description must steer usage: *"Refine an existing MDL file by merging a **partial** overlay (only the entities/columns you change, keyed by name). Preferred for edits — emit just the change, not the whole file. Use write_mdl_file only to create a file or restructure/remove."* Params: `path` (existing path_param), `overlay` (string; "Partial MDL JSON: only changed models/columns, keyed by name"), `summary` (string).
- [ ] **2.4** Add `"patch_mdl_file": self._patch_mdl_file` to the `dispatch()` map.
- [ ] **2.5** Add `"patch_mdl_file": "write"` to `_MUTATING_ACTIONS` (**D4**) and include it in the `_summarize_mutation` write/delete branch (reads `result["path"]`).
- [ ] **2.6** `pre-commit run mypy --files superset_ai_agent/semantic_layer/copilot/tools.py`.

### Phase 3 — Read-truncation fix (B1) — **no deps** (parallel with 1–2)
- [ ] **3.1** `config.py`: add `wren_copilot_tool_result_max_chars: int = 4000` + `from_env` wiring (env e.g. `WREN_COPILOT_TOOL_RESULT_MAX_CHARS`).
- [ ] **3.2** `loop.py`: thread the configured default in; build a per-tool override map — `read_mdl_file`, `read_document`, `validate_project` → no truncation (or very high); everything else → default. Apply at the call site (196) keyed by `call.name`.
- [ ] **3.3** Note the latent bug being fixed: `read_document` already caps to 100k internally ([tools.py:75](semantic_layer/copilot/tools.py)) but loop.py re-cut it to 4000 — so the agent never saw >4 KB of a doc. Confirm the fix restores the intended `max_chars`.
- [ ] **3.4** `pre-commit run mypy --files superset_ai_agent/semantic_layer/copilot/loop.py superset_ai_agent/config.py`.

### Phase 4 — Prompt + skills — **DEP: Phase 2** (tool must be named/registered)
- [ ] **4.1** `prompts/mdl_copilot.md`: add `patch_mdl_file` to "Your tools"; rewrite step 2 — *default to `patch_mdl_file` (emit only the changed subtree); use `write_mdl_file` only to create or restructure*. Reframe "Carry properties forward" as **automatic under patch** (merge preserves omitted keys); keep the warning scoped to `write_mdl_file`. (Net prompt **shrinks** → small input win.)
- [ ] **4.2** `skills/generate-mdl.md`: add patch to tool table; replace "Editing means re-emitting the full file" (57-58) with the patch-first guidance.
- [ ] **4.3** `skills/enrich-context.md` (the biggest beneficiary): replace "re-emit the full preserved object" (36-40, 288) with "emit a sparse overlay via `patch_mdl_file`; the merge preserves everything you omit."
- [ ] **4.4** Keep one explicit sentence in each: *removal/restructuring still requires `write_mdl_file`* (covers the deferred **D2**).

### Phase 5 — Tests — **DEP: Phases 1–3**
- [ ] **5.1** `test_mdl_merge.py`: additive merge keeps omitted columns/models; column-level merge preserves `type`/mapping; `properties` additively merged; new entity appends; order preserved; cube entry-level (H5.1). (Port/relocate any enrichment tests that exercised the private fns.)
- [ ] **5.2** `test_copilot_tools.py` (follow file conventions — `MdlToolset`, `SchemaIndex.from_snapshot`, `MdlFile`):
  - patch adds a column description without dropping siblings;
  - patch preserves `properties` the overlay omits (R3);
  - patch **composes on the working copy** (write then patch in one toolset);
  - patch on missing path → error mentioning `write_mdl_file` (R4/D7);
  - malformed overlay → error (R11);
  - typo'd name appends + returns `note` (D8);
  - **R6 invariant:** a column added via `patch_mdl_file` yields the *same* `proposed_content` as the equivalent `write_mdl_file`.
- [ ] **5.3** Loop truncation test: a `read_mdl_file` on a >4 KB file returns full content to the model (B1/R8).
- [ ] **5.4** Full suite: `pytest tests/unit_tests/superset_ai_agent/ -q`.

### Phase 6 — Deferred follow-ups (do NOT block A+B)
- [ ] **6.1 (B2)** `read_mdl_model(path, name)` targeted read — only if input tokens stay high (D5).
- [ ] **6.2 (v2)** Removal-via-patch: explicit name-keyed `remove:[{section,name,column?}]`, applied post-merge (D2).
- [ ] **6.3** Token telemetry: log prompt vs completion tokens per turn to quantify the output win on real projects.
- [ ] **6.4** Prompt caching of the static prefix (system prompt + specs + `get_physical_schema`) — the input lever.

### Phase 7 — Ship
- [ ] **7.1** `git add -A && pre-commit run --all-files` (CLAUDE.md mandate). Fix mypy/ruff/black/prettier.
- [ ] **7.2** Manual smoke (if env up): an "add synonyms to X" turn issues `patch_mdl_file`, not `write_mdl_file`; inspector lists the new tool; changeset diff looks identical to the whole-file equivalent.
- [ ] **7.3** Commit (`feat(copilot): sparse name-keyed patch_mdl_file + read-truncation fix`). Update `UPDATING.md` only if a user-facing flag/behavior changed (the config knob is operator-facing → note it).

---

## 5. Risks & mitigations

| Risk | Mitigation |
|------|-----------|
| Merge appends a typo'd/wrong-named entity silently | **D8**: return a `note` of matched-vs-appended names; `validate_project` (`strict_models=True`, [tools.py:1069-1080](semantic_layer/copilot/tools.py)) rejects a structurally-invalid appended model. |
| Agent tries to remove via patch (unsupported v1) | Prompt + skills state removal = `write_mdl_file` (Phase 4.4); merge is documented additive. |
| Weak model emits malformed overlay JSON | Lenient parse (string/dict); clear error fed back through the existing correction loop (R11). |
| Refactor breaks enrichment path | Pure move + alias re-exports (1.2); gated by `test_llm_wren_client.py` (1.3). **BLOCKER if red.** |
| Untruncated reads blow up context on huge schemas | Only reads/validation uncapped; `get_physical_schema`/`find_tables` keep a cap; B2 (6.1) is the pressure valve. |
| `_preserve_superset_properties` vs merge double-handling | Patch routes merged (already-preserving) content through the same `_stage_content`; the guard is a harmless no-op for patch, still active for write. |
| Inspector/provenance/frontend drift | New tool auto-surfaces via `specs()`; provenance reuses `write` verb (D4) → no enum/frontend change. |

---

## 6. Sources

- Output tokens cost 4–8× input, generated serially — <https://warehows.ai/blog/why-output-tokens-cost-more-than-input-tokens>
- Whole-file vs diff token/laziness trade-offs (weak models worse on diffs; whole-file more stable) — <https://aider.chat/docs/more/edit-formats.html>, <https://aider.chat/docs/unified-diffs.html>
- Anchored str_replace edit tool — <https://docs.anthropic.com/en/docs/agents-and-tools/tool-use/text-editor-tool>
- OpenAI apply_patch / V4A context-anchored diff — <https://developers.openai.com/api/docs/guides/tools-apply-patch>
- Fast-apply = plan-then-materialize (our merge is the deterministic apply) — <https://cursor.com/blog/instant-apply>, <https://www.morphllm.com/fast-apply-model>
- JSON Patch (index-based) vs Merge Patch (no array-by-identity) → name-keyed merge — <https://zuplo.com/learning-center/json-patch-vs-json-merge-patch>
