# superset_ai_agent — agent guide

Standalone FastAPI + LangGraph AI agent service (conversational DB assistance,
text-to-SQL, MDL semantic layer). Deliberately separate from Superset core.

## Documentation

All design docs, feature specs, implementation plans, audits, and references
are indexed at **[docs/README.md](docs/README.md)** — read that index first
instead of grepping the docs tree. Layout: `docs/plans/` (specs + impl
checklists), `docs/reference/` (as-built references, audits),
`docs/archive/` (finished one-off briefs), `evaluation/` (eval specs +
results, beside the runner code).

Rules that matter when editing docs:

- **Never rename a doc file.** Code comments, test docstrings, and session
  memory cite docs by exact filename (e.g. `wren_full.md Phase 1.3`).
- New plan docs go in `docs/plans/` as `plan_<topic>_spec.md` /
  `plan_<topic>_impl.md`, carry a `Status:` line, and get one line added to
  `docs/README.md` in the same change.
- Update a plan's `Status:` header when its work ships.

## Runtime Markdown (not documentation — never move)

- `prompts/*.md` — loaded by `prompts/registry.py` (DB-backed overrides in
  `prompts/store.py`; repo files are the seed defaults).
- `skills/*.md` — loaded by `skills/__init__.py`.
- `dev_fixtures/**/*.md` and `evaluation/` fixtures — read by eval runners.

## Orientation

- [README.md](README.md) — setup (Windows PowerShell), service overview.
- [MACOS.md](MACOS.md) — macOS setup.
- [ARCHITECTURE.md](ARCHITECTURE.md) — file-by-file architecture map,
  runtime diagrams, endpoints.
- Tests live in `tests/unit_tests/superset_ai_agent/` (repo root).
