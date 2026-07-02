# Database Access Grants (Admin Pre-Approval) — Spec + Implementation Checklist

Status: **SHIPPED 2026-07-02 — all items P0, S1–S9 complete.** As-built notes
live inline in the §4 checklist; §5 risks; §6 residual UI-expectation gaps.
Verified: 69 dedicated backend tests (526 incl. surrounding security/models
sweep), 23 frontend jest tests, ruff/ruff-format/mypy/prettier clean, tsc
clean.
Extends: `plan_self_service_connections_impl.md` (Builder role, owner-scoped `DatabaseFilter`, DB-tied ai_agent artifacts).
Audience: future agent sessions — use §4 as a resumable sequential checklist.

---

## 1. Intent

Admin pre-approves a list of **usernames** for access to a specific, existing database
connection. When a user with a matching username signs in for the first time (SSO
auto-registration) — or next signs in / next loads the app, if the grant arrived after
their first sign-in — they automatically gain access to that connection **without
entering credentials**, and with it all database-scoped objects (MDL projects, documents,
instructions, golden queries, learned SQL — everything keyed by database identity per
the self-service-connections work).

A **persistent dialog** (shown until explicitly acknowledged, server-persisted) notifies
the user which database they were granted: hostname, port, database name, backend,
connection username — everything in the DB signature **except the password**.

Trust assumptions (per deployment owner):
- User account creation stays untouched. On the target (Windows) deployment all
  non-admin accounts are SSO-backed, so `username == email == trusted identity`.
  We do NOT implement SSO here; we only make claiming work across all FAB auth paths.
- Admin is fully trusted (SECURITY.md): granting access to a connection Admin can see
  is an intended capability, not a boundary violation.

## 2. Design

### 2.1 Grant mechanism — native `database_access` PVM via a per-database role

Why not a filter-only change: `DatabaseFilter.apply`
(`superset/databases/filters.py:45-89`) controls *listing*, but SQL Lab execution and
dataset access go through `security_manager.can_access_database` → the
`database_access` PVM on `Database.perm` = `[db_name].(id:N)`
(`superset/models/core.py:1297-1308`). A grant that only touched the filter would show
the DB but deny queries. So the grant IS the PVM.

PVMs attach to roles, not users → materialize a **per-database grant role**:

- Name: `db_grant_<database_id>` (id-based → stable across DB rename; the view-menu
  string renames in place via `database_after_update`,
  `superset/security/manager.py:~2198`, so the PVM inside the role stays valid).
- Contents: exactly one PVM — `find_permission_view_menu("database_access", db.perm)`
  (created at DB insert by `database_after_insert`, manager.py:~2152; `merge_pv`
  backfills via `create_missing_perms`, manager.py:1828).
- Created lazily at first claim: `sm.add_role(name)` + `sm.add_permission_role(role, pvm)`
  (FAB sqla manager: add_role @587, add_permission_role @1180, find_role @675).
- Claim = append role to `user.roles` (idempotent), stamp the grant row.
- `sync_role_definitions` / `set_role` only rewrite the roles they name (Builder +
  FAB builtins) — custom `db_grant_*` roles survive syncs.

### 2.2 Pending-grant storage — new table `database_user_grants`

| column | type | notes |
| --- | --- | --- |
| `id` | int PK | |
| `uuid` | UUIDType, unique | repo UUID-migration convention |
| `database_id` | FK `dbs.id`, `ondelete=CASCADE`, indexed | grant target |
| `username` | String(255), stored **lowercased+stripped**, indexed | pasted by admin |
| `user_id` | FK `ab_user.id`, nullable | set at claim |
| `claimed_at` | DateTime, nullable | set at claim |
| `acknowledged_at` | DateTime, nullable | set when user dismisses the dialog |
| AuditMixinNullable | | `created_by` = granting admin, `created_on` = grant time |

- Unique `(database_id, username)`.
- Derived status: `pending` (no `user_id`) → `claimed` → `acknowledged`.
- Model file: `superset/models/database_grant.py`, imported from
  `superset/models/__init__.py` so Alembic autogenerate/metadata sees it.
- Migration: `superset/migrations/versions/` per convention
  (`YYYY_MM_DD_HHMM_<rev>_database_user_grants.py`), using
  `superset.migrations.shared.utils.create_table/create_index/create_fks_for_table`
  (template: `2025_12_18_0220_create_tasks_table.py`).

### 2.3 Claiming — one idempotent function, three triggers

