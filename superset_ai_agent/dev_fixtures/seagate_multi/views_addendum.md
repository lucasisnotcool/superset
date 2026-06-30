# Seagate Manufacturing — Standard Reusable Reports (View Catalog)

This page extends the BI glossary above. It names the **standard, reusable
analyses** the manufacturing analytics team runs every week. Each one joins across
models (often across the `seagate_core` ↔ `seagate_ops` schema boundary), so the
team wants each published as a **named semantic view** anyone can query by name.

Use the glossary's slang→column mappings and metric definitions above. Write each
view's statement as **semantic SQL over the MDL model names** (`seagate_sites`,
`seagate_production_lines`, `seagate_drive_skus`, `seagate_work_orders`,
`seagate_production_events`, `seagate_quality_tests`, `seagate_shipments`) — never
hand-qualify a physical `schema.table`. Give every view a clear
`properties.description`.

## 1. `warm_line_output_by_family` — Plated output on WARM lines, by drive family

For each drive family, the total **plated** patties produced on **WARM** lines.
(Recall: "plated" and "WARM" are defined in the glossary above.) Join the drive
catalog → work orders → production events, and work orders → production lines to
reach the line status. This is the reusable pattern behind "how many patties were
plated on WARM lines for each family".

## 2. `standard_golden_yield_by_family` — Golden Yield by drive family

The **Golden Yield** custom metric (see its exact definition in the glossary),
rolled up by drive family. Only STANDARD tickets count, per the metric definition.
A reusable cross-model analysis the yield reviews run every week.

## 3. `region_channel_shipments` — Shipped units by region and channel

For each **region** (the site→region rollups in the glossary) and each shipment
channel — **Dine-In** vs **To-Go**, and **Combo** vs **Single** pallets — the total
shipped units. Joins sites → production lines → work orders → shipments, crossing
the schema boundary. Published as a view so regional reviews don't re-derive it.

> Do **not** build views over the distractor tables (`seagate_iot_sensor_logs`,
> `seagate_finance_ledger`, `seagate_hr_roster`, `seagate_vendor_contracts`,
> `seagate_maintenance_logs`) — they are not part of any standard report.
