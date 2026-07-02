<!--
PROVENANCE — verbatim upstream Wren reference, fetched for reference only (NOT committed).
Source: https://raw.githubusercontent.com/Canner/WrenAI/main/core/wren/src/wren/skills_content/enrich-context/references/gap_catalog.md
Repo: Canner/WrenAI @ main · path: core/wren/src/wren/skills_content/enrich-context/references/gap_catalog.md
Fetched: 2026-06. Sibling reference of enrich-context/SKILL.md.
-->

# Wren Enrich Context — Gap Catalog

Ten business-semantic categories that the schema alone cannot carry. The main `SKILL.md` sweeps these every session — Lane 1 uses them as type-aware mechanical checks, Lane 2 maps raw claims onto them, Lane 3 proposes them when raw is silent.

## How to use this catalog

| Lane | How the catalog is used |
|---|---|
| Lane 1 (structural) | For each model/column, check the *Trigger* column below. A trigger that fires → a gap candidate for that category. |
| Lane 2 (claim-diff) | For each atomic claim extracted from raw, classify it under one of the 10 categories before deciding the sink. |
| Lane 3 (inference) | If raw is silent but a trigger from this catalog fires AND the slot is empty, propose an inference (open with "I'm guessing — " in grill mode, tag `agent inference` in auto-pilot). |

Categories 1, 2, 3, 5, 7 write to column `properties.description` (prose + `[tag]` line). Categories 4, 6, 8, 9, 10 write to `instructions.md` (new `##` section appended). All sinks are append-only — never modify what's there.

## Description write format (column-local categories)

Prose first, then one `[tag]` line per category. Tags are greppable for re-enrichment audits.

```yaml
- name: status
  type: VARCHAR
  properties:
    description: |
      Customer subscription status snapshot at row creation time.
      [enum] free=unpaid trial, pro=paid monthly, enterprise=contracted SLA
      [null] NULL = signup not yet completed
```

Use lowercase tag names exactly as listed below — Lane 1 greps these for re-enrichment dedup.

## The catalog

### 1. Enum value semantics
- Trigger: type ∈ {VARCHAR, CHAR, TEXT, INTEGER, SMALLINT} AND distinct count ≤ 30 AND description lacks `[enum]`.
- Name hints: status, state, type, kind, category, tier, level, flag, *_code.
- Raw scan: "enum", "values", "code", "constant", "0 =", "A =", "must be one of".
- Sink: column `properties.description`. Tag: `[enum] A=active, B=banned, C=churned`.
- Why: schema shows the raw code only; agent guesses meanings and ships wrong filters.

### 2. Unit / scale
- Trigger: name matches *_amount|*_price|*_cost|*_value|*_total|*_qty|*_count|*_duration|*_size|*_bytes|*_ratio|*_rate AND description lacks `[unit]`.
- Raw scan: "USD", "cents", "ms", "milliseconds", "seconds", "bytes", "KB", "%", "basis points", "decimal", "fraction".
- Sink: column `properties.description`. Tag: `[unit] cents (multiply by 0.01 for USD)` / `[unit] ms` / `[unit] basis points (10000 = 100%)`.
- Why: silent off-by-100x bugs in revenue / latency / percentage queries.

### 3. NULL semantics
- Trigger: not_null = false AND description lacks `[null]` AND NULL has business meaning.
- Name hints: *_at, last_*_at, deleted_at, optional FK columns.
- Raw scan: "not yet", "never", "n/a", "missing means", "absent", "uninitialized".
- Sink: column `properties.description`. Tag: `[null] NULL = user never logged in`.
- Why: `IS NULL` vs `< X` produce wildly different rows.

### 4. Soft-delete / active filters
- Trigger: model has deleted_at/is_deleted/archived_at/is_active/is_internal/tombstone_at OR raw mentions soft delete / active rows only / exclude internal.
- Sink: `instructions.md` under `## Default filters`.
- Format: rules like "`orders` queries exclude `deleted_at IS NOT NULL` unless asked"; "`users` default to `is_active = true AND is_internal = false`".
- Why: invisible from schema; "right SQL, wrong rows".

### 5. Magic sentinels
- Trigger: numeric column whose distinct values include outliers (-1, 0, 9999, 99999999) AND description lacks `[magic]`.
- Raw scan: "unknown", "all", "any", "n/a", "default", "sentinel", "-1 means", "999 means".
- Sink: column `properties.description`. Tag: `[magic] -1 = unknown; 0 = system user`.
- Why: averages/sums silently poisoned by sentinels.

### 6. Synonyms / business aliases
- Trigger: raw uses a business term mapping to a model/column/metric not present verbatim in MDL names/descriptions ("customer" → customers; "ARR" → mrr*12; "DAU").
- Sink: `instructions.md` under `## Naming conventions`.
- Why: retrieval matches on terms; users speak business, schema speaks tech.

### 7. Date / time conventions
- Trigger: type ∈ {DATE, TIMESTAMP, TIMESTAMP_TZ, TIMESTAMPTZ} AND description lacks `[time]` AND TZ/event-vs-record/grain ambiguous.
- Name hints: *_at, *_time, *_date, created_*, updated_*, as_of_*, effective_*.
- Raw scan: "UTC", "timezone", "event time", "as of", "snapshot", "month-end", "fiscal", "rolling".
- Sink: column `properties.description`. Tag: `[time] UTC; event time (not insert time); month-end snapshot for billing rows`.
- Why: cross-TZ aggregations, fiscal-vs-calendar buckets, as-of snapshots produce wrong-bucket numbers that pass dry-run.

### 8. Cross-system identifiers
- Trigger: name contains external-system tag (stripe_*, salesforce_*, intercom_*, hubspot_*, *_external_id, *_external_ref) OR raw maps internal ID to external system.
- Sink: `instructions.md` under `## External identifiers`.
- Why: the agent has no schema for foreign systems; needs explicit mapping + format/null semantics.

### 9. Currency / locale
- Trigger: model has currency/locale/country/region/fx_rate/original_amount OR raw mentions FX/multi-currency/non-USD reporting.
- Sink: `instructions.md` under `## Currency`.
- Why: the agent sums mixed-currency rows without converting if no rule is present.

### 10. Canonical table preferences
- Trigger: lookalike tables (users/users_v3, orders/orders_archive/orders_summary) OR raw says "use X not Y"/"deprecated"/"raw mirror".
- Sink: `instructions.md` under `## Canonical tables`.
- Why: without a rule the agent picks tables by lexical proximity and goes to the wrong one.

## Re-enrichment audit
```bash
grep -rE '\[(enum|unit|null|magic|time|pii)\]' models/
grep -E '^## (Default filters|Naming conventions|External identifiers|Currency|Canonical tables)' instructions.md
```
Any existing `[tag]` line or `##` section means that category was touched — do not rewrite (Universal Rule 1). Surface contradictions on the manual-fix list.

## Out of scope (do not propose in this skill)
- PII/privacy policy — add a `[pii] mask in non-prod` line if raw flags a column, but don't draft an org-wide policy.
- Performance hints — `cached:` is an MDL field, not a description rule.
- Row-level access — use the `row_level_access_controls:` MDL field, not free-text.
- Schema corrections — wrong PK/join/type. Surface on manual-fix; never edit (Universal Rule 1).
