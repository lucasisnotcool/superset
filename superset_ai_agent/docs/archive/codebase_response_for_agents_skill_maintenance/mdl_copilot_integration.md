# MDL Copilot base-prompt integration report

## 1. Summary
- Evolved `prompts/mdl_copilot.md` in place (did **not** overwrite with Wren's
  CLI-centric `AGENTS.md`): reshaped it to the AGENTS skeleton — intro → how you
  work (tools + loop) → hard contracts → quick reference — while preserving every
  hard rule already present.
- Promoted the **`properties` carry-forward** rule to the canonical base invariant
  and grounded it in the *actual consumers* — `displayName`/`alias`/`synonyms` read
  by `schema_retriever._semantic_terms` and `coverage._column_fact` — and named the
  **seeded catalog-provenance keys** (`superset_dataset_id`, `superset_database_id`,
  `source`, `superset_column_name`, `is_time`) that must also be carried forward.
  This is the headline layering improvement: the one thing all three reports asked
  the base to own now lives in the base, stated positively (correct-from-first-token).
- Removed the base's standalone "Enriching the semantic layer from documents"
  procedure (now fully owned by the `enrich-context` skill); kept only the
  document **tools** + the "ground meaning in a real passage" invariant.
- Tightened the MDL-shape invariant: camelCase, no YAML, **no build/compile step**,
  `type` required, `joinType` UPPERCASE enum — closing the generate-mdl report's
  "don't emit a mixed YAML/cubes signal" recommendation.
- Chose **Option A** (lean always-on base + always-on skills); recorded Option B
  (on-demand `get_skill` tool) as a costed recommendation, not built.

## 2. Base-prompt invariants (what now lives in the base, and why)
Each is true regardless of which skill is active, so it belongs in the base:

- **Physical authority** — never invent/rename/retype a physical table/column;
  structure from `get_physical_schema`. Enforced by validation
  (`mdl_validator.py` `unknown_table`/`unknown_column`/`column_type_mismatch`).
- **MDL shape** — JSON, camelCase keys, `type` on every non-relationship column,
  `joinType` ∈ UPPERCASE enum; we author the final manifest directly (no build
  step) — `mdl_schema.py:18-37,46-53,65-78`.
- **`properties` carry-forward** — re-emit existing `properties` verbatim, add
  never strip. Governance/retrieval keys read at `schema_retriever.py:101-114`
  (`_semantic_terms`) and `coverage.py:159-166` (`_column_fact`); seeded keys
  written at `integrations/wren/mdl_exporter.py:91-93,125-126`. wren-core tolerates
  the omission (`wren_core_validator.py` ignores unknown fields), so validation
  cannot catch the loss — the prompt is the primary mechanism.
- **Validate before finishing** — `validate_project` after edits; the loop also
  feeds errors back for bounded correction (`copilot/loop.py:168-202`).
- **Propose, don't persist** — edits stage in an in-memory working copy and return
  a reviewable `Changeset`; activation is a separate human action
  (`copilot/tools.py:18-25,102-104`; `service.py:apply_changeset_items` lands
  drafts).
- **Onboarded-and-stable lifecycle** — the base assumes the layer is `ready`
  before the Copilot edits; edits are 409-gated until then
  (`app.py:_require_project_ready:1367-1380`, readiness `empty|indexing|ready|failed`).
- **Tool surface** — the nine tools, each with "when to call", from
  `copilot/tools.py:123-230`.

## 3. Base ↔ skills layering map
| Concern | Lives in base | Lives in which skill | Duplicated before? |
|---|---|---|---|
| Identity ("MDL Copilot") + edit-loop discipline | ✅ | — | base only |
| Tool surface (names + when) | ✅ (concise) | `generate-mdl` (tool table) | partial — acceptable; base = contract, skill = field-level usage |
| Physical authority | ✅ (invariant) | all three (procedural) | yes → base owns the invariant; skills should defer (see §8) |
| MDL camelCase shape / `type` / `joinType` enum | ✅ (invariant) | `generate-mdl` (full field ref + example) | base = rule, skill = worked example |
| `properties` carry-forward + consumers | ✅ (canonical) | all three (procedural WHY) | yes → base owns it; skill repetition flagged §8 |
| Validate-before-finish | ✅ | all three | base = rule, skills = error-code tables |
| Propose-don't-persist / drafts-not-deploys | ✅ | `onboarding` (auto-activate detail) | base owns the invariant |
| Onboarding sequence (preflight → seed → overlay → activate) | — | `onboarding` | skill only |
| FK→`joinType` cardinality table, example manifest | — | `generate-mdl` | skill only |
| Gap catalog, `[tag]` discipline, grill/auto-pilot | — | `enrich-context` | skill only |
| Document-enrichment procedure | removed from base | `enrich-context` | **was duplicated in base — removed** |

