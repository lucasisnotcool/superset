# Seagate Multi-Schema — Cross-Schema Smoke-Test Queries

Ground-truth answers were computed directly from the generated data by
`superset/examples/seagate_multi/generate_data.py` (which imports the
`seagate_manufacturing` builders with the same seed `20251231`, so the seven core
tables are byte-identical). Re-run that script to reproduce them verbatim.

**What is different from the single-schema fixture:** the data is split across two
schemas — `seagate_core` (sites, production_lines, drive_skus) and `seagate_ops`
(work_orders, production_events, quality_tests, shipments). Because the data is
identical, **Q1–Q15 keep the same answers**; the only change is that Q6–Q10 and
Q12–Q15 are now genuine cross-schema joins, while Q5 (sites+lines, both `core`) and
Q11 (events only, `ops`) remain within-schema controls. Q16–Q18 are net-new and can
*only* be answered by joining across the schema boundary.

The DB also contains **distractor tables** the glossary never mentions
(`seagate_finance_ledger`, `seagate_iot_sensor_logs`, `seagate_hr_roster`,
`seagate_maintenance_logs`, `seagate_vendor_contracts`, and an out-of-scope
`seagate_ref` schema). A correct answer never references any of them.

## L1 — jargon only, single table (within `seagate_ops`)

**Q1.** How many patties got 86'd on 2025-10-30?
- Fact needed: patty = hard disk unit; 86'd = `units_scrapped`.
- Query: `SELECT SUM(units_scrapped) FROM seagate_ops.seagate_production_events WHERE event_date = '2025-10-30'`.
- Answer: **6**.

**Q2.** How many tickets are currently short order and not yet closed?
- Fact needed: ticket = work order; short order = `ticket_type = SHORT_ORDER`.
- Query: `SELECT COUNT(*) FROM seagate_ops.seagate_work_orders WHERE ticket_type = 'SHORT_ORDER' AND status != 'CLOSED'`.
- Answer: **1**.

**Q3.** How many patties are currently on the griddle, company-wide?
- Fact needed: "on the griddle" = ticket `status = BAKING`; patty count = `target_qty`.
- Query: `SELECT SUM(target_qty) FROM seagate_ops.seagate_work_orders WHERE status = 'BAKING'`.
- Answer: **57**.

**Q4.** How many heat lamp tests have a garnish problem logged?
- Fact needed: heat lamp = `test_type = HEAT_LAMP`; garnish = `garnish_defect`.
- Query: `SELECT COUNT(*) FROM seagate_ops.seagate_quality_tests WHERE test_type = 'HEAT_LAMP' AND garnish_defect = true`.
- Answer: **14**.

## L2 — join required (some cross-schema)

**Q5.** Which sites currently have a line that's WARM?
- Fact needed: WARM = staffed standby, not running. **Within-schema control** —
  both tables live in `seagate_core`.
- Query: `seagate_core.seagate_production_lines JOIN seagate_core.seagate_sites ON site_id WHERE status = 'WARM'`, distinct `site_name`.
- Answer: **Shugart Yard, Scotts Valley West, Reef Hollow** (not Tigerline Point).

**Q6.** How many patties has the Tigerline region plated in total?
- Fact needed: Tigerline region = sites `SGY` + `SGT` (markdown-only); plated =
  `units_completed`. **Cross-schema** (`core` sites/lines + `ops` work_orders/events).
- Query: `seagate_core.seagate_sites JOIN seagate_core.seagate_production_lines JOIN seagate_ops.seagate_work_orders JOIN seagate_ops.seagate_production_events`,
  filter `site_code IN ('SGY','SGT')`, `SUM(units_completed)`.
- Answer: **9,386**.

**Q7.** How many To-Go units has the Reef region shipped, in total?
- Fact needed: Reef region = sites `SGW` + `SGN`; To-Go = `fulfillment_type = TO_GO`.
  **Cross-schema** (`ops` shipments/work_orders + `core` lines/sites).
- Query: 4-table cross-schema join (`seagate_ops.seagate_shipments` -> `seagate_ops.seagate_work_orders` -> `seagate_core.seagate_production_lines` -> `seagate_core.seagate_sites`),
  filter `site_code IN ('SGW','SGN')` and `fulfillment_type = 'TO_GO'`, `SUM(qty_units)`.
- Answer: **2,979**.

**Q8.** How many patties were plated between 2025-12-25 and 2025-12-31, broken down by drive family?
- Fact needed: plated = `units_completed`; drive family lives on
  `seagate_core.seagate_drive_skus`, reached via `seagate_ops.seagate_work_orders.sku_id`.
  **Cross-schema**.
- Query: `seagate_ops.seagate_production_events JOIN seagate_ops.seagate_work_orders JOIN seagate_core.seagate_drive_skus`,
  filter date range, `SUM(units_completed) GROUP BY drive_family`.
- Answer: **Cobalt 193, Vantage 106, Nimbus 40** (Tundra: none in this window).

## L3 — custom derived metric (markdown-only formula, cross-schema)

**Q9.** What was the Golden Yield for the Cobalt drive family in December 2025?
- Fact needed: Golden Yield = `(units_completed - units_scrapped - units_reworked) / units_completed`,
  computed **only over STANDARD tickets**. **Cross-schema** (`ops` events/work_orders
  + `core` drive_skus).
