# Seagate Manufacturing — Wren Onboarding Smoke Test

Manual, end-to-end walkthrough proving the semantic layer changes query
outcomes. Pairs with:

- `bi_glossary.md` — the BI document to upload and onboard.
- `test_queries.md` — the L1-L4 graded questions, with ground-truth answers.

Run every question in `test_queries.md` once **before** step 3 below (the
wrong/confused baseline) and once more **after** step 5 (the document is
live). The delta between those two runs is the deliverable.

## 0. Prerequisites

Bring up Superset and the standalone AI agent per
`superset_ai_agent/README.md` (Docker Smoke or Native Dev). These steps
assume the Docker stack (`http://localhost:8090`, agent proxied at
`/ai-agent`); for native dev, drop the `/ai-agent` prefix and use
`http://localhost:8097` instead.

`WREN_ENABLED=true` must be set. If `WREN_ONBOARDING_ENABLED=true` and a real
Wren/LLM backend is configured, the document upload below produces full MDL
proposals; otherwise the deterministic fallback parser still picks up the
`bi_glossary.md` synonyms/metrics (it matches lines containing "also known
as", "Metric ... =", and lines ending in "?") so the smoke test works either
way.

## 1. Load the mock data

```bash
superset load-examples
```

This registers the 7 `seagate_*` tables as Superset Datasets under the
`examples` database (`superset/examples/seagate_manufacturing/`). Confirm in
the UI under **Data > Datasets** — you should see `seagate_sites`,
`seagate_production_lines`, `seagate_drive_skus`, `seagate_work_orders`,
`seagate_production_events`, `seagate_quality_tests`, `seagate_shipments`.

Note the `examples` database's numeric id (**Data > Databases**, commonly
`1` on a fresh install) and its schema name (open any `seagate_*` dataset's
**Edit** modal, or check SQL Lab — the Docker stack's `examples` database is
Postgres and defaults to schema `public`; a native SQLite-backed `examples`
database defaults to schema `main`). The commands below use
`<DB_ID>` and `<SCHEMA>` as placeholders for these two values.

## 2. Resolve the Wren semantic project for this schema

```bash
curl -X POST http://localhost:8090/ai-agent/agent/semantic-layer/projects/resolve \
  -H "Content-Type: application/json" \
  -d '{"database_id": <DB_ID>, "schema_name": "<SCHEMA>"}'
```

Keep the returned `id` if you want to scope later calls to a specific
project; the document and index endpoints below also accept a bare
`database_id`/`schema_name` scope and will resolve the project implicitly.

## 3. Run the baseline queries (before onboarding)

For each question in `test_queries.md`, call:

```bash
curl -X POST http://localhost:8090/ai-agent/agent/query \
  -H "Content-Type: application/json" \
  -d '{
    "question": "How many patties are currently on the griddle, company-wide?",
    "database_id": <DB_ID>,
    "schema_name": "<SCHEMA>",
    "execute": true
  }'
```

Record the `answer_summary`/`sql` from each response — this is the
"intuition only" baseline. Expect jargon questions (patty, griddle, 86'd,
Tigerline, Golden Yield, Diner Week, ...) to be misread or refused outright.

## 4. Upload and review the BI glossary

```bash
curl -X POST http://localhost:8090/ai-agent/agent/semantic-layer/documents \
  -F 'scope={"database_id": <DB_ID>, "schema_name": "<SCHEMA>"}' \
  -F 'file=@superset_ai_agent/dev_fixtures/seagate_manufacturing/bi_glossary.md;type=text/markdown'
```

Note the returned `id` (the document id) and inspect `proposed_updates`.
Approve everything that looks reasonable:

```bash
curl -X PATCH http://localhost:8090/ai-agent/agent/semantic-layer/documents/<DOCUMENT_ID>/review \
  -H "Content-Type: application/json" \
  -d '{"approved_update_ids": ["<update-id-1>", "<update-id-2>", "..."]}'
```

(List documents at any point with
`GET /ai-agent/agent/semantic-layer/documents?database_id=<DB_ID>&schema_name=<SCHEMA>`.)

## 5. Rebuild the semantic-layer index

```bash
curl -X POST http://localhost:8090/ai-agent/agent/semantic-layer/index/rebuild \
  -H "Content-Type: application/json" \
  -d '{"scope": {"database_id": <DB_ID>, "schema_name": "<SCHEMA>"}}'
```

This materializes the approved updates into active Wren MDL files.

## 6. Re-run the same queries (after onboarding)

Repeat step 3's `curl` calls verbatim. Compare each `sql`/`answer_summary`
against the ground-truth in `test_queries.md`:

- **L1/L2** should now resolve jargon and joins correctly.
- **L3** should apply the Golden Yield / True Pass Rate / Diner Week
  definitions, and should *not* produce a confident number for the Q12 trap
  question (Golden Yield of Short Order tickets is undefined by definition).
- **L4** should chain region rollup + custom metric + jargon without being
  told the join path.

A clean run shows wrong-or-refused answers in step 3 turning into correct,
ground-truth-matching answers in step 6 for the same exact question text.
