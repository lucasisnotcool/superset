# Seagate Manufacturing — Wren Smoke-Test Queries

Ground-truth answers below were computed directly from the generated data by
`superset/examples/seagate_manufacturing/generate_data.py` (seed `20251231`,
window 2025-07-01..2025-12-31). Re-run that script to reproduce them
verbatim. Use these to grade the agent's answer — not to grade phrasing, but
the underlying SQL logic and the final number.

Run each question once **before** uploading `bi_glossary.md` (capture the
wrong/confused baseline) and once **after** the document is reviewed,
approved, and the index is rebuilt. The delta between those two runs is the
actual proof of value.

## L1 — jargon only, single table

**Q1.** How many patties got 86'd on 2025-10-30?
- Fact needed: patty = hard disk unit; 86'd = `units_scrapped`.
- Query: `SELECT SUM(units_scrapped) FROM seagate_production_events WHERE event_date = '2025-10-30'`.
- Answer: **6**.

**Q2.** How many tickets are currently short order and not yet closed?
- Fact needed: ticket = work order; short order = `ticket_type = SHORT_ORDER`.
- Query: `SELECT COUNT(*) FROM seagate_work_orders WHERE ticket_type = 'SHORT_ORDER' AND status != 'CLOSED'`.
- Answer: **1**.

**Q3.** How many patties are currently on the griddle, company-wide?
- Fact needed: "on the griddle" = ticket `status = BAKING`; patty count for an
  in-process ticket is its `target_qty`.
- Query: `SELECT SUM(target_qty) FROM seagate_work_orders WHERE status = 'BAKING'`.
- Answer: **57**.

**Q4.** How many heat lamp tests have a garnish problem logged?
- Fact needed: heat lamp = `test_type = HEAT_LAMP`; garnish = bracket/hardware,
  logged via `garnish_defect`.
- Query: `SELECT COUNT(*) FROM seagate_quality_tests WHERE test_type = 'HEAT_LAMP' AND garnish_defect = true`.
- Answer: **14**.

## L2 — join required, including a markdown-only mapping

**Q5.** Which sites currently have a line that's WARM?
- Fact needed: WARM = staffed standby, not running.
- Query: `seagate_production_lines JOIN seagate_sites ON site_id WHERE status = 'WARM'`, distinct `site_name`.
- Answer: **Shugart Yard, Scotts Valley West, Reef Hollow** (not Tigerline Point).

**Q6.** How many patties has the Tigerline region plated in total?
- Fact needed: Tigerline region = sites `SGY` + `SGT` (markdown-only — no
  `region` column anywhere); plated = `units_completed`.
- Query: `seagate_sites JOIN seagate_production_lines JOIN seagate_work_orders JOIN seagate_production_events`,
  filter `site_code IN ('SGY','SGT')`, `SUM(units_completed)`.
- Answer: **9,386**.

**Q7.** How many To-Go units has the Reef region shipped, in total?
- Fact needed: Reef region = sites `SGW` + `SGN`; To-Go = `fulfillment_type = TO_GO`.
- Query: 4-table join (`seagate_shipments` -> `seagate_work_orders` -> `seagate_production_lines` -> `seagate_sites`),
  filter `site_code IN ('SGW','SGN')` and `fulfillment_type = 'TO_GO'`, `SUM(qty_units)`.
- Answer: **2,979**.

**Q8.** How many patties were plated between 2025-12-25 and 2025-12-31, broken down by drive family?
- Fact needed: plated = `units_completed`; drive family lives on
  `seagate_drive_skus`, reached via `seagate_work_orders.sku_id`.
- Query: `seagate_production_events JOIN seagate_work_orders JOIN seagate_drive_skus`,
  filter date range, `SUM(units_completed) GROUP BY drive_family`.
- Answer: **Cobalt 193, Vantage 106, Nimbus 40** (Tundra: none in this window).

## L3 — custom derived metric (markdown-only formula)