- Query: filter `drive_family = 'Cobalt'`, `event_date` in December 2025, `ticket_type = 'STANDARD'`, apply formula.
- Answer: **0.961** (96.1%), over 845 completed units.

**Q10.** What is the True Pass Rate for Plate Spin tests at Tigerline Point?
- Fact needed: True Pass Rate = `PASS / COUNT(*)` after removing
  `result = FAIL AND garnish_defect = true` rows from the denominator entirely.
  **Cross-schema** (`ops` quality_tests/work_orders + `core` lines/sites).
- Query: filter `site_name = 'Tigerline Point'`, `test_type = 'PLATE_SPIN'`, apply formula.
- Answer: **0.935** (93.5%), over a denominator of 46 tests.

**Q11.** How many patties were plated during the Moonlight shift in the Diner Week of 2025-12-17 to 2025-12-23?
- Fact needed: Diner Week runs Wednesday->Tuesday; Moonlight shift =
  `shift_code = 'MOONLIGHT'`. **Within-schema control** — `seagate_ops` only.
- Query: `SELECT SUM(units_completed) FROM seagate_ops.seagate_production_events WHERE shift_code = 'MOONLIGHT' AND event_date BETWEEN '2025-12-17' AND '2025-12-23'`.
- Answer: **145**.

**Q12 (trap).** What is the Golden Yield for Short Order tickets at Scotts Valley West?
- Fact needed: Golden Yield excludes `SHORT_ORDER` tickets **by definition** — there
  is no valid Golden Yield for a short-order-only slice.
- Correct answer: **undefined / not applicable** — a confident numeric answer means
  the agent applied the formula mechanically without internalizing the exclusion rule.

## L4 — chained multi-hop (cross-schema)

**Q13.** For Q4 2025 (2025-10-01 to 2025-12-31), compare the Tigerline and Reef regions on both Golden Yield and on units shipped via Combo pallets through Dine-In fulfillment.
- Facts: region rollup + Golden Yield (STANDARD-only) + Combo/Dine-In jargon, all cross-schema.
- Answer: **Tigerline** — Golden Yield 0.960 (n=3,818), Combo+Dine-In units = 729.
  **Reef** — Golden Yield 0.962 (n=3,324), Combo+Dine-In units = 521.

**Q14.** Which region had the higher True Pass Rate on Heat Lamp tests in Q4 2025, excluding Short Order tickets — Tigerline or Reef?
- Facts: region rollup + True Pass Rate + ticket_type filter, chained cross-schema.
- Answer: **Tigerline**, at 0.972 (n=36), vs. **Reef** at 0.882 (n=34).

**Q15.** For Nimbus-family shipments that went Dine-In in the last 60 days (2025-11-02 to 2025-12-31), what share of shipped units rode on Combo pallets, by region?
- Facts: region rollup + Dine-In + Combo + drive-family filter + recency window.
- Answer: **Tigerline** — 282 total units, 35.5% on Combo pallets.
  **Reef** — 408 total units, 12.0% on Combo pallets.

## L5 — cross-schema only (net-new for v2; unanswerable without a `core`<->`ops` join)

**Q16.** How many patties were plated on WARM lines, company-wide, broken down by drive family?
- Fact needed: line status (`WARM`) lives on `seagate_core.seagate_production_lines`;
  plated = `units_completed` on `seagate_ops.seagate_production_events`. The status
  filter and the measure are in **different schemas**.
- Query: `seagate_core.seagate_production_lines JOIN seagate_ops.seagate_work_orders JOIN seagate_ops.seagate_production_events`,
  filter `status = 'WARM'`, `SUM(units_completed) GROUP BY drive_family`.
- Answer: **Cobalt 1,751, Vantage 3,017** (only WARM lines: two Vantage lines + one Cobalt line; Tundra/Nimbus WARM lines are DARK with no events).

**Q17.** What was the Golden Yield for the Vantage drive family in Q4 2025 (2025-10-01 to 2025-12-31)?
- Fact needed: Golden Yield (STANDARD-only); `drive_family` on
  `seagate_core.seagate_drive_skus`, measure on `seagate_ops.seagate_production_events`.
  **Cross-schema**.
- Query: filter `drive_family = 'Vantage'`, Q4 window, `ticket_type = 'STANDARD'`, apply formula.
- Answer: **0.951** (95.1%), over 1,567 completed units.

**Q18.** For the Nimbus family in Q4 2025, how many Combo-pallet Dine-In units did the Tigerline and Reef regions each ship, and what was each region's Golden Yield over the same window?
- Facts: region rollup (`core` sites) + Combo/Dine-In + Nimbus filter on
  `seagate_ops.seagate_shipments`, plus Golden Yield on
  `seagate_ops.seagate_production_events` joined to `seagate_core.seagate_drive_skus`.
  Fully **cross-schema and chained**.
- Answer: **Tigerline** — 175 Nimbus Combo+Dine-In units, region Golden Yield 0.960.
  **Reef** — 151 Nimbus Combo+Dine-In units, region Golden Yield 0.962.
