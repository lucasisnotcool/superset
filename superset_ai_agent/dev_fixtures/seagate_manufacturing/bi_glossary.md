# Seagate Manufacturing — BI Glossary & Join Guide

This is the internal BI wiki page for the `seagate_*` tables loaded into the
`examples` database. Floor staff and the ERP export use diner slang for
almost everything; this page is the only place that slang is mapped back to
real columns. None of the facts below can be derived from the schema or
column names alone — if it isn't written here, don't guess it.

## Tables and how they join

- `seagate_sites` (`site_id`) — one row per fab. Joins to
  `seagate_production_lines.site_id`.
- `seagate_production_lines` (`line_id`) — one row per physical line inside a
  site. Joins to `seagate_sites.site_id` and to
  `seagate_work_orders.line_id`.
- `seagate_drive_skus` (`sku_id`) — the product catalog. Joins to
  `seagate_work_orders.sku_id`.
- `seagate_work_orders` (`work_order_id`) — one row per ticket. Joins up to
  `seagate_production_lines.line_id` and `seagate_drive_skus.sku_id`, and
  down to `seagate_production_events.work_order_id`,
  `seagate_quality_tests.work_order_id`, and
  `seagate_shipments.work_order_id`.
- `seagate_production_events` (`event_id`) — one row per ticket per
  production day. Joins to `seagate_work_orders.work_order_id`.
- `seagate_quality_tests` (`test_id`) — one row per QA test performed on a
  ticket. Joins to `seagate_work_orders.work_order_id`.
- `seagate_shipments` (`shipment_id`) — one row per outbound shipment for a
  ticket. Joins to `seagate_work_orders.work_order_id`.

To get from a site to its production output: `seagate_sites` ->
`seagate_production_lines` -> `seagate_work_orders` ->
`seagate_production_events` (for output) or `seagate_quality_tests` (for QA)
or `seagate_shipments` (for shipped units).

## Glossary

A "patty" is also known as a single physical hard disk drive unit. It is the
general floor term for one unit of product, regardless of what stage it's
in.

A "ticket" is also known as a row in `seagate_work_orders` — one production
batch of a single SKU on a single line.

"On the griddle" is also known as a ticket whose `status` is `BAKING` — it
is actively in production right now, not yet finished.

"Plated" is also known as the `units_completed` count on
`seagate_production_events` — patties that finished the bake and are ready
for QA.

"86'd" is also known as the `units_scrapped` count on
`seagate_production_events` — patties discarded outright.

"Flipped" is also known as the `units_reworked` count on
`seagate_production_events` — patties sent back through a rework pass
instead of being scrapped.

A "short order" is also known as a ticket with `ticket_type = SHORT_ORDER` —
an expedited rush batch. Short order tickets run a shorter bake and get only
one QA test recorded instead of two (see Business Rules below).

A "heat lamp" test is also known as a `seagate_quality_tests` row with
`test_type = HEAT_LAMP` — a thermal burn-in stress test.

A "taste test" is also known as a `seagate_quality_tests` row with
`test_type = TASTE_TEST` — a read/write data-integrity test.

A "plate spin" test is also known as a `seagate_quality_tests` row with
`test_type = PLATE_SPIN` — a platter balance and vibration test.

"Garnish" is also known as the mounting bracket and external hardware kit
bolted onto a drive — it is not the drive mechanism itself. A
`garnish_defect = true` row means the only thing wrong with that unit was
the bracket/hardware kit, not the drive.

"Dine-In" is also known as a shipment with `fulfillment_type = DINE_IN` — it
moves through a regional distribution warehouse before it reaches the
customer.

"To-Go" is also known as a shipment with `fulfillment_type = TO_GO` — it is
drop-shipped straight to the customer, bypassing the warehouse.

A "Combo" pallet is also known as a shipment with `pallet_type = COMBO` —
any pallet carrying more than one `sku_id`, regardless of drive family. A
"Single" pallet (`pallet_type = SINGLE`) carries exactly one `sku_id`.

A "HOT" line (`seagate_production_lines.status = HOT`) is also known as a
line that is actively running production right now.

A "WARM" line (`status = WARM`) is also known as a line that is staffed and
ready but currently idle — it is not running.

A "DARK" line (`status = DARK`) is also known as a line that has been
decommissioned and will not run again.

## Regions (not a column anywhere)

The "Tigerline" region is also known as the combination of sites `SGY`
(Shugart Yard) and `SGT` (Tigerline Point) — note it spans two countries and
is not the same thing as the `SGT` site code alone.

The "Reef" region is also known as the combination of sites `SGW` (Scotts
Valley West) and `SGN` (Reef Hollow) — also spans two countries.

There is no `region` column on `seagate_sites`. Region membership is fixed
business knowledge, not derivable from `site_code`, `site_name`, or
`country`.

## Custom metrics

Metric Golden Yield = (units_completed - units_scrapped - units_reworked) /
units_completed, summed over `seagate_production_events` joined to
`seagate_work_orders`, counting only rows where `ticket_type = STANDARD`.
Short order tickets are excluded from Golden Yield by definition — they are
never included in the numerator or denominator, even if a question asks for
the yield of short order tickets specifically. Golden Yield is undefined for
a short-order-only slice.

Metric True Pass Rate = COUNT(result = PASS) / COUNT(*) over
`seagate_quality_tests`, after first removing every row where
`result = FAIL AND garnish_defect = true` from both the numerator and the
denominator. A garnish-only failure counts as neither a pass nor a fail.

## Business calendar

The "Diner Week" is also known as the production-scheduling week. It runs
Wednesday through Tuesday, not the standard Sunday-Saturday or
Monday-Sunday calendar week.

## Business rules

Short order tickets get exactly one row in `seagate_quality_tests`, while
standard tickets get exactly two. This is expected and by design — it is not
a data quality gap, and it is not a sign that QA was skipped on those
tickets.

## Shift hours (deliberately not what the names suggest)

The SUNRISE shift runs 22:00 to 06:00 (overnight).

The DAYLIGHT shift runs 14:00 to 22:00 (afternoon into evening).

The MOONLIGHT shift runs 06:00 to 14:00 (morning into early afternoon).

## Example questions this glossary should make answerable

Show how many patties are currently on the griddle?

Show how many patties got 86'd on a given day?

Compare Golden Yield between the Tigerline region and the Reef region?

Show the True Pass Rate for Plate Spin tests at Tigerline Point?

List how many patties were plated during the Moonlight shift in a given
Diner Week?