`claim_database_grants(user)` (new `superset/commands/database_grants/claim.py`):

1. Normalize candidates: `{user.username.lower().strip(), (user.email or '').lower().strip()}`
   (DP-1: match username OR email — on SSO they're the same; matching both is free
   robustness for admin paste-style input).
2. Select all `database_user_grants` rows with `username IN candidates`.
3. For each: skip if DB row gone (FK cascade should prevent, but fail-soft);
   ensure `db_grant_<id>` role exists with the PVM; ensure role ∈ `user.roles`;
   stamp `user_id`/`claimed_at` if unset. Commit once. Never raise out of the
   auth path — log + swallow (a claiming bug must not break login).

Triggers (all idempotent, all in `BuilderSecurityManager` — already the deployment's
`CUSTOM_SECURITY_MANAGER`):

- **T1 — every successful login, all auth paths:** override
  `update_user_auth_stat(user, success=True)` → `super()` then claim. FAB calls it
  from `auth_user_db` (@1030), `auth_user_oauth` (@1469), `auth_user_remote_user`
  (@1407), LDAP. This also **re-heals after `AUTH_ROLES_SYNC_AT_LOGIN`**, which
  overwrites `user.roles` from the IdP mapping on each OAuth login (manager.py:1502)
  and would otherwise silently strip grant roles.
  ⚠️ Preflight P0 below: verify roles-sync happens *before* `update_user_auth_stat`
  in `auth_user_oauth`; if not, T3 is the backstop.
- **T2 — SSO auto-registration (first-ever sign-in):** override `add_user(...)` →
  `super()`; if a user was created, claim. Covers the "pre-approved before account
  exists" primary use case.
- **T3 — lazy claim on app load:** `GET /.../mine` (below) claims before returning.
  Covers grants issued mid-session and any auth-path ordering surprises.

Additionally, the **bulk-create command claims immediately** for usernames that already
have accounts (grant-after-first-sign-in case → access is live without waiting for the
next login; dialog appears on their next page load via T3's endpoint).

### 2.4 REST API — new `superset/database_grants/` package

`DatabaseGrantRestApi(BaseSupersetModelRestApi)`, `resource_name = "database_grant"`,
`class_permission_name = "DatabaseAccessGrant"`, `base_filters` none (admin-only for
CRUD; self-scoped endpoints enforce identity in code).

Admin endpoints (Admin-only automatically — the new permission name is in no
Gamma/Builder allow-list):

- `GET /api/v1/database_grant/` — FAB list; filters on database, username, status;
  list columns: database.database_name, username, status, created_by, created_on,
  claimed_at.
- `POST /api/v1/database_grant/` — `{database_id, usernames: [...]}` bulk upsert.
  Command normalizes (lower/strip/dedupe, drop empties), caps at 500/request,
  validates the database exists, skips duplicates (report `created` / `skipped`),
  immediately claims for existing users. `@protect()`, `@requires_json`, audit-logged.
- `DELETE /api/v1/database_grant/<id>` (+ bulk `?q=rison`) — **revoke**: if claimed,
  remove `db_grant_<database_id>` from that user's roles (only if they hold no other
  grant for the same DB — impossible under the unique constraint, so unconditional);
  delete the row. (DP-2: hard delete; audit trail lives in the event log.)

Self-service endpoints (any authenticated user, self-scoped by construction):

- `GET /api/v1/database_grant/mine` (`@permission_name("mine")`) — runs
  `claim_database_grants(current_user)` (T3), then returns this user's
  claimed-but-unacknowledged grants, each with the DB signature: `database_name`,
  `backend`, `driver`, `host`, `port`, `database`, `connection_username`,
  `granted_on`. Signature parsed server-side from `db.url_object`
  (= `make_url_safe(sqlalchemy_uri_decrypted)`, core.py:362) — password is never
  read into the payload; parse failures degrade to `database_name` + `backend` only.
- `POST /api/v1/database_grant/acknowledge` (`@permission_name("acknowledge")`) —
  `{ids: [...]}`, sets `acknowledged_at` on rows whose `user_id == current_user`
  only; foreign ids are ignored, not errors.

Builder access to the self endpooints: extend `_is_builder_pvm`
(`superset/security/builder.py`) with
`GRANT_SELF_PERMS = {"can_mine", "can_acknowledge"}` on view-menu
`"DatabaseAccessGrant"`. Admin gets everything by default.

