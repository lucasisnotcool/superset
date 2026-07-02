# Self-Service Connections & Redefined Gamma — Feature Spec

**Status:** Draft for review · **Author:** (generated) · **Date:** 2026-07-02
**Scope:** Apache Superset core RBAC + `superset_ai_agent` integration

---

## 1. Summary

Redefine the non-admin (`Gamma`) role from a **read-only consumer** into a **self-service builder**. A normal user should be able to:

1. **Bring their own database credentials** — create their own Superset `Database` connections.
2. **See only their own connections** — never another user's connection row or credentials — while **Admins see all**.
3. **Use SQL Lab, MDL Lab, MDL Copilot, and the AI SQL agent** out of the box.
4. **Share MDL (semantic-layer) projects** that are associated with a database they own/can-reach.

The central finding of the codebase analysis is that **the AI-agent side already implements the model you want**: MDL project access is *already* derived from live Superset database-access (per user session), not from ownership. The work is therefore concentrated in **Superset core**: (a) let non-admins create connections, (b) make connections owner-scoped, and (c) grant the SQL Lab permission bundle. Once connections are owner-scoped, MDL project privacy/sharing falls out of the existing machinery for free.

A second key finding, from industry research, reshapes *how* we honor "prove access by providing valid credentials": using a **live credential re-test as an ongoing authorization gate is a recognized anti-pattern** (conflates authentication with authorization; over-grants; goes stale on revocation; confused-deputy risk). We honor the *spirit* of the intent — you prove access by successfully creating a validated connection (a one-time authentication at creation) — and anchor durable authorization on **connection ownership + explicit grants**, which is exactly what Superset and every peer BI tool does.

---

## 2. Goals / Non-Goals (dev intent)

### Goals
- G1. Non-admin users create, edit, delete, and test **their own** DB connections (BYO credentials).
- G2. Connection listing/detail is **owner-scoped** for non-admins; **Admins see all**. Credentials never leak across users.
- G3. Every non-admin gets **SQL Lab + MDL Lab + MDL Copilot + AI SQL agent** by default.
- G4. **MDL projects are private** to the owner of the underlying connection and **shareable** by explicit grant.
- G5. Changes are delivered via **sanctioned extension points** (`CUSTOM_SECURITY_MANAGER`, a `DatabaseFilter` change, an `owners` relation) — no fork of FAB internals.

### Non-Goals
- NG1. Per-query, per-end-user credential delegation / OAuth pass-through (Snowflake/BigQuery SSO). Valuable but a separate, larger effort (see §9, Future).
- NG2. Replacing Superset's `SECRET_KEY`/AES field encryption with KMS envelope encryption. Noted as a hardening follow-up (§8.6), not required for v1.
- NG3. Changing how the AI agent executes SQL (it already runs as the calling user via `/api/v1/sqllab/execute/`).
- NG4. Multi-workspace / hard tenant isolation (separate metadata DBs per tenant, à la Preset). Out of scope; we use owner-scoping within one instance.

---

## 3. User Intent → Flows → UI

### Flow A — "Connect my database"
1. Non-admin opens **Settings → Database Connections** (or the SQL Lab "+ Database" affordance) and clicks **+ Database**.
2. Enters a SQLAlchemy URI / host+creds. Superset **validates** the connection (existing `TestConnectionDatabaseCommand`), encrypts the password, and saves.
3. The connection is **auto-owned** by the creator; the creator (and Admins) can see it. No other non-admin can.
4. **UI:** the existing `DatabaseModal` and `DatabaseList` are reused unchanged; the list is simply now non-empty for non-admins and scoped to their own rows. No new screen required for v1.

**Intent alignment:** "bring your own credentials" == create your own validated connection. The validation step *is* the credential proof, performed once, at the natural moment.

### Flow B — "Explore / query my data"
1. User opens **SQL Lab** (now visible in the nav). Their databases populate the schema browser.
2. The **AI SQL agent** panel and **MDL Lab / MDL Copilot** panels are present (they already mount inside SQL Lab with no separate gate).
3. Queries run **as the user** — Superset's per-object `raise_for_access` still governs which physical DB/schema they may hit (they may hit their own connections).

