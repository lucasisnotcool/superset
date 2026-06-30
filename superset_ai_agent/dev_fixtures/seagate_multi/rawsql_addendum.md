# Seagate Manufacturing — Legacy Report SQL (raw extract)

This page is pasted verbatim from the legacy reporting warehouse. The analysts want
these **standard reusable reports** re-published as named views in the semantic
layer so they stop copy-pasting the SQL. The SQL below is correct and ran in
production; it is written against the **physical tables** (raw `schema.table`).

When re-publishing, prefer a **semantic** view over the MDL **model names**
(`seagate_production_lines`, `seagate_drive_skus`, `seagate_work_orders`,
`seagate_production_events`) — substitute each physical table below with its model
and drop the physical `schema.` qualifier; the engine resolves each model's
physical schema from its `tableReference`. Keep the columns and logic identical.

## `warm_line_output_by_family` (legacy SQL)

```sql
SELECT
    sku.drive_family,
    SUM(ev.units_completed) AS plated_units
FROM seagate_core.seagate_drive_skus            AS sku
JOIN seagate_ops.seagate_work_orders            AS wo  ON wo.sku_id  = sku.sku_id
JOIN seagate_core.seagate_production_lines      AS ln  ON ln.line_id = wo.line_id
JOIN seagate_ops.seagate_production_events      AS ev  ON ev.work_order_id = wo.work_order_id
WHERE ln.status = 'WARM'
GROUP BY sku.drive_family;
```

## `standard_golden_yield_by_family` (legacy SQL)

```sql
SELECT
    sku.drive_family,
    SUM(ev.units_completed - ev.units_scrapped - ev.units_reworked)::float
        / NULLIF(SUM(ev.units_completed), 0) AS golden_yield
FROM seagate_ops.seagate_production_events      AS ev
JOIN seagate_ops.seagate_work_orders            AS wo  ON wo.work_order_id = ev.work_order_id
JOIN seagate_core.seagate_drive_skus            AS sku ON sku.sku_id = wo.sku_id
WHERE wo.ticket_type = 'STANDARD'
GROUP BY sku.drive_family;
```

These two are the reusable patterns; publish each as one view.
