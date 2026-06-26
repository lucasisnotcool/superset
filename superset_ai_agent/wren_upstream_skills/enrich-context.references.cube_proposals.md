<!--
PROVENANCE — verbatim upstream Wren reference, fetched for reference only (NOT committed).
Source: https://raw.githubusercontent.com/Canner/WrenAI/main/core/wren/src/wren/skills_content/enrich-context/references/cube_proposals.md
Repo: Canner/WrenAI @ main · path: core/wren/src/wren/skills_content/enrich-context/references/cube_proposals.md
Fetched: 2026-06. Sibling reference of enrich-context/SKILL.md.
NOTE for our fork: confirm whether OUR mdl_schema.py + validator + materializer support
cubes end-to-end before porting the cube sink. If cubes are schema-only (not query-wired),
map aggregation metrics to OUR supported sink (metrics / calculated columns) instead.
-->

# Wren Enrich Context — Cube Proposals

When raw documents define a named aggregation metric (`ARR = MRR × 12`, `weekly active users`, `quarterly churn`), the right sink is almost always a cube. Cubes give agents a structured aggregation API instead of asking them to hand-write `GROUP BY` and `DATE_TRUNC` — the place where small models fail most often.

## Sink decision tree

```
Raw mentions a named metric / aggregation pattern
├── Same base model has multiple measure-shaped columns + at least one group-by dimension
│   → propose CUBE  (cubes/<name>/metadata.yml)
├── Pure row-level expression (amount_with_tax = amount * 1.1, no grouping)
│   → propose CALCULATED COLUMN  (is_calculated: true, expression: ...)
├── Needs JOIN across multiple models, window function, or CTE
│   → propose VIEW  (views/<name>/metadata.yml)
└── Old-style MDL `metrics:` already covers it
    → surface on "please fix manually" — do not propose a duplicate cube alongside
```

Why cube is the default: agents pick wrong joins, double-count, and mis-truncate dates when forced to write aggregation SQL by hand. Cubes pre-declare those decisions once.

## Before proposing — duplication guard
```bash
wren cube list                       # all existing cube names
wren cube describe <cube_name>       # measures + expressions per cube
```
- Same expression already exists for the same base_object → do not propose; add a `queries.yml` example pointing at the existing cube.
- Same name, different base_object → name collision → `<name>_v2` (auto-pilot) or grill for a better name.
- Old MDL `metrics:` already defines this → surface on manual-fix ("consider migrating to a cube").

## Naming policy
snake_case, lowercase; most specific term raw uses; singular. Grill: show draft name, let user accept/edit. Auto-pilot: use draft, log it.

| Raw term | Draft cube name |
|---|---|
| ARR / Annual Recurring Revenue | arr |
| Weekly Active Users / WAU | weekly_active_users |
| Quarterly Churn | quarterly_churn |
| Net Revenue Retention / NRR | nrr |

## YAML template
```yaml
# cubes/<name>/metadata.yml
name: <name>                  # snake_case, matches the file's directory
base_object: <model_or_view>  # MUST already exist — verify with wren context show
measures:
  - name: total
    expression: SUM(<column>)
    type: DOUBLE
  - name: count
    expression: COUNT(*)
    type: BIGINT
dimensions:
  - name: <dim_name>
    expression: <column>
    type: VARCHAR
time_dimensions:
  - name: <td_name>
    expression: <ts_column>
    type: TIMESTAMP
hierarchies:
  - name: time
    levels: [year, quarter, month]
properties:
  description: |
    <one-line summary from raw + source citation>
```

### Measure expression patterns
| Pattern | Expression | Type |
|---|---|---|
| Sum a column | `SUM(<col>)` | DOUBLE/BIGINT matching col |
| Row count | `COUNT(*)` | BIGINT |
| Distinct count | `COUNT(DISTINCT <col>)` | BIGINT |
| Average | `AVG(<col>)` | DOUBLE |
| Ratio (named in raw) | `SUM(<num>) / NULLIF(SUM(<den>), 0)` | DOUBLE |
| Derived multiplier (ARR = MRR×12) | `SUM(<mrr_col>) * 12` | DOUBLE |

When raw gives an explicit formula, use it verbatim; quote the source in `properties.description`.

### base_object selection
Must already exist as a model or view. If the metric crosses tables: use a relationship's reachable column if one exists; else propose a VIEW that pre-joins, then a cube on that view; else surface on manual-fix (needs a relationship first).

## Validation flow
```bash
wren context validate                                   # structural
wren cube query --cube <name> --measures <m> --sql-only # semantic (expressions compile)
```
On failure: revert the cube YAML, log/grill/skip. Never leave a project with a cube that fails both checks.

## Auto-pilot escalation
Cubes are always high-blast-radius (public name in `wren cube list`). In auto-pilot, treat every cube proposal as a Universal Rule 7(b) escalation: drop into grill, ask, then apply or skip — even for Lane 2 NEW claims.

## Things not to do
- Don't write a cube whose base_object doesn't exist.
- Don't invent measure/dimension columns not on base_object (or reachable via relationships).
- Don't add time_dimensions when raw didn't ask for time bucketing.
- Don't write a `metrics:` entry on a model when proposing the same logic as a cube (cube replaces metric).
- Don't modify an existing cube YAML even if raw contradicts it (Universal Rule 1 → manual-fix).
