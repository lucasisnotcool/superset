---
description: Rescan the live tree and re-anchor the current plan before resuming (after parallel agents or stale context)
---

Other agent work has completed and/or time has passed. Rescan and re-anchor before
resuming. $ARGUMENTS

1. Run `git status` and `git log --oneline -10`; diff reality against what the
   current plan doc assumes.
2. Re-verify the plan's entrypoints/touchpoints against the live tree with
   source-backed evidence (file:line). Flag any plan claim that no longer holds —
   do not trust prior-session summaries or memory.
3. Check whether file touchpoints conflict with any still-running agent; produce a
   go/no-go with the safe boundary (which files are green to edit).
4. If go: update the plan checklist and proceed. If no-go: stop and report the
   conflict.
