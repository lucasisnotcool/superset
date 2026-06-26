<!--
PROVENANCE — verbatim upstream Wren skill, fetched for reference only (NOT committed to the repo).
Source: https://raw.githubusercontent.com/Canner/WrenAI/main/core/wren/src/wren/skills_content/enrich-context/SKILL.md
Repo: Canner/WrenAI @ main · path: core/wren/src/wren/skills_content/enrich-context/SKILL.md
Fetched: 2026-06 (verify currency before relying on it).
This is the genuine source for tailoring superset_ai_agent/skills/enrich-context.md.
Sibling skills referenced (gap_catalog, cube_proposals) are NOT fetched here; fetch them
from the same skills_content/ dir if the tailoring agent needs the gap-category detail.
Our equivalents: raw/ + wren memory + cubes/queries.yml are replaced by our document tools
(list_documents/search_documents/find_duplicate_documents), instructions store, and JSON MDL.
-->

# Wren Enrich Context — Fill the Business-Context Gap

This skill exists because most business context never lives in a DB schema — it lives in handbooks, glossaries, finance reports, support playbooks, code comments, Slack rules-of-thumb. The agent reads those raw artifacts, finds what's missing from the Wren project, and either grills the user one question at a time (grill mode) or applies its best inferences directly and hands over an audit (auto-pilot mode) before writing back. The output lands in three (or four) sinks each project already has — no new artifact, no new tooling.

## Hard rules — READ FIRST

### Universal (apply to both modes)

1. Only add, never modify existing. If you find an existing MDL description / relationship / rule that looks wrong, do not edit it. Surface it on the "please fix manually" list shown in Step 9.
2. Every MDL edit must validate. Right after any MDL YAML change, run `wren context validate`. If it fails, revert that single change and feed the error back. Never leave a project in an invalid state.
3. Pre-draft every proposal. Whether you're showing the draft to the user (grill) or applying it directly (auto-pilot), generate the concrete content — never lazy-ask "what should the description say?".
4. Be explicit about confidence. In grill mode, open Lane 3 inference questions with "I'm guessing — ". In auto-pilot, tag every Lane 3 inference and partial Lane 2 match in the Step 9 audit with confidence (high / med / low) and source.

### Grill mode only

5. One question at a time. Grill relentlessly. Walk every gap top-down, resolve one decision before moving to the next. Provide a recommended answer for every question.
6. Skip is final for this session. No pending queue, no nagging next round. If the user wants to revisit, they re-run the skill.

### Auto-pilot mode only

7. Drop into grill for three cases. Always interrupt auto-pilot and ask the user when:
   - (a) Lane 2 conflict — raw and current MDL disagree.
   - (b) High-blast-radius proposal (any lane) — new cube, new view, new relationship, or new MDL metric/calculated column. These become public artifacts visible to every future agent session, so blast radius doesn't depend on whether the trigger was raw evidence (Lane 2) or inference (Lane 3).
   - (c) Lane 2 routing ambiguity — you can't confidently pick a sink (MDL / instructions.md / queries.yml / cubes/).

   Everything else: apply directly and log to the audit list.

## Step 0 — Mode selection (before anything else)

Before touching the project or reading any file, ask the user which mode to run in. Lock the choice for the whole session — no mid-session switching; the user re-runs to change.

> Two modes for this session:
> a) Grill mode — I walk every gap with you, one question at a time, proposing a draft and waiting for your accept / edit / skip.
> b) Auto-pilot mode — I read raw + current context, make my best inferences, and apply them. I'll only stop to grill you on (1) conflicts between raw and existing MDL and (2) high-blast-radius additions. The session ends with a full diff + confidence-tagged inference list.
> Which? (a / b)

Remember the choice as `MODE = grill | autopilot` and use it to branch Steps 6 and 9.

## Preflight

### Step 1 — Choose the Wren project

Always ask the user which project to enrich before doing anything else — never assume cwd. A user can have several Wren projects and an ambient `~/.wren` profile that doesn't match the one they want to augment.

Offer concrete hints in the question so the user can answer in one round-trip:

```bash
# Hint 1 — does cwd look like a project?
test -f wren_project.yml && pwd
# Hint 2 — does ~/.wren/config.yml point at a default project?
grep -E '^project_path:' ~/.wren/config.yml 2>/dev/null
```