Commands: `superset/commands/database_grants/{create.py,delete.py,claim.py,acknowledge.py,exceptions.py}`
with `@transaction()`, mirroring `superset/commands/security/` (RLS) structure.

### 2.5 Admin UI — new Settings page

- Backend registration: thin `DatabaseAccessGrantsView(BaseSupersetView)` (pattern:
  `RowLevelSecurityView`, `superset/views/sqla.py:30`) + `appbuilder.add_view(...,
  category="Security", ...)` in `superset/initialization/__init__.py` (~line 566
  block). FAB emits the menu entry only to roles holding the view perm → Admin-only
  menu for free (DP-3: Security category).
- Frontend page: `src/pages/DatabaseAccessGrantsList/` cloned from
  `src/pages/RowLevelSecurityList/index.tsx` — `useListViewResource('database_grant',...)`,
  `ListView` columns (database, username, status tag pending/claimed/acknowledged,
  granted by, granted on), row + bulk **Revoke** with `ConfirmStatusChange`.
- Grant modal (feature dir `src/features/databaseGrants/GrantAccessModal.tsx`):
  `DatabaseSelector` (`src/components/DatabaseSelector`, db-level props only) +
  `<Input.TextArea>` for pasted usernames. Client-side parse: split on
  newlines/commas/semicolons/whitespace, trim, lowercase, dedupe; show live count
  chip + the parsed list; POST bulk; toast `N granted, M already granted`.
- Route: lazy import + `{path: '/databaseaccessgrants/list/', Component: ...}` in
  `src/views/routes.tsx` (template @316-319).

### 2.6 User-facing persistent dialog

- `src/features/databaseGrants/DatabaseGrantNotice.tsx`, mounted in
  `src/views/App.tsx` beside `<ToastContainer />` (@118) — inside
  `RootContextProviders`, outside the `<Switch>`, so it renders on dashboards,
  Explore, **and SQL Lab** (single SPA; no separate sqllab webpack entry).
- On mount: skip if `state.user` is anonymous/missing; `GET .../mine`; if any
  unacknowledged grants → blocking `Modal` from `@superset-ui/core/components`
  with `maskClosable={false}` `closable={false}`, custom footer = single primary
  "Got it" button.
- Body: one card per grant — database display name, backend/driver, and the
  signature line `user@host:port/database`, plus granted-on date. Copy states
  plainly: "An administrator has granted you access to this database. You can use
  it in SQL Lab and MDL Lab without entering credentials."
- "Got it" → `POST .../acknowledge` → close. Ack is **server-persisted**
  (`acknowledged_at`) — survives devices/browsers; no localStorage (none of the
  existing dismiss patterns fit, and localStorage would re-nag on new machines or
  never nag after clearing).
- All fetch errors (incl. 403 for roles without the perm) fail silent — the notice
  must never break app load.
- Cadence (DP-5): fetch on SPA mount only. Mid-session grants surface on the next
  full page load; claiming itself is NOT gated on the dialog (T1/T2 and the
  create-command claim keep access live regardless).

### 2.7 ai_agent — no changes required (verified)

A granted user sees the **same connection row** (same `database_id`) as everyone else
on that DB, so `SemanticAccessService._require_project_permission` succeeds on the
direct `default_database_id` path (access.py:305-351) — fingerprint translation never
fires. Projects/documents/instructions/memory are keyed by database identity with
owner as audit-only. Permission level derives from access level (datasets visible →
write), not ownership.

Optional cosmetic follow-up (not in scope): the legacy scope-level provenance SSE
stream still owner-filters events (`sqlalchemy_store.py:437-456` /
`list_events`); the project-scoped stream is already shared. One-line parity fix if
ever needed.

## 3. Decision points (recommendations pre-selected; flag disagreement before build)

- **DP-1 Username matching:** match grant `username` against caller's `username` OR
  `email`, case-insensitive. *(Recommended — on SSO they coincide; costs nothing.)*
- **DP-2 Revoke semantics:** hard-delete grant row + remove role membership; rely on
  event log for audit. Alternative: `revoked_at` soft state. *(Recommended: hard.)*
- **DP-3 Menu placement:** Settings → Security. *(Recommended.)*
- **DP-4 Claim triggers:** all three (login-stat override + add_user override + lazy
  endpoint), each idempotent. *(Recommended — belt & braces against
  AUTH_ROLES_SYNC_AT_LOGIN clobbering and auth-path ordering.)*
- **DP-5 Dialog freshness:** on-mount fetch only, no polling/SSE. *(Recommended.)*