### Flow C — "Build & share a semantic (MDL) project"
1. In **MDL Lab / MDL Copilot**, the user authors an MDL project bound to one of their databases (`default_database_id` + `database_uri_fingerprint` + schema set).
2. The project is **private by default** — visible only to principals who can reach that database in Superset (currently just the owner + Admins).
3. To **share**, the user (or an Admin) grants another user access to the underlying connection (v1: grant `database_access`; v1.5: an explicit project-level grant). The shared user then sees the project through the *existing* `db_access` visibility logic.
4. **UI:** a "Share" action on the connection (add a user as co-owner / grant viewer) and/or on the MDL project. See §7-D1 for the recommended sharing primitive.

---

## 4. Current-State Architecture (grounded)

> All findings below were verified against the working tree. Citations are `path:line`.

### 4.1 Superset core — connections are admin-owned, not owner-scoped
- `Database` has **no `owners` relationship** — only `created_by`/`changed_by` audit stamps from `AuditMixinNullable` (`superset/models/core.py:208`, `superset/models/helpers.py:574`). Contrast `Slice`/`Dashboard`, which do have `owners`.
- **`can_write` on `Database` is Admin-only.** `Database ∈ READ_ONLY_MODEL_VIEWS` (`superset/security/manager.py:704`); `_is_admin_only` marks any non-read permission on those views admin-only (`manager.py:2015-2019`), so `_is_gamma_pvm`/`_is_alpha_pvm` both exclude it. Alpha/Gamma get only read-side PVMs (Alpha additionally gets `can_upload` via `ALPHA_ONLY_PMVS`, `manager.py:777`).
- **Visibility is by permission, not owner.** `DatabaseFilter` (`superset/databases/filters.py:41-76`) returns all rows for `all_database_access` holders (Admin/Alpha), else filters to databases whose `database_access`/`schema_access`/etc. PVM the user holds. There is **no owner-based scoping** today.
- The per-DB PVM `[name].(id:N)` is auto-created on insert via `database_after_insert` (`manager.py:2152-2172`).
- **Credentials are already safe at rest and on read:** `password`/`encrypted_extra`/`server_cert` are AES-encrypted columns (`superset/models/core.py:220,246,248`; `superset/utils/encrypt.py`), the stored URI carries `PASSWORD_MASK` (`models/core.py:465-472`), and API responses only ever emit masked values (`databases/schemas.py`, password fields are `load_only`).

### 4.2 AI agent — MDL access is ALREADY database-access-derived
- A `SemanticProject` is bound to a database via `default_database_id`, `database_uri_fingerprint`, `catalog_name`, and a `schema_names` set (`superset_ai_agent/semantic_layer/schemas.py:218-272`).
- **`owner_id` on a project is audit-only.** `_is_visible` and `_with_permission` explicitly discard it; a project with `visibility="db_access"` is "visible to anyone who can reach that database" (`semantic_layer/projects.py:858-874`; `SqlAlchemySemanticProjectStore.list` at `:490-507`).
- The **single real enforcement point** is `load_context` (`superset_ai_agent/app.py:582-608`), which calls the Superset context provider **using the user's own session** (`superset_auth_mode="user_session"`, `config.py:348`). "Can this user reach this DB/schema?" is answered by Superset returning datasets under the user's session. `SemanticAccessService` (`semantic_layer/access.py:82-355`) turns that into read/write (FULL context → `write`, PARTIAL → `read`).
- **The credential-proof scaffolding is dormant.** `db_uri_match` mode, `supplied_uri` validation, `SemanticAccessProof`, and `AiAgentSemanticProjectGrant` exist as models/config but are **never wired** (`access.py:318-341`, `persistence/models.py:312-341`). `database_uri_fingerprint` (`semantic_layer/uri_fingerprint.py`) is used only as a cross-instance identity key, computed from the Superset-stored URI.

### 4.3 Lab surfaces — SQL Lab access is the de-facto gate for everything
- SQL Lab is gated by `SQLLAB_ONLY_PERMISSIONS` (`manager.py:816-847`); `_is_gamma_pvm` explicitly excludes them (`manager.py:2096`), which is why default Gamma has no SQL Lab.
- **MDL Lab / MDL Copilot / AI SQL agent have no Superset feature flag and no permission gate** — they mount unconditionally inside SQL Lab (`superset-frontend/src/SqlLab/components/AppLayout/index.tsx:141`). So **SQL Lab access == access to all three**.
- The AI SQL agent executes SQL **as the calling user** via `/api/v1/sqllab/execute/` (`superset_ai_agent/integrations/superset/rest.py:259`), so SQL Lab permissions + per-DB access are a hard runtime dependency, not just UI visibility.
- The `AiAgent` FAB permission (for the DB-identity helper `superset/ai_agent/api.py`) is **already granted to Gamma** by default.
- The only admin-gated AI surface is the **"AI Agent Usage"** telemetry menu (`initialization/__init__.py`, `views/routes.tsx:353`).
- **Sanctioned customization path:** `CUSTOM_SECURITY_MANAGER` (`config.py:219`, wired at `initialization/__init__.py:971`). `set_role` recomputes each built-in role from its predicate on every `sync_role_definitions`, so **editing Gamma in the UI is not durable** — a subclass is the durable path.