After the user answers, lock the path in for the whole session (cd, verify `wren_project.yml`, `wren context show`). If either check fails, stop — suggest `wren skills get onboarding` if it's not a project, or `wren context validate` if the manifest is broken.

From this point on, every command and file path in this skill is relative to the chosen project root. Do not switch projects mid-session.

### Step 2 — Detect memory availability

```bash
wren memory --help >/dev/null 2>&1
```

- Exit 0 → `MEMORY_AVAILABLE = true`. The fourth sink (direct `wren memory store`) is open.
- Exit non-zero → `MEMORY_AVAILABLE = false`. Skip the memory-only paths.

### Step 3 — Ensure raw/ folder exists

```bash
mkdir -p raw
```

If you just created it, tell the user to drop business-context artifacts (PDFs, glossaries, handbooks, financial reports, data dictionaries, sample queries, code with comments) into `raw/` and confirm. Heads-up that contents may be sensitive; don't touch `.gitignore`.

## Step 4 — Read everything

Read both sides — the raw material and the current Wren context — before forming any opinion.

### Raw
Read every file under `raw/`. Use whatever capability your agent has natively (text, markdown, code, PDF). If you can't read a file, tell the user once and move on. Do not install extra Python packages.

### Current Wren context

| Source | Command |
|---|---|
| MDL (full) | `wren context show --output json` |
| Project instructions | `wren context instructions` |
| Existing cubes (names) | `wren cube list` |
| Existing cubes (measures + dimensions) | `wren cube describe <cube>` for each |
| Curated NL-SQL pairs | read `queries.yml` directly |
| (Memory) stored pairs | `wren memory list -n 200 --output json` |
| (Memory) schema as text | `wren memory describe` |

## Step 4.5 — Ground-truth probe (grill mode default; auto-pilot opt-out)

When raw is silent on a column's enum / unit / null / magic / time semantics, the catalog's column-local categories can often be settled by sampling distinct values from the live DB.

| Mode | Default | Override |
|---|---|---|
| Grill | Probe on. Ask once before first query. | User says no → skip. |
| Auto-pilot | Probe off (never queries live DB). | Re-run in grill. |

Candidate selection (no DB call yet): description empty OR lacks the relevant `[tag]`; type/name matches enum / NULL / magic / time-grain triggers.
Probe query: `wren --sql "SELECT DISTINCT <col> FROM <model> LIMIT 30" --output json` (+ MIN/MAX for magic sentinels).
- ≤30 distinct → enum/sentinel/grain candidate (confidence "med — probed values, semantics still inferred").
- 30 (LIMIT hit) → cardinality too high; skip.
- Query fails → don't retry; log + continue with Lane 2 + Lane 3.
Probe each (model, column) at most once; never probe a column that already has a matching `[tag]`; results stay in working memory.

## Step 5 — Three gap-detection lanes (in your head, no artifact)

Before sweeping, load `gap_catalog` — the ten business-semantic categories the schema cannot carry.

### Lane 1 — Structural coverage (mechanical)
Scan current MDL: every model has non-empty `properties.description`? every (non-PK/FK) column has a description? every model has a `primary_key`? every model has ≥1 relationship? `instructions.md` beyond scaffold? `queries.yml` has canonical pairs? Plus walk every column/model against gap_catalog triggers (enum/unit/null/magic/time tags; soft-delete → Default filters; lookalike tables → Canonical tables; currency/external-id sections).

### Lane 2 — Claim-diff (raw vs current context)
For each raw file, internally extract 5–15 atomic claims. Classify each: covered (skip) / partial (tighten) / new (route to sink) / conflict (grill the user in both modes, do not edit existing — surface for manual fix).

### Lane 3 — Inference (your own guesses)
Propose additions the user did not literally state but that would help later (missing cubes for repeated metrics, undefined business terms, undocumented JSON columns). For any aggregation-shaped proposal default to a cube; check `wren cube list`/`describe` first to avoid duplicates. In grill mode open every Lane 3 question with "I'm guessing — "; in auto-pilot tag the audit entry `agent inference`.

## Step 6 — Resolve gaps

Branch on `MODE`.