## 4. Sequential implementation checklist

Rules: complete in order; each step lists its tests; run them before ticking. `[B]`
= blocker for later steps.

- [x] **P0 (preflight)** DONE 2026-07-02. Verified in installed FAB
  (`flask_appbuilder/security/manager.py`): `auth_user_oauth` roles-sync (@1501)
  runs BEFORE `update_user_auth_stat` (@1523) — claim after auth-stat survives
  AUTH_ROLES_SYNC_AT_LOGIN. Better hook found: FAB's `on_user_login(user)` (@979,
  called from `update_user_auth_stat` @1026 on success, on ALL auth paths incl.
  SAML) — and Superset already overrides it (`superset/security/manager.py:973`,
  session stamp + audit). T1 therefore = `BuilderSecurityManager.on_user_login`
  chaining `super()` then claim, NOT `update_user_auth_stat`.
- [x] **S1 [B]** DONE. Model `superset/models/database_grant.py`
  (`DatabaseUserGrant` + `grant_role_name()` + `normalize_grant_username()`),
  imported from `superset/models/__init__.py`; migration
  `2026-07-02_10-00_5c1a0e6b9d42_create_database_user_grants.py`
  (revises `78a40c08b4be`; shared-utils helpers; FKs: dbs CASCADE, ab_user
  SET NULL ×3; unique (database_id, username)). 7 tests in
  `tests/unit_tests/models/database_grant_test.py`.
- [x] **S2 [B]** DONE. `superset/commands/database_grants/claim.py`:
  `grant_candidates` (username+email, lowercased), `ensure_grant_role`
  (find-or-create role/permission/view-menu/PVM via plain session — commits
  atomically with the claim; NOTE: the `database_access` PVM normally already
  exists, created by `database_after_insert`), `_claim_grant`,
  `claim_database_grants(user, session=None, commit=True)` — never raises,
  rolls back on failure. 10 tests in
  `tests/unit_tests/database_grants/claim_test.py` (incl. IdP roles-wipe
  re-heal and error-swallow).
- [x] **S3** DONE. `BuilderSecurityManager`: T1 = `on_user_login` override
  (per P0 finding — cleaner than update_user_auth_stat; chains Superset's
  session-stamp/audit super()), T2 = `add_user` override (\*args/\*\*kwargs
  passthrough), `GRANT_SELF_PERMS = {can_mine, can_acknowledge}` branch in
  `_is_builder_pvm`. ALSO: added `"DatabaseAccessGrant"` +
  `"Database Access Grants"` to `ADMIN_ONLY_VIEW_MENUS` in core
  `superset/security/manager.py` — without this, `_is_gamma_pvm` would have
  defaulted the new view into Gamma and every Builder would have had grant
  MANAGEMENT (found during S3; test pins it). Tests extended in
  `tests/unit_tests/security/builder_role_test.py`.
- [x] **S4** DONE. `superset/commands/database_grants/`:
  `create.py` `BulkCreateDatabaseGrantsCommand` (normalize/dedupe/cap-500,
  DatabaseDAO.find_by_id defense-in-depth, skip-dupes reporting, immediate
  claim for existing accounts matching username OR email),
  `revoke.py` `RevokeDatabaseGrantCommand` (detach role membership if
  claimed, delete row; role row itself survives for other holders),
  `acknowledge.py` `AcknowledgeDatabaseGrantsCommand` (self-scoped,
  idempotent, foreign ids ignored), `exceptions.py`. 8 tests in
  `commands_test.py`.
- [x] **S5** DONE. `superset/database_grants/{api.py,schemas.py,utils.py}`;
  `DatabaseGrantRestApi` (resource `database_grant`, class perm
  `DatabaseAccessGrant`): GET/GET_LIST/INFO, custom POST (bulk), DELETE +
  rison bulk_delete, `GET /mine` (@permission_name("mine"), runs T3 claim,
  returns signature via `database_signature()` — parsed from the STORED
  password-masked URI so the secret is never read), `POST /acknowledge`.
  Registered in `superset/initialization/__init__.py`. `mine`
  list also exposes `changed_on_delta_humanized` for the admin ListView sort.
  10 tests in `api_test.py` (incl. password-never-in-response and
  self-scoping).