**Q9.** What was the Golden Yield for the Cobalt drive family in December 2025?
- Fact needed: Golden Yield = `(units_completed - units_scrapped - units_reworked) / units_completed`,
  computed **only over STANDARD tickets**.
- Query: `seagate_production_events JOIN seagate_work_orders JOIN seagate_drive_skus`,
  filter `drive_family = 'Cobalt'`, `event_date` in December 2025, `ticket_type = 'STANDARD'`, apply formula.
- Answer: **0.961** (96.1%), over 845 completed units.

**Q10.** What is the True Pass Rate for Plate Spin tests at Tigerline Point?
- Fact needed: True Pass Rate = `PASS / COUNT(*)` after removing
  `result = FAIL AND garnish_defect = true` rows from the denominator entirely.
- Query: `seagate_quality_tests JOIN seagate_work_orders JOIN seagate_production_lines JOIN seagate_sites`,
  filter `site_name = 'Tigerline Point'`, `test_type = 'PLATE_SPIN'`, apply formula.
- Answer: **0.935** (93.5%), over a denominator of 46 tests.

**Q11.** How many patties were plated during the Moonlight shift in the Diner Week of 2025-12-17 to 2025-12-23?
- Fact needed: Diner Week runs Wednesday->Tuesday (2025-12-17 is a
  Wednesday); Moonlight shift = `shift_code = 'MOONLIGHT'` (06:00-14:00,
  deliberately not "moonlight" hours).
- Query: `SELECT SUM(units_completed) FROM seagate_production_events WHERE shift_code = 'MOONLIGHT' AND event_date BETWEEN '2025-12-17' AND '2025-12-23'`.
- Answer: **145**.

**Q12 (trap).** What is the Golden Yield for Short Order tickets at Scotts Valley West?
- Fact needed: Golden Yield excludes `SHORT_ORDER` tickets **by definition**,
  not by the asker's filter — there is no valid Golden Yield for a
  short-order-only slice.
- Correct answer: **undefined / not applicable** — a confident numeric answer
  here means the agent applied the formula mechanically without internalizing
  the exclusion rule. This is the one question where the "right" answer is a
  refusal, not a number.

## L4 — chained multi-hop

**Q13.** For Q4 2025 (2025-10-01 to 2025-12-31), compare the Tigerline and Reef regions on both Golden Yield and on units shipped via Combo pallets through Dine-In fulfillment.
- Facts needed: region rollup (markdown-only) + Golden Yield formula
  (STANDARD-only) + Combo/Dine-In jargon, all combined.
- Answer: **Tigerline** — Golden Yield 0.960 (n=3,818), Combo+Dine-In shipped
  units = 729. **Reef** — Golden Yield 0.962 (n=3,324), Combo+Dine-In shipped
  units = 521. Note the split: Reef has the *higher* yield, Tigerline has the
  *higher* Combo+Dine-In volume — no region wins on both, which is the point:
  a model that just picks one "winning" region without computing both numbers
  has not actually run the query.

**Q14.** Which region had the higher True Pass Rate on Heat Lamp tests in Q4 2025, excluding Short Order tickets — Tigerline or Reef?
- Facts needed: region rollup + True Pass Rate formula (garnish-only-failure
  exclusion) + ticket_type filter, chained across
  `seagate_quality_tests` -> `seagate_work_orders` -> `seagate_production_lines` -> `seagate_sites`.
- Answer: **Tigerline**, at 0.972 (n=36), vs. **Reef** at 0.882 (n=34).

**Q15.** For Nimbus-family shipments that went Dine-In in the last 60 days (2025-11-02 to 2025-12-31), what share of shipped units rode on Combo pallets, by region?
- Facts needed: region rollup + Dine-In jargon + Combo jargon + drive-family
  filter + a recency window, with no custom formula — this isolates
  join-chaining skill from formula recall.
- Answer: **Tigerline** — 282 total units, 35.5% on Combo pallets.
  **Reef** — 408 total units, 12.0% on Combo pallets.