## 4. mdl_copilot.md changes
- **Intro (keep/revise):** kept the "MDL Copilot / code-editor agent / structure
  authoritative" framing (and the literal "MDL Copilot" string the inspector test
  asserts). **Added** a paragraph stating the base holds always-true invariants and
  the appended Skills hold task procedures, with "invariants win on conflict", plus
  the onboarded-and-stable lifecycle and propose-don't-deploy stance.
- **Tools (revise):** regrouped the same nine tools into MDL / ground-truth /
  validation / documents buckets; trimmed prose. No tool added or renamed.
- **How you work (keep/revise):** kept the 4-step read→edit→validate→summarize
  loop; made step 2 explicit that `write_mdl_file` is a full-file overwrite so the
  whole file is re-emitted (ties into the `properties` rule).
- **Enrichment section (delete → move-to-skill):** removed the standalone
  "Enriching the semantic layer from documents" procedure; the invariant ("ground
  meaning in a real passage, add never strip") survives in the tools bullet + the
  `properties` rule. Procedure now solely in `enrich-context`.
- **Hard rules (revise/add):** rewrote the bulleted "Hard rules" into four named
  contracts. **Added** consumer grounding and seeded-key names to the `properties`
  contract; **added** "no build/compile step" and the relationship arity to MDL
  shape; **added** an explicit "Propose, don't persist" contract.
- **Quick reference (add):** new compact situation→skill/tool table (AGENTS
  skeleton's "quick reference", adapted — **no CLI commands**).

## 5. Native-correctness changes
- The `properties` contract is stated as a **positive** rule ("re-emit existing
  `properties` verbatim before adding"), naming the exact keys and the two consumer
  functions, so a generation is correct from the first token rather than relying on
  cleanup.
- The MDL-shape contract pins camelCase + `type` + `joinType` enum inline, so the
  first write already validates.
- `tools.py:_preserve_superset_properties` is explicitly reframed as
  **defense-in-depth** ("never rely on it"): with the prompt correct, the guard has
  nothing to restore. The base no longer phrases the rule as "a guard will fix
  dropped properties".

## 6. Reconciled recommendations
Merging the three reports' §8:
- **onboarding §8** (name the seeded `superset_*`/`is_time`/`source` keys; add
  `synonyms` to the column properties example) → **adopted**: the `properties`
  contract now lists both the governance keys and the seeded provenance keys, and
  names `synonyms` as a governance key (no separate JSON example needed at base
  altitude — the worked example stays in `generate-mdl`).
- **generate-mdl §8** (base must not tell the model to author YAML/cubes; point at
  the native camelCase contract) → **adopted**: MDL-shape contract states camelCase,
  no YAML, no build step; base authors nothing about cubes.
- **enrich-context §8** (one-line global "re-emit the full object, copy all
  `properties` + physical fields forward verbatim"; `properties.synonyms` is the
  canonical colloquial home) → **adopted**: both are now base invariants.
- **Conflict resolution:** onboarding wanted the seeded keys *named*; generate-mdl
  wanted the base lean and example-free. Resolved by **naming the keys inline but
  keeping the full JSON example only in `generate-mdl`** — the base lists keys, the
  skill shows shape. No wording conflicts between reports otherwise; all three
  converge on the same `properties`/camelCase invariant.

## 7. Over/under-prompting decision
- **Implemented Option A** (prompt-only, always-on injection kept). The base stays
  the invariant layer; depth stays in the skills.
- **Assembled-prompt size:** base `mdl_copilot.md` ≈ 64 lines / ~2.6 KB (incoming
  working-tree version) → **95 lines / 5.5 KB** after. Assembled system prompt
  (`build_inspector`) ≈ **46.4 KB** total: base ~5.5 KB (~12%), skills ~40.9 KB
  (onboarding 9.1 KB, generate-mdl 14.4 KB, enrich-context 17.3 KB). The base grew
  deliberately to absorb the three reports' recommended grounding; it remains a
  small fraction of the whole and is not self-repeating.
- **Option B (recommend, not built):** add a `get_skill(name)`-style tool to
  `MdlToolset` and stop always-injecting all three skills — the agent pulls only
  the relevant skill per task (true Wren `wren skills get` parity). This would cut
  ~30 KB of always-on context per turn. **Cost:** a new tool spec + dispatch in
  `tools.py`, loop wiring so the model knows to fetch, a routing hint in the base,
  and changes to `service.py` (`_skill_texts` → on-demand) plus the inspector and
  its tests. Worthwhile once skills grow further or a fourth/fifth skill lands;
  not justified at three always-on skills today.

## 8. Skill recommendations (no edits — proposals only)
- **All three skills:** now that the base owns the `properties` carry-forward
  invariant *with* consumer grounding and seeded-key names, each skill repeats the
  full WHY (retrieval+coverage degradation, guard-is-defense-in-depth). Trim each
  to a one-line "see the base prompt's `properties` contract; this skill adds
  [task-specific bit]" to remove the ~3× repetition across the assembled prompt.
- **`generate-mdl`:** keep the worked example + field reference + FK→`joinType`
  table (base-unique value); drop the standalone "Phase 5 — properties" WHY in
  favor of the base contract.
- **`enrich-context`:** keep the gap catalog + `[tag]` discipline; its Rule 1
  "add, never strip" can point at the base contract rather than restating it.
- **`onboarding`:** keep the seed/overlay split + lifecycle; the "carry every
  existing key forward verbatim" bullet duplicates the base — shorten to a pointer.
- **(Code, from enrich-context §8)** a scoped `add_instruction` tool would let
  enrichment write rule-shaped facts (categories 4/8/9/10) instead of recommending
  them; out of this agent's scope but the highest-value parity closer.

## 9. Consolidated parity gaps
Union of the three reports' §7 plus this integration:
- **No on-demand skill loading** (Wren `wren skills get`) — we always inject all
  three. Tracked as **Option B** (§7). *New here.*
- **No agent instruction-write tool** — rule-shaped facts are recommended for a
  human to add (`enrich-context §7`). Highest-value code gap.
- **No cube authoring** — schema/validator/merge support cubes, but
  `AuthoredManifest` omits them; aggregations route to metrics/calculated fields
  (`generate-mdl §7`, `enrich-context §7`).
- **No live-DB distinct-value probe** — enum/sentinel/grain settled from physical
  types + documents (`enrich-context §7`).
- **No `queries.yml` / `wren memory` writeback** — NL→SQL pairs belong to the
  `usage` query agent (all three reports).
- **`wren memory index` / 200-model threshold, bundled demo, dlt-connector,
  connection troubleshooting** — dropped as not applicable to a connected,
  RBAC-filtered Superset datasource (`onboarding §7`).
- **`AuthoredMetric` has no explicit `properties` field** (relies on
  `extra="allow"`) — metric-level `displayName`/synonyms reach retrieval only if
  present (`generate-mdl §8`). Latent.

## 10. Verification log
```
# copilot tests green (base prompt advertises "MDL Copilot" + tools)
python -m pytest tests/unit_tests/superset_ai_agent/test_copilot_*.py -q
  → 41 passed

# all 9 tool names in the base prompt exist in tools.py
grep -oE '`(list_mdl_files|read_mdl_file|write_mdl_file|delete_mdl_file|
  get_physical_schema|validate_project|list_documents|search_documents|
  find_duplicate_documents)`' prompts/mdl_copilot.md | sort -u  → all 9

# consumers + guard the base names exist
grep -c _semantic_terms semantic_layer/schema_retriever.py        → 3
grep -c _column_fact semantic_layer/copilot/coverage.py           → 2
grep -c _preserve_superset_properties semantic_layer/copilot/tools.py → 2

# seeded keys the base names exist in the exporter
grep -oE 'superset_dataset_id|superset_database_id|superset_column_name|is_time' \
  integrations/wren/mdl_exporter.py | sort -u  → all 4
grep -n '"source"' integrations/wren/mdl_exporter.py → :93

# assembled prompt sanity (build_inspector)
assembled ≈ 46,379 chars; base ≈ 5,491; 'MDL Copilot' present; one '## Skills'
  block; skills onboarding/generate-mdl/enrich-context injected once each
```