- [x] **S6** DONE. `superset/views/database_grants.py`
  `DatabaseAccessGrantsView` (piggybacks can_read on DatabaseAccessGrant, RLS
  pattern) + Security-category menu entry (icon fa-key) in initialization.
  2 tests in `view_test.py` (auth bounce + SPA dispatch; note: harness
  reloads superset.views.base, so the render patch must target the view
  module's own BaseSupersetView reference).
- [x] **S7** DONE. `src/pages/DatabaseAccessGrantsList/` (ListView: username,
  database, derived status Tag pending/claimed/acknowledged, granted-by,
  last-modified sort, revoke + bulk revoke w/ ConfirmStatusChange; hidden
  `database` column backs the relation filter — ListView requires filter ids
  among column accessors), `src/features/databaseGrants/`
  ({types,utils,GrantAccessModal}.ts[x]) — modal = DatabaseSelector + paste
  textarea + live unique-count + R2 trust-warning Alert; route
  `/databaseaccessgrants/list`. 15 jest tests.
- [x] **S8** DONE. `DatabaseGrantNotice` mounted beside `<ToastContainer />`
  in `src/views/App.tsx`: fetches `/mine` once per app load (skips
  anonymous), blocking Modal (closable=false, maskClosable=false), per-grant
  card `user@host:port/database (backend)` + granted-on, "Got it" POSTs
  acknowledge; ALL failure paths silent; failed ack keeps dialog for retry.
  6 jest tests.
- [x] **S9** DONE. ruff + ruff-format + mypy + prettier clean (one C901
  complexity fix in claim.py); backend sweep 526 passed (security + models +
  database filters + grants); FE sweep 23 passed + tsc clean; this doc +
  memory updated.

## 5. Risks / expectation gaps

- **R1 AUTH_ROLES_SYNC_AT_LOGIN clobber** — mitigated by idempotent re-claim on
  every login (T1) + lazy claim (T3). Residual: between an OAuth roles-sync and the
  next claim trigger there is no window in practice (same request), but document it.
- **R2 Identity trust** — grants key on username string. Safe ONLY because the
  deployment restricts account creation to SSO (spoof-proof). On a deployment with
  open self-registration, a squatter could register a pre-approved username and
  inherit access. Surfaced in the grant modal's warning Alert + this doc.
- **R3 Role-edit drift** — an admin manually removing `db_grant_*` from a user in
  the FAB roles UI will be silently undone at next login (claim re-heals). Revoke
  must go through the grants panel.
- **R4 Orphaned grant roles** — deleting a database cascades grant rows and FAB
  drops the view-menu, but the empty `db_grant_<id>` role row may linger on users.
  Harmless (no PVMs). Optional cleanup in `database_after_delete` if it bothers.
- **R5 Mid-session grants** — access is live immediately (create-command claim),
  but the dialog waits for the next full page load (DP-5). Acceptable per spec
  ("first sign in or first encounters this grant").
- **R6 Signature exposure** — host/port/db/connection-username shown to grantee by
  explicit requirement; password never leaves the server (signature parses the
  stored, already-masked URI). Broader than `DatabaseRestApi.show_columns` —
  deliberate, self-scoped, admin-initiated.

## 6. As-built residual gaps (UI expectation vs implementation)

- **G1 Dialog timing**: the notice appears on the next FULL page load after a
  grant (SPA route changes don't re-fetch `/mine`). Access itself is already
  live. If "instant" notification is ever needed → poll or SSE (not built).
- **G2 Multi-grant acknowledge is all-or-nothing**: one "Got it" acknowledges
  every listed grant; there is no per-grant dismiss. Matches the spec's single
  persistent dialog.
- **G3 Admin list shows lifecycle, not login history**: "Claimed" means the
  role is attached, not that the user has recently logged in.
- **G4 Grant modal's DatabaseSelector lists the databases the ADMIN can see**
  (all of them, since Admin bypasses the filter). Fine for the intended
  Admin-only panel; a hypothetical delegated-granter role would be scoped by
  DatabaseFilter automatically (create-command uses DatabaseDAO).
- **G5 No audit surface for revokes** beyond the event log (`DELETE` is
  event-logged); the grant row is hard-deleted per DP-2.
- **G6 `mine` claims on EVERY app load** (one cheap indexed query when the
  user has no grants). If a deployment has millions of grant rows this is
  still one `IN (2 candidates)` indexed lookup — fine.
- **G7 Vanilla-Gamma deployments**: only Builder (and Admin) get
  can_mine/can_acknowledge; a plain-Gamma user with a grant would get access
  claimed at login (T1/T2) but never see the dialog (mine 403 → silent).
  Intended for this deployment (all users are Builder); note if role strategy
  changes.