### 4.4 Industry benchmark (who does user-owned connections, and how)
- **User-owned connections exist in Tableau (Personal Space), Power BI (My Workspace), and Hex (project-scoped connections).** Admin-owned-only: Metabase, Redash, **Superset**, Looker, Mode, Sigma, Preset. So this feature moves Superset into the Tableau/Power BI/**Hex** self-service camp — a deliberate departure from its lineage. **Hex's project-scoped connection** is the closest analogue.
- **Artifact ≤ source invariant:** every semantic-layer product (dbt, Cube, Looker, Metabase) enforces that a derived artifact can only ever expose a **subset** of what the requesting user can already reach in the source; a shared model never *grants* new DB access. Our design must preserve this.
- **Anti-pattern to avoid:** a live connection/credential test as an authorization gate conflates authN with authZ (OWASP A01), over-grants at object level, and goes stale on revocation. Legitimate delegation passes the *user's own identity* to the DB per query; it does not cache a boolean "auth succeeded."
- **OWASP API1 (BOLA):** possessing valid credentials to physical DB-A does **not** entitle you to another user's *connection/project object* built on DB-A — those are two distinct authorization boundaries. Enforce object-level ownership on **every** access (read/update/delete/test/export), deny-by-default, secrets write-only, UUIDs as defense-in-depth only, cross-tenant tests as a merge gate.

---

## 5. Proposed Design

Three pillars. Pillars A and B are Superset-core; Pillar C is mostly "confirm the existing behavior + add a sharing primitive."

### Pillar A — Redefine Gamma as a self-service builder role
Deliver via a `CUSTOM_SECURITY_MANAGER` subclass that overrides class attributes/predicates so `sync_role_definitions` recomputes Gamma with the new grants. Two capability additions:

**A1. Grant the SQL Lab bundle to Gamma.** Stop `_is_gamma_pvm` from excluding `SQLLAB_ONLY_PERMISSIONS`. Cleanest override: subclass and set the predicate to include the SQL Lab set (or, equivalently, move the SQL Lab bundle out of the "gamma-excluded" branch for our deployment). This grants open/run/results + `TabStateView`/`TableSchemaView`/`SavedQuery` editor perms. This single change unlocks SQL Lab **and** MDL Lab/Copilot/AI SQL agent (§4.3).

**A2. Grant `Database` create/write to Gamma — but NOT `all_database_access`.** Reclassify `("can_write","Database")` (and `can_add`/`can_edit`/`can_delete` as needed for the CRUD flow) so it is no longer admin-only *for our deployment*. Because `Database ∈ READ_ONLY_MODEL_VIEWS` and `∈ GAMMA_READ_ONLY_MODEL_VIEWS`, the subclass must override **both** gates (`_is_admin_only` via `READ_ONLY_MODEL_VIEWS`, and `_is_alpha_only` via `GAMMA_READ_ONLY_MODEL_VIEWS`) — see §4.1 and the DB/RBAC analysis. **Critically, do not grant `all_database_access`** (it stays `ALPHA_ONLY_PERMISSIONS`, `manager.py:801`), or owner-scoping (Pillar B) is bypassed.

> Decision point D2 (§7): reclassify inside built-in **Gamma**, or mint a **new named role** ("Builder"/"Contributor") and set it as `AUTH_USER_REGISTRATION_ROLE`. **Recommendation: new named role**, leaving stock Gamma semantics intact.

### Pillar B — Owner-scope database connections
**B1. Add owner scoping to `DatabaseFilter`.** Mirror the `ChartFilter` pattern (`superset/charts/filters.py:103-128`): early-return the full query for privileged principals (`security_manager.can_access_all_databases()` — Admin/Alpha), otherwise constrain to the caller's own connections. v1 constraint: `created_by_fk == get_user_id()` **OR** the existing `database_access` PVM membership (so admin-granted shares still work). This is the load-bearing security change; see risks §8.1.

**B2. Auto-grant the creator access to their new connection.** On create, ensure the creator can see their own DB. The per-DB `database_access` PVM already auto-creates on insert (`manager.py:2152`); add a step in the create path to attach that PVM to the creator (or rely solely on the `created_by` branch in B1 — see D1). Result: creator + Admins see it; nobody else does.

**B3. Reaffirm credential non-disclosure (mostly already true).** Passwords are encrypted and masked on read (§4.1). Harden the *edges* OWASP flags (§8.3): scrub credentials from **error messages**, **export/import**, and **clone/duplicate** payloads; keep the `load_only` write-only pattern; confirm `GET /<pk>/connection` stays admin/owner-only.

**B4. (Decision D3) Add a real `owners` M2M to `Database`** for shareability, or stay with `created_by`. **Recommendation:** add `owners` (migration + relation), matching Chart/Dashboard, because it makes sharing a first-class, multi-user operation and avoids overloading `database_access` PVMs for people-sharing. `created_by` remains the auto-first-owner.

### Pillar C — MDL project sharing follows database-access (already built)
- **No new enforcement needed for privacy.** Once Pillar B scopes connections by owner, MDL projects on a user's DB are automatically private: only the owner (and Admins) can reach the DB via their session, so only they resolve the project's datasets (§4.2). The `visibility="db_access"` logic does the rest.
- **Sharing = granting access to the underlying connection.** When user X is added as an owner of (or granted `database_access` on) user A's connection, X's Superset session now returns that DB's datasets, so `load_context` grants X read/write on the MDL projects for that DB — through the existing path, no new agent code.
- **Optional v1.5 — explicit project grants.** Wire the dormant `AiAgentSemanticProjectGrant` table (`persistence/models.py:312`) so a project can be shared to specific users *without* granting full connection access, if finer granularity is wanted. Defer unless product needs it.

### The credential-proof reframing (honoring intent, avoiding the anti-pattern)
Your phrase "prove they have access by providing valid db credentials" is realized as:
- **At creation:** the user provides credentials; Superset validates the connection (`TestConnectionDatabaseCommand`). This one-time authentication is the proof of access to the *physical* database. ✔ Honors intent.
- **Durably:** authorization is anchored on **ownership of the connection object** (+ explicit shares), re-checked object-by-object on every request. ✔ Avoids the OWASP A01 anti-pattern.
- **We explicitly do NOT** auto-grant user B access to user A's connection/MDL project merely because B could also authenticate to the same physical DB. B may create *their own* connection to that DB (their own creds, their own owned object, their own private MDL projects). Shared *objects* require an explicit grant. This is the OWASP API1 (BOLA) boundary and the "artifact ≤ source" invariant. See decision D1.

---

## 6. Data & API changes (concrete)

| Area | Change | File(s) |
|---|---|---|
| Security manager | `CUSTOM_SECURITY_MANAGER` subclass: include SQL Lab bundle in the new role; reclassify `Database` write out of admin-only for the role; **withhold** `all_database_access` | new `superset/<deploy>/security.py`; config `CUSTOM_SECURITY_MANAGER` |
| Role registration | Set `AUTH_USER_REGISTRATION_ROLE` (and default assignment) to the new builder role | `superset_config.py` |
| DB visibility | Owner-scope `DatabaseFilter.apply` (privileged early-return + `created_by`/owners/PVM constraint) | `superset/databases/filters.py:41` |
| DB ownership | (D3) `owners` M2M on `Database` + Alembic migration; wire into create command + API `add_columns`/`edit_columns`/related filters | `superset/models/core.py`, `superset/commands/database/create.py`, `superset/databases/api.py` |
| Creator grant | Attach `database_access` PVM (or owner row) to creator on insert | `superset/commands/database/create.py` / `manager.database_after_insert` |
| Secret edges | Scrub creds from errors/export/clone; confirm masked read paths | `superset/databases/{api,schemas,commands}` |
| MDL sharing (v1.5, optional) | Wire `AiAgentSemanticProjectGrant` read/write into `SemanticAccessService` | `superset_ai_agent/semantic_layer/access.py`, `persistence/models.py` |
| Frontend | (Optional) "Share" action on Database list/modal; surface owners column | `superset-frontend/src/features/databases/*` |

No change is needed to mount the AI panels (already ungated) or to how the agent executes SQL (already user-session).

---

## 7. Decision Points (with recommendations)

**D1 — Sharing model: object-ownership vs. same-physical-DB.**
Should user B automatically access user A's connection/MDL project if B can independently authenticate to the same physical database (matched by `database_uri_fingerprint`)?
- **Option 1 (recommended): No — strict object ownership + explicit sharing.** Fingerprint is *identity only*, never an auto-grant. B creates their own connection or A/Admin explicitly shares. Aligns with OWASP API1 (BOLA) and the "artifact ≤ source" invariant; avoids leaking A's stored creds/cached results/derived data to B.
- **Option 2: Yes — fingerprint-based auto-sharing.** Any user who can prove creds to DB-A sees all MDL projects on DB-A. Closest to the literal phrasing of the request, but this is the flagged anti-pattern (it re-tests credentials as an authz gate and cross-shares objects). **Not recommended.**
- *Recommendation:* **Option 1.** It satisfies the intent ("access derives from having database access") while keeping the security boundary correct. Fingerprint remains the mechanism that lets *shared* or *same-owner* projects line up across connections.

**D2 — Redefine built-in Gamma vs. new named role.**
- **Recommended: new role** ("Builder"/"Contributor") set as `AUTH_USER_REGISTRATION_ROLE`. Preserves stock Gamma (useful for pure viewers/embedded), makes the capability grant explicit and greppable, and reduces blast radius if we later host mixed personas.
- Alternative: mutate Gamma in the subclass. Simpler mental model ("all users are Gamma") but overloads a well-known name and complicates any future viewer-only persona.

**D3 — `owners` M2M vs. `created_by` for connection ownership.**
- **Recommended: add `owners` M2M** (mirrors Chart/Dashboard, first-class multi-user sharing) with `created_by` as the automatic first owner. Requires a migration.
- Alternative (faster v1): `created_by` + `database_access` PVM as the sharing lever, no schema change. Ship this if we want a minimal first cut, then add `owners` in v1.5.

**D4 — Guardrails on what users may connect to.**
Self-service connections can point at internal/metadata hosts (SSRF-adjacent). Decision: enforce an **operator allow/deny list** for hosts/ports and disable dangerous engine options by default (`allow_dml`, `allow_ctas`, file upload). *Recommendation: yes, ship with a deny-list for loopback/link-local/metadata IPs and DML/CTAS off by default (they already default off, `models/core.py:229-231`).*

**D5 — Credential encryption hardening.**
Keep Superset's `SECRET_KEY`/AES field encryption for v1 (adequate, already in place) vs. move to KMS envelope encryption with per-tenant keys.
- *Recommendation:* v1 keeps existing encryption + ensures `SECRET_KEY` is injected from a secrets manager and `re-encrypt-secrets` rotation is documented. KMS envelope + per-user DEKs is a hardening follow-up (§8.6), not a v1 blocker.

---

## 8. Risks & Mitigations

**8.1 (Critical) Missing/incorrect owner filter → cross-user connection leak (OWASP API1/BOLA).**
The `DatabaseFilter` change is the load-bearing control. A single wrong clause exposes every user's connections.
- *Mitigations:* deny-by-default in the filter; derive the principal from the session, never the request; add object-level checks on **every** DB route that takes an id (get/edit/delete/test/**export**/`/connection`); write cross-user access tests that must return 0 rows / 403 and make them a **merge gate** (OWASP API1 rec #4); prefer UUID exposure for DB ids as defense-in-depth (repo is already migrating toward UUIDs).

**8.2 Alpha/`all_database_access` bypass.** Any role holding `all_database_access` sees every connection, silently defeating owner-scoping. *Mitigation:* the builder role must never receive `all_database_access`; add a test asserting the role's PVM set excludes it.

**8.3 Credential leakage via edges (OWASP API3).** Masked GET is safe, but export/import, clone, and **error/stack messages** can echo a DSN with an embedded password. *Mitigations:* cherry-pick response properties; scrub secrets from errors/logs/exports/clones; schema-validate outbound payloads so the secret field cannot serialize; keep password fields `load_only`. (Superset has a documented history of credential-harvesting via exposed connection data — treat this as high priority.)

**8.4 Connection-pool / engine cache contamination.** If engines are cached keyed only by DB coordinates, user B could execute through user A's authenticated engine. *Mitigation:* ensure any cached engine/pool is keyed per `(owner, database_id)`, never by host/name alone; never reuse a connection across principals.

**8.5 Shared physical DB confusion (OWASP API1 boundary).** Two users' separate connections to the same DB must remain separate objects with separate private MDL projects. *Mitigation:* implement D1-Option-1; fingerprint is identity, not an authorization grant; object-ownership checked per request.

**8.6 Self-service SSRF / dangerous engines (D4).** Users could target internal hosts or enable DML/CTAS. *Mitigations:* host allow/deny list; DML/CTAS/file-upload off by default; per-DB `expose_in_sqllab` respected; async/impersonation reviewed.

**8.7 Role recompute wipes manual edits.** `sync_role_definitions` replaces `role.permissions` wholesale (`manager.py:2001`). *Mitigation:* all grants must live in the `CUSTOM_SECURITY_MANAGER` subclass, never in ad-hoc UI edits.

**8.8 Auth stays fresh on revocation.** Because authZ is object-ownership (not a cached credential test), revoking a share / deleting a connection takes effect immediately — no stale "proof" window. (This is *why* we avoid D1-Option-2.) Note the AI-agent's `_auth_context_cache` TTL (`app.py:573`) is a short introspection cache keyed by owner; confirm its TTL is acceptable for de-share latency.

---

## 9. Phased Delivery

**Phase 0 — Decisions.** Resolve D1–D5 (crux: D1, D2, D3). Small design review.

**Phase 1 — Role & access unlock (no schema change).**
- `CUSTOM_SECURITY_MANAGER` subclass: SQL Lab bundle + `Database` write to builder role; withhold `all_database_access`.
- Owner-scope `DatabaseFilter` via `created_by` + PVM (D3 minimal path).
- Auto-grant creator on insert.
- Tests: role PVM set; cross-user list/detail returns 0/403; SQL Lab + AI agent smoke.

**Phase 2 — First-class ownership & sharing.**
- (D3) `owners` M2M + migration; wire create command, API columns, related filters, FE owners surfacing.
- "Share connection" UX.
- Secret-edge scrubbing (errors/export/clone) + schema-validated responses.

**Phase 3 — MDL sharing polish (optional).**
- Confirm db_access-derived privacy end-to-end with owner-scoped connections.
- (Optional) wire `AiAgentSemanticProjectGrant` for project-level shares without full connection access.

**Phase 4 — Hardening (follow-up).**
- D4 host allow/deny + engine guardrails.
- D5 KMS envelope encryption / per-user DEKs; `SECRET_KEY` from secrets manager + rotation runbook.
- Audit logging on owner-scoped connection/project access; enumeration/oracle-response review (uniform 403/404).

---

## 10. Testing Strategy
- **Authorization (merge gate):** matrix tests — builder-A cannot see/edit/delete/test/export builder-B's connection (0 rows / 403); Admin sees all; builder cannot obtain `all_database_access`.
- **Secret non-disclosure:** GET/list/export/clone/error paths never contain a plaintext password or unmasked DSN (schema assertion).
- **MDL privacy:** builder-A's MDL project is invisible to builder-B until an explicit share; after share, B gets exactly read/write per the db_access mapping.
- **SQL Lab + agent functional:** builder can open SQL Lab, run a query against their own DB, and the AI SQL agent executes as them.
- **Role sync durability:** re-running `superset init` preserves the builder grants (they live in the subclass).
- Prefer unit/integration tests over E2E per repo guidance; add cross-tenant tests to CI.

---

## 11. Open Questions
1. D1 confirmation — is strict object-ownership sharing (recommended) acceptable, or is same-physical-DB auto-sharing a hard product requirement? (Shapes the whole security model.)
2. Do we want per-project grants (v1.5) or is connection-level sharing sufficient?
3. What is the operator policy for connectable hosts (D4)? Any internal networks that must be denied?
4. Registration: keep self-registration off and have Admins invite, or enable `AUTH_USER_REGISTRATION` with the builder role as default?

---

### Appendix — Key file references
Superset core: `superset/security/manager.py` (`704`, `816-847`, `1886-1889`, `2015-2019`, `2083-2098`, `2152`), `superset/databases/filters.py:41`, `superset/models/core.py:208,220,465`, `superset/charts/filters.py:103`, `superset/commands/database/create.py`, `superset/config.py:219`, `superset/initialization/__init__.py:971`.
AI agent: `superset_ai_agent/semantic_layer/access.py:82-355`, `semantic_layer/projects.py:858-874`, `app.py:582-608`, `config.py:348`, `integrations/superset/rest.py:259`, `persistence/models.py:312-341`.
Frontend: `superset-frontend/src/SqlLab/components/AppLayout/index.tsx:141`, `superset-frontend/src/features/databases/*`.
