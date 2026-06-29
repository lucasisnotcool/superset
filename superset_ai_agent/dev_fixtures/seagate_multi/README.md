# Seagate Multi-Schema — Cross-Schema Eval Fixture (v2)

The multi-schema sibling of `seagate_manufacturing`, built to exercise what the
single-schema fixture cannot: **cross-schema MDL**, **table-selection
discrimination** against distractors, and the new **coverage** signal. Pairs with
[`../../evaluation/EVAL_V2_SPEC.md`](../../evaluation/EVAL_V2_SPEC.md).

## Contents

- `bi_glossary.md` — the BI document to onboard/enrich. Rewritten so every join
  path is schema-qualified and the region/metric facts force cross-schema hops.
  **Names no distractor table.**
- `test_queries.md` — Q1–Q18 with ground truth. Q1–Q15 keep the
  `seagate_manufacturing` answers (data is byte-identical); Q16–Q18 are net-new and
  cross-schema-only.
- `tables.json` — machine-readable manifest (relevant vs distractor sets, schema
  map, adversarial distractors) consumed by the harness for E9.

## Data layout (two schemas + an out-of-scope third)

| Schema | Tables | Role |
| --- | --- | --- |
| `seagate_core` | sites, production_lines, drive_skus (+ hr_roster, vendor_contracts) | master/reference (+ distractors) |
| `seagate_ops` | work_orders, production_events, quality_tests, shipments (+ finance_ledger, iot_sensor_logs, maintenance_logs) | transactional (+ distractors) |
| `seagate_ref` | marketing_campaigns, web_sessions | out-of-scope distractors |

**Adversarial distractors** (designed to tempt mis-selection): `finance_ledger.units`
(vs units_completed), `iot_sensor_logs.temperature_c`/`line_id` (vs "heat lamp" + a
shared FK), `hr_roster.shift_code`/`site_id` (vs the shift mapping + a shared FK).

## Prerequisites — **Postgres only**

This fixture requires the **Docker dev stack (Postgres)**. SQLite has no real
schemas, so the multi-schema split and cross-schema joins cannot be represented
(EVAL_V2_SPEC.md R4). The eval harness fails fast on a non-Postgres examples DB.

## Loading

The data + dataset YAMLs are generated (and ground truth recomputed) by:

```bash
python superset/examples/seagate_multi/generate_data.py
```

This asserts byte-parity with `seagate_manufacturing` and prints the Q16+ numbers.
Then `superset load-examples` auto-discovers `superset/examples/seagate_multi/`
(pure filesystem glob — no code wiring) and loads the 14 tables into the three
schemas of the `examples` database. The legacy single-schema `seagate` fixture is
left intact so the single-vs-split comparison (E10) is possible.

## Running the eval

Point the harness at this fixture and the multi-schema project:

```python
cfg = EvalConfig.from_env(
    fixture_name="seagate_multi",
    schema_name="seagate_ops",                      # primary schema
    schema_names=["seagate_core", "seagate_ops"],   # project scope (NOT seagate_ref)
)
```

`seagate_ref` is deliberately excluded from the project scope — pulling it in is an
E9 failure (the project should not model an out-of-scope schema).