### Grill mode
For every gap: (1) state the gap + source (quote raw for Lane 2); (2) propose the concrete answer; (3) propose the sink; (4) accept/edit/skip; (5) on accept write back; (6) on edit apply wording then write back; (7) on skip drop it. Explore the codebase/raw instead of asking when you can. One question at a time.

### Auto-pilot mode
Process every Lane 1–3 finding directly except the three escalation cases (Lane 2 conflict; new metric/view/relationship; routing ambiguity → grill those). For everything else: synthesize the proposal, decide the sink, write back, `wren context validate` immediately after any MDL edit (revert single change on failure), append to the audit list with confidence + source.

## Step 7 — Routing & writeback

| Finding type | Sink | How to write |
|---|---|---|
| Schema structure / relationship / view / model or column description | MDL YAML under `models/`, `views/`, `relationships.yml` | Edit the YAML. For enum/unit/null/magic/time/PII, append a `[tag]` line to `properties.description` (prose first, then one tag per category). |
| Aggregation metric / named measure | `cubes/<name>/metadata.yml` | New file per cube. Default sink for SUM/COUNT/AVG/ratio. Validate + `wren cube query --cube <name> --sql-only`; revert on failure. Always escalates to grill in auto-pilot. |
| Default filter / business/naming convention / external mapping / currency / canonical table | `instructions.md` | Append under the catalog `##` heading (Default filters / Naming conventions / External identifiers / Currency / Canonical tables). Create heading if absent; never modify existing text. |
| Canonical NL→SQL example for the team | `queries.yml` | Append under `pairs:` |
| Ad-hoc NL→SQL pair (only if MEMORY_AVAILABLE) | `wren memory store` | `wren memory store --nl "..." --sql "..." --tags "source:enrich"` |

After every MDL edit run `wren context validate`; on failure revert the single change, show/log the error, re-grill (grill) or mark "revert: validation failed" (auto-pilot).

Format reminders: `queries.yml` follows `wren memory dump` (`version: 1`, `pairs:` of `{nl, sql, source}`); MDL YAML uses snake_case keys, `wren context build` converts to camelCase for `target/mdl.json`; `instructions.md` is free-form markdown grouped by heading.

## Step 8 — Session finalize

```bash
wren context build
```
Recompiles `target/mdl.json`. If MEMORY_AVAILABLE: `wren memory index` (re-embeds new schema items, updated instructions.md, new queries.yml entries).

## Step 9 — Summary

Print a tight session report (added counts by sink + by tag; "please fix manually" list of contradictions you didn't edit). Grill extras: skipped count. Auto-pilot extras: confidence-tagged inferred-items audit, validation applies/reverts, escalated-to-grill items.

## Things to avoid

- Do not write a `gaps.yml`/`state.yml` or any tracking artifact. The session lives in conversation.
- Do not modify any existing MDL field, instructions rule, or queries.yml entry — only append/add. Surface mismatches on the manual-fix list.
- Do not install new Python packages to read raw. Ask the user to convert files you can't open.
- Do not auto-resolve a conflict between raw and current MDL — always grill, in both modes.
- Do not present Lane 3 inferences as quoted from raw.
- Do not call `wren memory store` when MEMORY_AVAILABLE = false — write to queries.yml.
- Do not commit anything to git.
- Do not nag about skipped questions (grill only).
- Do not append a `[tag]` line if the same category tag already exists for that column.
- Do not invent new instructions.md headings — stick to the five catalog-defined ones.
- Do not probe the live DB in auto-pilot.
- Do not propose a cube whose measure already exists on the same base_object — point a queries.yml example at the existing cube.
- Do not modify an existing cube YAML even when raw contradicts it.
- Do not write a new cube alongside an old MDL `metrics:` entry covering the same logic — surface "consider migrating to cube".
- Do not skip `wren cube query --cube <name> --sql-only` after creating a cube.
- In auto-pilot, do not auto-apply Lane 2 conflicts or new metric/view/relationship inferences — grill those.

## See also
- `gap_catalog` — ten business-semantic gap categories, triggers, default sinks, write formats.
- `cube_proposals` — decision tree (cube vs view vs calculated column), cube YAML template, naming policy, duplication guard, validation flow.
