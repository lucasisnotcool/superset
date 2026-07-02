---
description: Execute the approved plan checklist sequentially — tests after each item, then a risk/gap report
---

Proceed with full implementation as planned. $ARGUMENTS

Rules for this run:

1. **Re-anchor first**: re-check `git status` and the plan doc's touchpoints against
   the live tree (other agents may have edited files since the plan was written).
   Update the plan if it has drifted; flag conflicts instead of editing files another
   agent owns.
2. **Complete all items in sequence** without pausing for approval.
3. **After EACH item**: add tests, run them (backend: relevant pytest subset;
   frontend: `npx jest <paths>` + `npx tsc` typecheck of touched files), and mark the
   item `[COMPLETE]` with evidence in the plan doc.
4. **After all items**: report remaining risks and any gaps between
   (a) dev intent vs actual implementation and
   (b) user expectation vs actual UI behavior.
5. New env vars go into `.env example` / `.env.example` too; flag that a sync +
   image rebuild is needed.
6. Do not commit or push unless explicitly asked.
