# Self-Service Connections — Implementation Checklist

**Companion to:** `plan_self_service_connections_spec.md` (design rationale, industry research, risk analysis).
**Status:** ✅ BUILT (WS1+WS2+WS3 complete, 2026-07-02) — see §10 "As-built" for what shipped, deltas from plan, and residual risks. Phase 4 hardening items remain open.
**Audience:** future agent sessions — this is a resumable, source-backed checklist. Check items off as done; respect the stated blockers/dependencies.

---

## 10. AS-BUILT (2026-07-02)

**All WS1/WS2/WS3 items complete.** Verification: 1276 backend tests passed (12 new DB-tied tests in `tests/unit_tests/superset_ai_agent/test_db_tied_artifacts.py`, 32 WS1 tests), 355 FE jest tests passed; migration `0018_db_tied_artifacts` verified up/down/up. One failure in the suite (`test_bulk_activate_fetches_live_schema_once_and_deactivate_zero`) is **pre-existing on pristine HEAD** — not from this work.

### What shipped (key files)
- **Builder role:** `superset/security/builder.py` (`BuilderSecurityManager._is_builder_pvm` = Gamma ∪ sql_lab ∪ {can_write, can_export on Database}; ALPHA_ONLY guard). Wired in `docker/pythonpath_dev/superset_config.py` (`CUSTOM_SECURITY_MANAGER`, `AUTH_USER_REGISTRATION_ROLE="Builder"`). Requires `superset init` to materialize.
- **Owner-scoped connections:** `superset/databases/filters.py` (creator branch OR-ed into `DatabaseFilter`, fail-closed on no user). All pk routes verified base-filtered (`find_by_id`/`find_by_ids`/`datamodel.get`).
- **BOLA fix found during audit:** `superset/commands/database/test_connection.py` resolved the stored model **by name** and substituted decrypted secrets for a masked URI — now gated by owner-scoped `find_by_id` re-resolution. (This was pre-existing but only reachable by admins until Builder got can_write.)
- **DB-tied plumbing:** `ConversationScope.database_uri_fingerprint` + `AgentQueryRequest.database_uri_fingerprint`; `scope_hash` substitutes fingerprint for database_id; new `scope_hashes()` (DB-tied first, legacy second); `scope_matches` fingerprint-aware (both-present → fingerprints decide).
- **Migration:** `persistence/migrations/versions/0018_db_tied_artifacts.py` — nullable `database_uri_fingerprint` + index on documents/nl_sql_examples/instructions. **No backfill** (a migration can't resolve URIs through Superset); legacy rows converge on next update, reads match both keys.
- **Documents:** store owner-gates dropped (both backends); list by fingerprint-or-database_id; chunks keyed by document. Route gate `_load_authorized_document` now **translates** the doc's fingerprint onto the caller's own connection when direct scope auth fails (fail-closed when no match).
- **Instructions:** keyed by scope_hash alone (owner = audit); reads take `scope_hashes` list; **delete is scope-gated** — route now requires scope query params + WRITE auth (FE updated: `deleteInstruction(scope, id)`); LanceDB cache key de-owner-ed (pre-D1b `{owner}:{hash}` rows inert, degrade-closed).
- **Memory:** pooled by `_pool_key(db_id, fingerprint)`; `_pool_predicate` (fingerprint OR legacy-NULL+db_id); converge-on-update; LanceDbMemory cache partition follows.
- **Copilot parity (R8):** `ConversationGraph` gained `instruction_store` + instruction recall in the draft node (injected as `"instructions"` payload key, same as SQL agent); both graphs share memoized `_scope_fingerprint` resolution via the caller's Superset client.
- **Grounding across connections:** `wren_runtime.resolve_effective_schema` DB-guard accepts fingerprint match; `materialize_request_semantic_project` lists projects by fingerprint. This is what makes projects + goldens resolve from user B's own connection.
- **Project access translation (R5/R6):** `SemanticAccessService.caller_database_id_for_fingerprint` (enumerates only the caller's visible connections via their session) + retry in `_require_project_permission`. app.py passes `list_databases` into the service.
- **Verified no-change-needed:** golden queries (project-keyed → fingerprint via project), NL→SQL recall F2 access filter (still applied), conversations (stay owner-scoped), document blobs (identity-neutral).

### Deltas from plan
- Chunks did NOT get a fingerprint column (keyed by document_id; parent doc carries the fingerprint) — simpler than planned 2A.1.
- Instructions kept `scope_hash` as the key (with fingerprint substituted into the hash) instead of re-keying columns; `database_uri_fingerprint` column added as denormalized audit/query aid.
- `_with_scope_fingerprint`/`_instruction_scope_hashes` helpers in app.py enrich scopes **only after** scope authorization (never widens an unauthorized caller's view).

### Residual risks / gaps (carry into Phase 4)
1. **Fingerprint-translation cost:** `caller_database_id_for_fingerprint` enumerates the caller's databases + N identity calls on first miss (memoized per request-scoped service; fingerprint TTL-cached in `_auth_context_cache`). Fine at BYO scale (few connections/user); revisit if users hold many connections.
2. **Same-host-different-privilege creds:** two users' connections to the same physical DB may have *different DB-level privileges*; DB-tied artifacts (docs/instructions/goldens/memory SQL text) are shared at the app layer regardless. Memory/goldens recall still applies the F2 table-reachability filter, but document text is shared wholesale — an uploader with broad DB grants can expose doc content to a narrow-grant user. Accepted under D1 ("access to the database" = any valid connection), but worth stating in user docs.
3. **Fingerprint collisions across replicas:** a read replica with a different host does NOT fingerprint-match its primary (documented limitation, test 3.7 analog in `test_scope_matches_is_fingerprint_aware`).
4. **Legacy rows don't auto-share:** pre-0018 rows (NULL fingerprint) stay per-connection until next update. Deliberate (no backfill possible in-migration); a backfill CLI (resolve fingerprints via Superset admin creds) is a Phase-4 option.
5. **Scope-level events** (`list_events`) remain owner-scoped (not named in D1b); project provenance events were already shared. Flag if the SSE state panel should be DB-tied too.
6. **Pre-existing suite failure:** `test_bulk_activate_fetches_live_schema_once_and_deactivate_zero` fails on pristine HEAD (TTL-cache interaction) — unrelated; fix separately.
7. **Phase 4 open:** SSRF host deny-list (DP-D), audit logging, SECRET_KEY-from-secrets-manager runbook, `_auth_context_cache` TTL review for de-share latency.

### UI-expectation gaps (user intent vs current UI)
- **Connection modal:** Builders now see "+ Database" and only their own rows — no UI change needed.
- ~~**No "shared" affordance**~~ — **CLOSED (2026-07-02, P1–P3 below).**
- **Instruction delete:** now requires an active scope (schema selected). The panel already guards on scope, so no visible change; deleting from a stale panel after switching schema will 404/403 — acceptable, surfaced via existing toast.
- **`superset init` required** after deploy for the Builder role to exist; self-registration flows 500 on a missing role otherwise (deployment runbook note).

### UI sharing-affordance closure (P1–P3, 2026-07-02) — SHIPPED
- **P1 (correctness bug fixed):** `InstructionsPanel.tsx` previously asserted instructions were **personal/private** (`instructions-personal-note`, "Only your own instructions are listed") — written for the pre-D1b design, made **false** by the D1b re-keying. Replaced with accurate DB-tied copy (`instructions-shared-note`, "…shared with everyone who can connect to this database… don't add anything you wouldn't share"). Tests updated.
- **P2 (canonical affordance):** new `DatabaseSharedBadge.tsx` (Tag+Popover, neutral/blue, `Icons.UsergroupAddOutlined`, keyboard-focusable per WCAG 1.4.1/2.1.1) in the MDL Lab workspace-strip badge cluster (`index.tsx` beside `SemanticLayerStateBadge`/`CoverageBadge`). Popover names the database and enumerates what's shared (models/docs/instructions/goldens/learned SQL) vs private (chat). 4 new tests.
- **P3 (point-of-authoring hints):** shared-note Alert on `AttachDocumentDialog.tsx` (document upload) + the corrected P1 note on instructions — the two surfaces where a user *types content others will read*. Goldens (promoted, not typed) and memory (passive) rely on the P2 badge only. Freeform `uploadSemanticDocument` scope path has **no FE caller**, so no other upload surface to cover.
- **Verification:** full AiAgentPanel jest suite 360 passed (was 355; +5), editor index suite green (badge mounts), tsc clean, prettier-formatted.
- **Residual UI gaps after P1–P3:**
  1. The badge lives **only in MDL Lab**, not the SQL-agent query panel. Correct (authoring happens in MDL Lab) but a user who only ever uses the AI SQL agent + instructions never sees the badge — the P1 inline note is their only cue. Acceptable; revisit if instructions get authored outside MDL Lab.
  2. **"Always apply" (global) instructions** are shared *and* unconditionally injected for every user on the DB — higher blast radius than a scoped one. Per D1a decision, kept to the neutral badge + P1 note; no extra per-toggle warning (a scary warning on a normal collaboration feature would be noise). Flag if support reports confusion.
  3. Badge copy is **static** — it doesn't enumerate *who* currently shares the DB (no member list). Intentional (privacy + no cheap API for it); "everyone who can connect" is the honest, stable statement.
  4. **`databaseLabel` may be null** for a freshly-resolved project; badge degrades to "this database" (tested). Cosmetic.

---

---

## 0. Settled decisions (do not re-litigate)

| # | Decision | Resolution |
|---|---|---|
| D1 | MDL/artifact sharing model | **DB-tied, not user-tied.** Artifacts are keyed by `database_uri_fingerprint`; a user reaches them by owning their **own** validated connection to that physical DB. Credentials stay owner-private. |
| D1a | Co-accessor write vs read | **Both can edit** (keep existing FULL-access → `write` in `_project_with_permission`). |
| D1b | What is DB-scoped | **RAG documents + golden queries + NL→SQL memory + instructions** all DB-tied. Principle: *unless explicitly called out (e.g. MDL project instructions), MDL Copilot and AI SQL agent behave identically.* Conversations stay owner-scoped. |
| D2 | Role | **New `Builder` role** (not a mutation of stock Gamma), set as `AUTH_USER_REGISTRATION_ROLE`. |
| D3 | Connection ownership | **`created_by` single-owner** (Option A). No `owners` M2M — connections are never shared (each user brings their own). |

### ⚠️ Correctness invariant (read before touching agent-side scoping)
The shared key **must be `database_uri_fingerprint`**, never `database_id`. Two users with separate connections to the same physical DB have **different** `database_id`s but the **same** fingerprint (creds are stripped in `fingerprint_database_uri`, `semantic_layer/uri_fingerprint.py`). Any artifact keyed by `database_id` is effectively *per-user* in this model and will silently fail to share. This is the single most important thing to get right.

---

## 1. Requirements (testable acceptance criteria)

- **R1** A `Builder` user can create/edit/delete/test their own DB connection (BYO credentials). *(WS1)*
- **R2** A `Builder` user sees **only** connections where `created_by == themselves`; Admins see all. No route (list, get, edit, delete, test, `/connection`, export) leaks another user's connection or credentials. *(WS1)*
- **R3** A `Builder` user has SQL Lab + MDL Lab + MDL Copilot + AI SQL agent, and can run a query against their own DB. *(WS1)*
- **R4** The `Builder` role never holds `all_database_access`. *(WS1)*
- **R5** Given users A and B **each** with their own connection to the same physical DB-X: B sees and can edit the MDL project(s), RAG documents, golden queries, NL→SQL memory, and instructions A created for DB-X — because both connections fingerprint to DB-X. *(WS2)*
- **R6** A user with **no** connection to DB-X sees none of DB-X's artifacts. *(WS2)*
- **R7** No DB-tied artifact embeds data a co-accessor could not independently obtain: no plaintext credentials, and (verify) no cached row-level result data. Schema metadata, learned SQL, and instructions are shareable; conversations remain private. *(WS2 + review)*
- **R8** MDL Copilot and AI SQL agent recall the **same** DB-tied context (memory, goldens, docs, instructions) — no divergence. *(WS2)*

---

## 2. Remaining decision points (resolve during Phase 0)

- **DP-A — Document/instruction DB key: add a `database_uri_fingerprint` column vs. derive at query time.**
  Recommendation: **add a `database_uri_fingerprint` column** to `AiAgentSemanticDocument`, `AiAgentDocumentChunk` (or reach via `document_id→project/database`), and `AiAgentInstruction`, populated on write by resolving the fingerprint once (via the agent's Superset client `get_database_identity`). Rationale: matches the proven `AiAgentSemanticProject.database_uri_fingerprint` pattern (`persistence/models.py:271`); avoids a per-read fingerprint resolution round-trip; makes a clean partial-unique/index key. Backfill existing rows in the migration (resolve fingerprint from `database_id`).
- **DP-B — NL→SQL memory re-key from `database_id` → `database_uri_fingerprint`.**
  Not explicitly named in D1b, but required for the invariant (memory keyed by `database_id` won't share across users' separate connections) and for R8/identical-behavior. Recommendation: **re-key memory to fingerprint** for consistency with the DB-tied principle. If product wants memory to stay per-connection, that is an explicit carve-out and R5 no longer covers memory. *Flag for user confirmation.*
- **DP-C — `Builder` write access to the connection-detail endpoint.**
  `GET /database/<id>/connection` uses `DatabaseConnectionSchema`, whose docstring says "only for admins" (`superset/databases/schemas.py:1125`). Builders must edit their **own** connection. Recommendation: allow it, gated by the owner-scoped `base_filters` (they can only fetch their own row); keep credentials masked as today. Verify no unmasked field is added to the schema.
- **DP-D — Host allow/deny guardrail (SSRF).** Recommendation: ship a deny-list for loopback/link-local/metadata IPs; keep `allow_dml`/`allow_ctas` off by default (already default-off, `models/core.py:229-231`). Can land in Phase 4 but decide the policy now.

---

## 3. Workstreams & dependency graph

```
WS1 (Superset core: role + connection ownership)  ──┐
                                                     ├──► WS3 (verification: cross-user isolation, e2e sharing)
WS2 (AI agent: DB-tie docs/instructions/memory)   ──┘
```
- **WS1 and WS2 are independent in code** and can proceed in parallel.
- **WS3 end-to-end sharing tests (R5/R6) depend on BOTH** (need multiple Builder users each with a connection to the same DB).
- Within WS2, each artifact is independent; do documents and instructions first (they need schema changes), memory second (DP-B), golden queries last (verify-only).

---

## 4. WS1 — Superset core: Builder role + owner-scoped connections

### Phase 1A — Builder role (no schema change)
- [ ] **1A.1** Create a `CUSTOM_SECURITY_MANAGER` subclass of `SupersetSecurityManager`.
  - Where: new file e.g. `superset/extensions_deploy/security.py` (or the deployment's config module). Wire via `CUSTOM_SECURITY_MANAGER` in `superset_config.py` (config key at `superset/config.py:219`; consumed at `superset/initialization/__init__.py:971`).
  - Dependency: none. Blocker for everything else in WS1.
- [ ] **1A.2** Override `sync_role_definitions()` to also register a `Builder` role after the built-ins:
  - Pattern to mirror: `superset/security/manager.py:1874-1889` (`set_role("Gamma", self._is_gamma_pvm, pvms)` etc.). Add `self.set_role("Builder", self._is_builder_pvm, pvms)`.
  - `set_role` recomputes `role.permissions` wholesale each sync (`manager.py:1983-2001`) — this is why the grant must live in the subclass, not the UI (Risk 8.7 in spec).
- [ ] **1A.3** Implement `_is_builder_pvm(self, pvm)` = Gamma baseline ∪ SQL Lab bundle ∪ Database-write, **minus** `all_database_access`:
  ```
  return (
      self._is_gamma_pvm(pvm)
      or self._is_sql_lab_pvm(pvm)                       # SQLLAB_ONLY ∪ EXTRA (manager.py:2110)
      or (pvm.view_menu.name == "Database"
          and pvm.permission.name in self.DATABASE_WRITE_PERMS)
  )
  ```
  - `all_database_access` is `("all_database_access","all_database_access")` — view_menu ≠ `"Database"`, and it is `ALPHA_ONLY_PERMISSIONS` (`manager.py:801`), so none of the three branches include it. **Add a unit assertion** that it is absent (R4).
  - Blocker: confirm the exact Database write permission names in the running instance — inspect generated PVMs for view_menu `"Database"` (`can_write`, and possibly `can_add`/`can_edit`/`can_delete`; the REST API maps mutations to `can_write` via `MODEL_API_RW_METHOD_PERMISSION_MAP`, `databases/api.py:185`). Set `DATABASE_WRITE_PERMS` accordingly.
- [ ] **1A.4** Confirm SQL Lab menu + editor perms come through: `("menu_access","SQL Lab")`, `("menu_access","SQL Editor")`, `can_sqllab`, `can_read`/`can_execute_sql_query`/`can_get_results` on `SQLLab`, plus `TabStateView`/`TableSchemaView`/`SavedQuery` perms (all in `SQLLAB_ONLY_PERMISSIONS`, `manager.py:816-847`). These unlock MDL Lab/Copilot/AI-SQL automatically (they mount inside SQL Lab, `superset-frontend/src/SqlLab/components/AppLayout/index.tsx:141`).
- [ ] **1A.5** Confirm `AiAgent` permission is granted (it already is to Gamma, so Builder inherits it via the gamma branch — `superset/ai_agent/api.py`). No action expected; verify.
- [ ] **1A.6** Set registration/default role to `Builder`: `AUTH_USER_REGISTRATION_ROLE = "Builder"` (and decide `AUTH_USER_REGISTRATION` per spec Open Q4). Ref `superset/config.py:458`.
- [ ] **1A.7** Run `superset init` (invokes `sync_role_definitions`) and verify the `Builder` role's PVM set in the Roles UI/API.

### Phase 1B — Owner-scope the `Database` list (the load-bearing security change)
- [ ] **1B.1** Modify `DatabaseFilter.apply` to add creator scoping.
  - Where: `superset/databases/filters.py:41-76`.
  - Pattern to mirror: `SavedQueryFilter` (`superset/queries/saved_queries/filters.py:83-98`) — `created_by == g.user` unless privileged.
  - Change: keep the existing privileged early-return (`security_manager.can_access_all_databases()` → all; covers Admin/Alpha, `manager.py:1103`). In the else branch, **add** `Database.created_by_fk == get_user_id()` to the existing `or_(...)` (so a Builder sees their own connections **plus** anything explicitly PVM-granted; additive, doesn't break existing shares).
  - `created_by` is auto-stamped on insert (`default=get_user_id`, `models/helpers.py:590`) — no create-path change needed for ownership.
  - ⚠️ **Risk 8.1 (BOLA):** this is the single control preventing cross-user connection disclosure. Deny-by-default; derive the principal from session (`get_user_id()`), never the request.
- [ ] **1B.2** Verify every single-object DB route enforces `base_filters` (so owner-scoping covers get/edit/delete/test/`/connection`/export, not just list).
  - `base_filters = [["id", DatabaseFilter, ...]]` (`databases/api.py:187`); object fetch via `self.datamodel.get(pk, self._base_filters)` (`databases/api.py:792`).
  - **Checklist per route** (confirm each resolves the object through `_base_filters`, else add an explicit ownership check): `get` (show), `put` (edit), `delete`, `test_connection`, `get_connection` (`/<pk>/connection`), `export`, `related`/`schemas`/`tables` metadata routes, SSH-tunnel routes. Any route bypassing `_base_filters` is a BOLA hole.
- [ ] **1B.3** Resolve **DP-C**: allow Builder to read/edit **their own** connection detail (`/<pk>/connection`) via the owner-scoped filter; verify `DatabaseConnectionSchema` (`databases/schemas.py:1121-1125`) emits only masked values.
- [ ] **1B.4** Confirm creator visibility works **without** granting per-DB PVMs to the creator (the `created_by` branch covers it), so `add_permissions` (`superset/commands/database/utils.py:54`) needs no change. Verify a freshly created connection is visible to its creator and invisible to another Builder.

### Phase 1C — Credential-edge hardening (OWASP API3, spec 8.3)
- [ ] **1C.1** Confirm masked-read paths: `password`/`encrypted_extra`/`server_cert` are encrypted columns (`models/core.py:220,246,248`); URI stores `PASSWORD_MASK` (`models/core.py:465-472`); password fields are `load_only`. Add/keep a response-schema assertion that no plaintext secret can serialize.
- [ ] **1C.2** Scrub credentials from **error messages** (connection failures echoing a DSN), **export/import**, and **clone/duplicate** payloads. Audit `TestConnectionDatabaseCommand` error surfaces and the export command.
- [ ] **1C.3** Ensure any cached SQLAlchemy engine/pool is keyed per `(user/connection)`, never by DB coordinates alone (spec 8.4) — verify no cross-principal engine reuse.

---

## 5. WS2 — AI agent: DB-tie documents, instructions, memory (D1b)

> Reference pattern to copy everywhere: `SemanticProject` — keyed by `database_uri_fingerprint`, `owner_id` is audit-only, `_is_visible`/`_with_permission` `del owner_id` (`superset_ai_agent/semantic_layer/projects.py:858-874`). The DB-access proof stays in `load_context`/`authorize_semantic_scope` (`app.py:582-608`), unchanged.

### Phase 2A — RAG documents + chunks → fingerprint scope
- [ ] **2A.1** (DP-A) Add `database_uri_fingerprint` column to `AiAgentSemanticDocument` (`persistence/models.py:139-163`) and `AiAgentDocumentChunk` (`:187-218`). Alembic migration + backfill (resolve fingerprint from each row's `database_id` via the same helper `SemanticProject` uses).
  - Dependency: none; blocker for 2A.2–2A.4.
- [ ] **2A.2** Drop the owner gate in the store (both backends), scope by fingerprint instead:
  - `sqlalchemy_store.py`: `_get_document_model:481` (remove `owner_id != owner_id → NotFound` at `:489`), `get_document:192`, `list_documents:64` (`WHERE owner_id` → `WHERE database_uri_fingerprint`), `update_document:206`, `delete_document:238` (+ chunk delete `:256`).
  - `memory.py` twins: `102`, `56`, `113`, `123`.
  - Keep `owner_id` as a write-side `created_by` stamp (mirror projects). Stamp `database_uri_fingerprint` on `save_document`/`_document_to_model` (`sqlalchemy_store.py:53,494`) and `save_chunks` (`:261`).
- [ ] **2A.3** Chunk-scope methods → fingerprint (or via `project_id`/`document_id`): `list_chunks:289`, `delete_chunks:310`, `save_chunks:261`, `memory.py` twins `147/159/134`.
- [ ] **2A.4** Route layer: `_load_authorized_document` (`app.py:4314-4333`) — once the store owner-gate is gone, the sole enforcement becomes `authorize_semantic_scope(document.scope)` (already DB-access based). Verify the 404-if-not-authorized still holds for a user with **no** connection to the DB (R6). Endpoints inheriting this: `app.py:4277,4339,4355,4382,4404,4426,4457` and scope-list `list_semantic_documents:4251`.
  - **Note:** project-attached document reads (`list_project_documents` etc., `sqlalchemy_store.py:92`) are **already** fingerprint-shared via `project_id` — no change. Only the scope-list + single-doc (owner-gated) paths change.

### Phase 2B — Instructions → fingerprint scope + Copilot parity (R8)
- [ ] **2B.1** (DP-A) Add `database_uri_fingerprint` to `AiAgentInstruction` (`persistence/models.py:442-457`). Migration + backfill from `database_id`. Decide fate of `scope_hash` (keep for schema-level partition within a DB, or drop — see `store.py:233 instruction_scope_hash`).
- [ ] **2B.2** Re-key the store from `owner_id` to fingerprint:
  - `semantic_layer/instructions.py`: `list_instructions:248` (`WHERE owner_id AND scope_hash` → `WHERE fingerprint [AND scope_hash]`), `add:221` (stamp fingerprint; keep owner as audit), `delete:261` (drop `owner_id != owner_id` gate at `:264`; authorize via scope instead), `recall:270`.
  - Fix the vector-cache partition key `LanceDbInstructionStore._scope_key:306` (`f"{owner_id}:{scope_hash}"` → fingerprint-based) so cache doesn't leak/mis-partition.
- [ ] **2B.3** Routes: `app.py:4518` (add), `:4544` (list), `:4575` (delete) — keep `authorize_semantic_scope` (DB-access) as the gate; drop reliance on owner partitioning.
- [ ] **2B.4** **Parity fix (R8):** add instruction recall to `ConversationGraph` (MDL Copilot) draft path, mirroring `TextToSqlGraph` (`graph.py:699-710`). Currently `conversation_graph.py` never calls `instruction_store` — this is the one divergence found. Also reconcile app-level helper `_recalled_instructions` (`app.py:2657`) to fingerprint scope.

### Phase 2C — NL→SQL memory → fingerprint scope (DP-B, pending confirmation)
- [ ] **2C.1** *(Gated on DP-B = yes.)* Re-key `AiAgentNlSqlExample` (`persistence/models.py:416-439`) from `database_id` to `database_uri_fingerprint`. Add column + backfill; update `SqlAlchemyMemory.load_candidates:539` (`WHERE database_id` → `WHERE fingerprint`) and `store_confirmed:567`.
  - Reads: `graph.py:677` ≡ `conversation_graph.py:1147` (already identical). Write-back: `graph.py:953`, `conversation_graph.py:1573` — thread fingerprint resolution.
  - **If DP-B = no:** document explicitly that NL→SQL memory is per-connection (not shared across users to the same DB), and that R5 excludes memory.

### Phase 2D — Golden queries (verify-only, no change)
- [ ] **2D.1** Confirm goldens remain correct: they live in `AiAgentSemanticMdlFile` (`queries.json`) keyed by `project_id` (`mdl_files.py:list:344`, owner ignored), and projects are fingerprint-keyed — so goldens are **already** DB-tied. Recall path `recall_golden_queries` (`golden_queries.py:265`) is read identically by both agents (`graph.py:685` ≡ `conversation_graph.py:1153`). Just add a regression test asserting cross-user visibility (R5).

### Phase 2E — Confirm no owner-private data rides along (R7)
- [ ] **2E.1** Audit shared artifacts for embedded owner-private data: verify documents/chunks store extracted text/embeddings only (no other user's credentials), goldens/memory store SQL text (acceptable), instructions store text. Confirm **conversations stay owner-scoped** (`conversations/store.py:41-137`, untouched). Flag any cached row-level result data for exclusion.

---

## 6. WS3 — Verification (merge gate)

- [ ] **3.1** Role/permission unit tests: `Builder` PVM set includes SQL Lab bundle + Database write; **excludes `all_database_access`** (R4). Re-running `superset init` preserves grants (subclass durability).
- [ ] **3.2** Cross-user connection isolation (R2, BOLA merge gate — spec 8.1): Builder-A cannot list/get/edit/delete/test/export/`/connection` Builder-B's connection (expect 404/403, uniform response to avoid enumeration oracle). Admin sees all.
- [ ] **3.3** Secret non-disclosure (R7): GET/list/export/clone/error paths never contain a plaintext password or unmasked DSN (schema assertion + error-message scan).
- [ ] **3.4** SQL Lab + agent functional (R3): Builder opens SQL Lab, runs a query on their own DB; AI SQL agent executes as them (`/api/v1/sqllab/execute/`, `integrations/superset/rest.py:259`).
- [ ] **3.5** DB-tied sharing e2e (R5/R6 — depends on WS1+WS2): two Builders each with a connection to the same physical DB see/edit the same MDL project, documents, goldens, instructions (and memory if DP-B=yes); a third Builder with no such connection sees none.
- [ ] **3.6** Identical-behavior (R8): assert MDL Copilot and AI SQL agent recall the same memory/goldens/docs/instructions for the same scope.
- [ ] **3.7** Fingerprint stability: two connections `db://userA:x@host/db` and `db://userB:y@host/db` produce the same fingerprint; a replica/alt-host does not (document the limitation).
- [ ] Prefer unit/integration over E2E per repo guidance; add cross-user tests to CI.

---

## 7. Phase 4 — Hardening (follow-up, non-blocking)
- [ ] **4.1** (DP-D) Host allow/deny list for self-service connections (loopback/link-local/metadata IPs); confirm `allow_dml`/`allow_ctas`/file-upload default-off (`models/core.py:229-231`).
- [ ] **4.2** Audit logging on owner-scoped connection access + DB-tied artifact access (detect enumeration / cross-owner attempts).
- [ ] **4.3** (Spec D5) `SECRET_KEY` from a secrets manager + `re-encrypt-secrets` rotation runbook; evaluate KMS envelope encryption / per-user DEKs.
- [ ] **4.4** Review `_auth_context_cache` TTL (`app.py:573`) for acceptable de-share/revocation latency.

---

## 8. Risk register (delta from spec §8, updated for the DB-tied model)

| # | Risk | Mitigation | Phase |
|---|---|---|---|
| R-1 | `DatabaseFilter` gap → cross-user connection/credential leak (BOLA) | Owner-scope + per-route `base_filters` audit + cross-user tests as merge gate | 1B, 3.2 |
| R-2 | Builder accidentally granted `all_database_access` → owner-scoping bypassed | Explicit exclusion + unit assertion | 1A.3, 3.1 |
| R-3 | **Artifact keyed by `database_id` doesn't share across users' separate connections** | Key everything on `database_uri_fingerprint` (the invariant) | 2A–2C |
| R-4 | Owner-private data rides along with a shared artifact (API3) | R7 audit; conversations stay owner-scoped; no cached rows in shared state | 2E, 3.3 |
| R-5 | Agent divergence (instructions only in AI SQL agent) breaks identical behavior | Add instruction recall to `ConversationGraph` | 2B.4, 3.6 |
| R-6 | Vector-cache mis-partition after re-key (LanceDB instruction cache keyed by owner) | Update `_scope_key` to fingerprint | 2B.2 |
| R-7 | Self-service SSRF / dangerous engines | Host deny-list; DML/CTAS off by default | 4.1 |
| R-8 | Role edits wiped by `sync_role_definitions` | All grants in the subclass, never UI | 1A.2 |

---

## 9. Key source references (quick index)
**Superset core:** `security/manager.py` (`704`, `801`, `816-847`, `1103`, `1874-1889`, `1983-2001`, `2110`), `databases/filters.py:41`, `queries/saved_queries/filters.py:83`, `databases/api.py:187,792`, `commands/database/create.py:58`, `commands/database/utils.py:54`, `models/core.py:220,465`, `models/helpers.py:590`, `config.py:219,458`, `initialization/__init__.py:971`, `databases/schemas.py:1121`.
**AI agent:** `semantic_layer/projects.py:858-874,682`, `semantic_layer/uri_fingerprint.py`, `persistence/models.py:139,187,271,416,442`, `semantic_layer/sqlalchemy_store.py:64,192,238,261,481`, `semantic_layer/memory.py:56,102,147`, `semantic_layer/instructions.py:221,248,264,306`, `semantic_layer/memory_store.py:539,567`, `semantic_layer/golden_queries.py:265`, `semantic_layer/mdl_files.py:344`, `graph.py:677,685,699,953`, `conversation_graph.py:1147,1153,1573`, `app.py:582,2657,3537,4251,4314,4518`.
**Frontend:** `superset-frontend/src/SqlLab/components/AppLayout/index.tsx:141`, `superset-frontend/src/features/databases/*`.
