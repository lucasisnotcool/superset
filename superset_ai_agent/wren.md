<!--
Licensed to the Apache Software Foundation (ASF) under one or more
contributor license agreements.  See the NOTICE file distributed with
this work for additional information regarding copyright ownership.
The ASF licenses this file to You under the Apache License, Version 2.0
(the "License"); you may not use this file except in compliance with
the License.  You may obtain a copy of the License at

   http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
-->

# Wren-Style Conversational Analytics Implementation Plan

This document describes how to extend the standalone `superset_ai_agent` service
with Wren-style conversational analytics artifacts and a document-driven
semantic-layer authoring workflow while preserving Superset governance.

The core rule is unchanged: Wren can improve context, examples, memory, semantic
planning, and reviewed semantic-layer metadata, but SQL execution must continue
to go through the configured Superset `SupersetClient`. The recommended governed
execution adapter is the REST SQL Lab adapter.

## Plan Precedence

This document has evolved from the initial conversational analytics plan into a
schema-scoped semantic-layer product plan. When sections conflict, use this
precedence:

1. Governance invariants always win.
2. Sections 14 through 24 are the finalized direction for Wren semantic-layer
   scoping, persistence, access, and UI.
3. Earlier scope-based document routes and the existing AI-panel
   `SemanticLayerDrawer` describe transitional baseline behavior only.
4. The target UI for semantic-layer CRUD is the SQL Lab
   `SemanticLayerEditor` tab opened from a database/schema node.
5. The AI panel remains the conversational analytics and artifact surface; it
   should show semantic-layer status and an "Open semantic layer" action, not
   own MDL file CRUD.

## Confirmed Current Code Facts

The current agent service is implemented in these files:

| Area | Current file/class |
| --- | --- |
| FastAPI app and dependency wiring | `superset_ai_agent/app.py::create_app` |
| Runtime configuration | `superset_ai_agent/config.py::AgentConfig` |
| One-shot text-to-SQL graph | `superset_ai_agent/graph.py::TextToSqlGraph` |
| Conversation graph | `superset_ai_agent/conversation_graph.py::ConversationGraph` |
| Conversation reflection | `superset_ai_agent/conversation_graph.py::SqlReflection`, `_reflect_sql_outcome` |
| One-shot API schemas | `superset_ai_agent/schemas.py` |
| Conversation schemas | `superset_ai_agent/conversations/schemas.py` |
| Conversation store | `superset_ai_agent/conversations/store.py`, `memory.py` |
| Superset client protocol | `superset_ai_agent/integrations/superset/client.py::SupersetClient` |
| REST adapter | `superset_ai_agent/integrations/superset/rest.py::SupersetRestClient` |
| MCP adapter | `superset_ai_agent/integrations/superset/mcp.py::SupersetMcpClient` |
| Adapter factory | `superset_ai_agent/integrations/superset/factory.py::create_superset_client` |
| SQL read-only validation | `superset_ai_agent/tools/sql.py::validate_read_only_sql` |
| SQL Lab AI frontend API | `superset-frontend/src/SqlLab/components/AiAgentPanel/api.ts` |
| SQL Lab AI frontend panel | `superset-frontend/src/SqlLab/components/AiAgentPanel/index.tsx` |

## Codebase Source Map And Completion Status

Use this section as the implementation-session refresher. It records what is
already present in the repository and what still needs to be closed before the
Wren semantic-layer increment should be considered production-ready.

Legend:

- `[COMPLETE]` means the repository has an implemented baseline and tests or
  source coverage matching the plan's intent.
- `[PARTIAL]` means the repository has useful code, but the risk is not fully
  closed or the product behavior still differs from the finalized direction.
- `[TODO]` means the item remains implementation work.

| Status | Capability | Codebase source |
| --- | --- | --- |
| [COMPLETE] | Optional conversational artifact response fields: summary, insight cards, chart spec, data preview, audit, follow-ups, Wren context. | `superset_ai_agent/schemas.py::AgentQueryResponse`, `ExecutionResult`, `InsightCard`, `ChartSpec`, `AuditInfo`, `WrenContextArtifact`; `superset_ai_agent/conversations/schemas.py::ConversationArtifact`. |
| [COMPLETE] | Deterministic result analysis and chart inference. | `superset_ai_agent/artifacts/insights.py`; `superset_ai_agent/artifacts/charts.py`; `tests/unit_tests/superset_ai_agent/test_artifact_insights.py`; `tests/unit_tests/superset_ai_agent/test_artifact_charts.py`. |
| [COMPLETE] | One-shot graph and conversation graph attach post-execution artifacts. | `superset_ai_agent/graph.py::TextToSqlGraph`; `superset_ai_agent/conversation_graph.py::ConversationGraph`; `tests/unit_tests/superset_ai_agent/test_graph.py`; `tests/unit_tests/superset_ai_agent/test_conversation_graph.py`. |
| [COMPLETE] | Wren is read/planning-only at the public agent boundary and fails closed when execution is enabled. | `superset_ai_agent/integrations/wren/client.py::WrenClient`, `DisabledWrenClient`, `FileWrenClient`; `superset_ai_agent/integrations/wren/http_client.py::WrenHttpClient`; `superset_ai_agent/integrations/wren/factory.py::create_wren_client`; `tests/unit_tests/superset_ai_agent/test_wren_client.py`; `tests/unit_tests/superset_ai_agent/test_wren_http_client.py`. |
| [COMPLETE] | REST SQL Lab adapter is the default governed execution adapter and carries catalog/schema. | `superset_ai_agent/config.py::AgentConfig.superset_agent_adapter`; `superset_ai_agent/integrations/superset/factory.py::create_superset_client`; `superset_ai_agent/integrations/superset/rest.py::SupersetRestClient.execute_sql_raw`. |
| [COMPLETE] | Agent request and conversation scopes carry `catalog_name` and `schema_name`. | `superset_ai_agent/schemas.py::AgentQueryRequest`; `superset_ai_agent/conversations/schemas.py::ConversationScope`; `superset_ai_agent/context/superset_metadata.py::SupersetMetadataContextProvider`. |
| [COMPLETE] | Agent-owned SQLAlchemy stores exist for conversations and semantic-layer workflow state. | `superset_ai_agent/conversations/sqlalchemy_store.py::SqlAlchemyConversationStore`; `superset_ai_agent/semantic_layer/sqlalchemy_store.py::SqlAlchemySemanticLayerStore`; `superset_ai_agent/persistence/models.py`. |
| [COMPLETE] | Identity can be resolved from Superset session, signed header, or static development mode; persistence rejects unsafe static identity unless explicitly allowed. | `superset_ai_agent/auth.py`; `superset_ai_agent/app.py::_validate_identity_persistence_config`; `tests/unit_tests/superset_ai_agent/test_auth.py`; `tests/unit_tests/superset_ai_agent/test_config.py`. |
| [COMPLETE] | Schema-scoped semantic project and MDL file persistence models exist. | `superset_ai_agent/persistence/models.py::AiAgentSemanticProject`; `AiAgentSemanticMdlFile`; `superset_ai_agent/semantic_layer/projects.py`; `superset_ai_agent/semantic_layer/mdl_files.py`. |
| [COMPLETE] | Project-aware semantic routes and MDL CRUD/upload/materialization endpoints exist. | `superset_ai_agent/app.py::create_app`; routes under `/agent/semantic-layer/projects`; `tests/unit_tests/superset_ai_agent/test_semantic_layer_api.py`; `tests/unit_tests/superset_ai_agent/test_semantic_layer_projects.py`; `tests/unit_tests/superset_ai_agent/test_semantic_layer_mdl_files.py`. |
| [COMPLETE] | Project materialization writes only active MDL files into project-scoped Wren runtime state. | `superset_ai_agent/semantic_layer/wren_materializer.py::materialize_wren_project`; `superset_ai_agent/semantic_layer/wren_runtime.py`; `tests/unit_tests/superset_ai_agent/test_wren_materializer.py`. |
| [COMPLETE] | URI fingerprint utilities strip secret material and provide fallback Superset database fingerprints. | `superset_ai_agent/semantic_layer/uri_fingerprint.py`; `tests/unit_tests/superset_ai_agent/test_semantic_layer_projects.py`. |
| [COMPLETE] | Semantic project authorization is centralized behind a Superset-proven access service. | `superset_ai_agent/semantic_layer/access.py::SemanticAccessService`; `superset_ai_agent/app.py::authorize_semantic_scope`; `authorize_semantic_project`; `tests/unit_tests/superset_ai_agent/test_semantic_layer_access.py`. |
| [COMPLETE] | Agent DB persistence uses versioned Alembic migrations with an explicit bootstrap mode for legacy unversioned tables. | `superset_ai_agent/persistence/database.py::run_migrations`; `superset_ai_agent/persistence/migrations/versions/0001_initial_agent_tables.py`; `tests/unit_tests/superset_ai_agent/test_persistence_database.py`. |
| [PARTIAL] | UI exposes semantic-layer editing, but as a left-bar button plus `SemanticLayerDrawer`, not a first-class mixed SQL Lab tab opened from a schema row. | `superset-frontend/src/SqlLab/components/SqlEditorLeftBar/index.tsx`; `superset-frontend/src/SqlLab/components/AiAgentPanel/SemanticLayerDrawer.tsx`; `superset-frontend/src/SqlLab/components/TabbedSqlEditors/index.tsx`. |
| [COMPLETE] | Markdown upload/enrichment can call a read-only Wren HTTP onboarding adapter and falls back to deterministic review drafts. | `superset_ai_agent/integrations/wren/http_client.py::WrenHttpClient.propose_mdl_from_document`; `superset_ai_agent/app.py` route `/documents/{document_id}/enrich`; `tests/unit_tests/superset_ai_agent/test_wren_http_client.py`; `tests/unit_tests/superset_ai_agent/test_semantic_layer_api.py`. |
| [COMPLETE] | AI source audit metadata is modeled as a typed execution source and carried through accepted SQL Lab fields. | `superset_ai_agent/schemas.py::SqlExecutionSource`; `AuditInfo`; `superset_ai_agent/integrations/superset/rest.py::SupersetRestClient.execute_sql_raw`; `TextToSqlGraph._execute_sql`; `ConversationGraph._execute_sql`; `tests/unit_tests/superset_ai_agent/test_superset_client.py`; `tests/unit_tests/superset_ai_agent/test_graph.py`; `tests/unit_tests/superset_ai_agent/test_conversation_graph.py`. |
| [COMPLETE] | Superset-proven DB/schema identity endpoint and semantic access proofs. | `superset/ai_agent/api.py::AiAgentRestApi.database_identity`; `superset_ai_agent/integrations/superset/client.py::DatabaseIdentity`; `superset_ai_agent/semantic_layer/access.py::SemanticAccessService`; `tests/unit_tests/superset_ai_agent/test_superset_client.py`; `tests/unit_tests/superset_ai_agent/test_semantic_layer_access.py`. |
| [COMPLETE] | Top-k schema retrieval service for very large schema projects with schema-required Wren runtime behavior. | `superset_ai_agent/semantic_layer/retrieval.py`; `superset_ai_agent/context/superset_metadata.py::SupersetMetadataContextProvider`; `superset_ai_agent/graph.py::TextToSqlGraph._load_wren_context`; `superset_ai_agent/conversation_graph.py::ConversationGraph._load_wren_context`; `tests/unit_tests/superset_ai_agent/test_semantic_layer_retrieval.py`; `tests/unit_tests/superset_ai_agent/test_context_provider.py`; `tests/unit_tests/superset_ai_agent/test_graph.py`. |
| [COMPLETE] | Object storage implementation for uploaded source documents. | `superset_ai_agent/semantic_layer/file_storage.py::LocalDocumentStorage`; `S3DocumentStorage`; `superset_ai_agent/app.py::_create_document_storage`; `tests/unit_tests/superset_ai_agent/test_semantic_layer_file_storage.py`. |
| [PARTIAL] | Superset semantic-layer/view REST bridge primitives exist, but the explicit publish route and Wren-MDL-to-Superset configuration mapper remain open. | `superset_ai_agent/integrations/superset/client.py::SupersetClient`; `superset_ai_agent/integrations/superset/rest.py::SupersetRestClient.list_semantic_layers`; `create_semantic_layer`; `update_semantic_layer`; `delete_semantic_layer`; `create_semantic_views`; `tests/unit_tests/superset_ai_agent/test_superset_client.py`. |

The conversation graph already supports SQL execution and reflection:

```text
load_conversation
  -> load_context
  -> draft_response
  -> validate_sql
  -> execute_sql
  -> reflect_sql_outcome
  -> retry or final answer
```

This plan adds deterministic analytics artifacts after Superset execution and
before reflection completes. Reflection remains responsible for the final
assistant prose answer.

## Governance Invariants

The implementation must preserve these invariants:

1. `SupersetClient.execute_sql` is the only execution boundary used by the
   agent graphs.
2. `SupersetRestClient` remains the recommended governed default because it
   delegates execution to SQL Lab REST.
3. Wren direct execution is not implemented. If a future Wren SDK exposes an
   execution method, the agent wrapper must not expose it.
4. SQL generated with Wren context still passes through
   `validate_read_only_sql`.
5. Superset authorization, SQL Lab permissions, database permissions, dataset
   permissions, RLS, row limits, timeouts, and audit behavior remain
   authoritative.
6. Documents and Wren semantic-layer data are context, not permission sources.
7. Existing `/ai-agent` request and response shapes remain compatible by adding
   optional fields.

## Target User Experience

For a request such as:

```text
Show gross moves by stage
```

the backend should return:

- generated SQL
- validation result
- optional execution result
- trace
- answer summary
- 2 to 3 insight cards
- chart spec
- data preview
- audit info if available
- recommended follow-up questions
- optional Wren context and planning metadata

The frontend should render:

- assistant answer card
- 2 to 3 KPI or insight cards
- chart preview
- `Data - N rows` toggle and table
- SQL, validation, trace, and audit collapsibles
- recommended follow-up buttons
- semantic-layer status and an action to open the SQL Lab semantic-layer editor

## Persistence And Identity Recommendation

The initial agent kept conversation state process-local. The current
implementation has both the original in-memory stores and durable SQLAlchemy
stores:

- `superset_ai_agent/conversations/store.py::ConversationStore` is a clean
  protocol boundary.
- `superset_ai_agent/conversations/memory.py::InMemoryConversationStore` is the
  local/test implementation.
- `superset_ai_agent/conversations/sqlalchemy_store.py::SqlAlchemyConversationStore`
  persists conversations, messages, and artifacts.
- `superset_ai_agent/semantic_layer/memory.py::InMemorySemanticLayerStore` is
  the local/test semantic-layer implementation.
- `superset_ai_agent/semantic_layer/sqlalchemy_store.py::SqlAlchemySemanticLayerStore`
  persists uploaded documents, proposed updates, reviewed updates, semantic
  versions, and semantic-layer events.
- `superset_ai_agent/persistence/models.py` defines the agent-owned tables.
- `superset_ai_agent/app.py::_validate_identity_persistence_config` rejects
  database-backed stores with static identity unless the explicit development
  override is set.
- `superset_ai_agent/auth.py::SupersetSessionIdentityProvider` validates the
  inbound browser session with Superset `/api/v1/me/` and scopes persisted
  state by the resolved Superset user.

Persisting conversations or uploaded semantic-layer documents with the static
`"local"` identity would create shared state across users. Database-backed
persistence must use `superset_session` or `signed_header` identity outside
local development.

Recommended storage model:

| State | Recommended owner | Reason |
| --- | --- | --- |
| Conversations, messages, artifacts | Agent-owned DB | Fast-moving agent contract, independent of Superset core migrations. |
| Uploaded documents, extracted text, review state | Agent-owned DB plus file/object storage | This is agent workflow state, not a Superset semantic model. |
| Wren context cache, semantic overlay versions, SSE events | Agent-owned DB | Derived/cache state with agent-specific invalidation rules. |
| Approved/published semantic models | Superset `semantic_layers` and `semantic_views` or Superset REST API | These are governed Superset semantic-layer objects with existing permissions and commands. |

Storage alternatives:

| Option | Fit | Pros | Cons |
| --- | --- | --- | --- |
| Agent-owned DB | Recommended Phase 0 | Low coupling, works while the agent is standalone, clean migration path for chat/doc state, simple tests. | Adds a second DB backup/migration surface. |
| Superset metadata DB via new Superset core models | Possible later | Unified metadata backup, native Superset ownership/RBAC patterns, no separate DB. | Requires core model/API/migration ownership and couples the POC to Superset release cadence. |
| Superset metadata DB via ad hoc direct writes from the agent | Avoid | Fast to prototype. | Bypasses Superset command validation, couples to internals, and does not solve identity. |
| Superset key-value table | Avoid for primary state | Existing metadata table with expiry support. | Poor fit for queryable conversations, review queues, ownership filtering, document lifecycle, and migrations. |

The codebase already has Superset semantic-layer tables in
`superset/semantic_layers/models.py::SemanticLayer` and `SemanticView`, with
DAOs in `superset/daos/semantic_layer.py` and migrations in
`superset/migrations/versions/2025-11-04_11-26_33d7e0e21daa_add_semantic_layers_and_views.py`.
Those tables should be used for explicitly approved semantic-layer publication,
not for transient chat transcripts, uploaded document drafts, review queues, or
Wren retrieval caches.

Avoid directly piggybacking the standalone agent onto Superset's metadata DB in
Phase 1 because:

- direct Superset DB writes would couple the agent to Superset app context,
  migrations, encryption configuration, and model internals;
- Superset core tables do not own transient chat transcripts, upload drafts,
  review queues, or Wren retrieval caches;
- uploaded document review state has different lifecycle and retention needs
  from Superset semantic-layer objects;
- using Superset's DB as a connection string is acceptable only if the agent
  owns its own tables and migrations, not if it mutates Superset internals.

Recommended architecture:

```text
AI Agent FastAPI service
  -> Agent identity provider
  -> Agent-owned persistence DB
       conversations
       messages
       artifacts
       semantic documents
       semantic update reviews
       Wren context versions/cache
       SSE events
  -> Superset REST SQL Lab for governed SQL execution
  -> Superset semantic-layer REST/commands only for approved publication
```

### Identity Boundary

Add:

```text
superset_ai_agent/auth.py
```

Types:

```python
class AgentIdentity(BaseModel):
    owner_id: str
    username: str | None = None
    email: str | None = None
    source: Literal["static", "signed_header", "superset_session"] = "static"


class IdentityProvider(Protocol):
    def get_identity(self, request: Request) -> AgentIdentity:
        """Resolve the owner for persisted agent state."""
```

Implement:

```python
class StaticIdentityProvider:
    """Development-only identity provider using DEFAULT_OWNER_ID."""


class SignedHeaderIdentityProvider:
    """Trusts signed internal headers from a Superset-authenticated proxy."""
```

Do not trust browser-supplied `X-Superset-User-*` headers directly. The
implemented SQL Lab path uses `AI_AGENT_IDENTITY_PROVIDER=superset_session` and
`SUPERSET_AUTH_MODE=user_session`: the agent validates the inbound browser
session with Superset `/api/v1/me/` and forwards only that request's browser
credentials to Superset REST/MCP. Signed internal headers remain an alternate
trusted-proxy pattern.

Modify `superset_ai_agent/app.py::create_app`:

- create an identity provider from config;
- add a FastAPI dependency such as `identity: AgentIdentity = Depends(...)`;
- pass `owner_id=identity.owner_id` to every `ConversationStore` and
  `SemanticLayerStore` method;
- reject database-backed persistence in production if identity mode is still
  static, unless an explicit development override is enabled.

Config additions in `superset_ai_agent/config.py::AgentConfig`:

```python
identity_provider: Literal["static", "signed_header", "superset_session"] = "superset_session"
allow_static_identity_with_persistence: bool = False
signed_identity_header: str = "X-Superset-Ai-Agent-Identity"
signed_identity_secret: str | None = None
superset_auth_mode: Literal["service_account", "user_session"] = "user_session"
```

Environment variables:

```text
AI_AGENT_IDENTITY_PROVIDER
AI_AGENT_ALLOW_STATIC_IDENTITY_WITH_PERSISTENCE
AI_AGENT_SIGNED_IDENTITY_HEADER
AI_AGENT_SIGNED_IDENTITY_SECRET
```

### Agent-Owned Database

Add dependencies to `requirements-ai-agent.txt`:

```text
SQLAlchemy follows the repository-pinned Superset dependency set.
alembic>=1.13,<2.0
```

For Postgres deployments, add a driver dependency either in the base agent
requirements or a deployment-specific requirements file:

```text
psycopg[binary]>=3.1,<4.0
```

Use synchronous SQLAlchemy sessions because the current FastAPI routes and store
protocols are synchronous.

Add:

```text
superset_ai_agent/persistence/__init__.py
superset_ai_agent/persistence/database.py
superset_ai_agent/persistence/models.py
superset_ai_agent/persistence/migrations/env.py
superset_ai_agent/persistence/migrations/script.py.mako
superset_ai_agent/persistence/migrations/versions/
```

`database.py`:

```python
def create_engine_from_config(config: AgentConfig) -> Engine: ...
def create_session_factory(engine: Engine) -> sessionmaker[Session]: ...
def run_migrations(config: AgentConfig) -> None: ...
```

Config additions:

```python
conversation_store: Literal["memory", "sqlalchemy"] = "memory"
semantic_layer_store: Literal["memory", "sqlalchemy"] = "memory"
agent_database_url: str = "sqlite:///./.data/ai_agent.db"
agent_database_echo: bool = False
agent_run_migrations: bool = True
agent_storage_dir: str = "./.data"
```

Environment variables:

```text
AI_AGENT_CONVERSATION_STORE=memory|sqlalchemy
AI_AGENT_SEMANTIC_LAYER_STORE=memory|sqlalchemy
AI_AGENT_DATABASE_URL
AI_AGENT_DATABASE_ECHO
AI_AGENT_RUN_MIGRATIONS
AI_AGENT_STORAGE_DIR
```

Docker Compose should mount a persistent volume when SQLite is used:

```yaml
services:
  superset-ai-agent:
    volumes:
      - superset_ai_agent_data:/app/.data

volumes:
  superset_ai_agent_data:
```

Postgres is recommended for shared deployments:

```text
AI_AGENT_DATABASE_URL=postgresql+psycopg://superset_ai_agent:...@db:5432/superset_ai_agent
```

Modify `superset_ai_agent/app.py::create_app` so persistent stores share one
database setup:

```python
session_factory = None
if (
    app_config.conversation_store == "sqlalchemy"
    or app_config.semantic_layer_store == "sqlalchemy"
):
    engine = create_engine_from_config(app_config)
    if app_config.agent_run_migrations:
        run_migrations(app_config)
    session_factory = create_session_factory(engine)

active_conversation_store = conversation_store or _create_conversation_store(
    app_config,
    session_factory=session_factory,
)
```

Update `_create_conversation_store`:

```python
def _create_conversation_store(
    config: AgentConfig,
    *,
    session_factory: sessionmaker[Session] | None = None,
) -> ConversationStore:
    if config.conversation_store == "memory":
        return InMemoryConversationStore()
    if config.conversation_store == "sqlalchemy":
        if session_factory is None:
            raise ValueError("SQLAlchemy conversation store requires a DB.")
        return SqlAlchemyConversationStore(session_factory)
    raise ValueError(...)
```

### Agent Persistence Models

Add ORM models in `superset_ai_agent/persistence/models.py`.

Tables:

```text
ai_agent_conversations
ai_agent_messages
ai_agent_artifacts
ai_agent_semantic_documents
ai_agent_semantic_updates
ai_agent_semantic_layer_versions
ai_agent_wren_context_cache
ai_agent_events
```

Recommended columns:

```python
class AiAgentConversation(Base):
    __tablename__ = "ai_agent_conversations"

    id = mapped_column(String(36), primary_key=True)
    owner_id = mapped_column(String(255), index=True, nullable=False)
    title = mapped_column(String(255), nullable=False)
    database_id = mapped_column(Integer, nullable=False)
    catalog_name = mapped_column(String(255), nullable=True)
    schema_name = mapped_column(String(255), nullable=True)
    scope = mapped_column(JSON, nullable=False)
    created_at = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    deleted_at = mapped_column(DateTime(timezone=True), nullable=True)


class AiAgentMessage(Base):
    __tablename__ = "ai_agent_messages"

    id = mapped_column(String(36), primary_key=True)
    conversation_id = mapped_column(
        String(36),
        ForeignKey("ai_agent_conversations.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    owner_id = mapped_column(String(255), index=True, nullable=False)
    role = mapped_column(String(32), nullable=False)
    content = mapped_column(Text, nullable=False)
    sequence = mapped_column(Integer, nullable=False)
    created_at = mapped_column(DateTime(timezone=True), nullable=False)


class AiAgentArtifact(Base):
    __tablename__ = "ai_agent_artifacts"

    id = mapped_column(String(36), primary_key=True)
    message_id = mapped_column(
        String(36),
        ForeignKey("ai_agent_messages.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    owner_id = mapped_column(String(255), index=True, nullable=False)
    type = mapped_column(String(64), nullable=False)
    sql = mapped_column(Text, nullable=True)
    payload = mapped_column(JSON, nullable=False)
    created_at = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at = mapped_column(DateTime(timezone=True), nullable=False)
```

Store the full `ConversationArtifact.model_dump(mode="json")` in
`AiAgentArtifact.payload`. Keep `sql` denormalized for filtering/debugging.

Semantic document tables:

```python
class AiAgentSemanticDocument(Base):
    __tablename__ = "ai_agent_semantic_documents"

    id = mapped_column(String(36), primary_key=True)
    project_id = mapped_column(String(36), index=True, nullable=True)
    owner_id = mapped_column(String(255), index=True, nullable=False)
    database_id = mapped_column(Integer, nullable=False, index=True)
    catalog_name = mapped_column(String(255), nullable=True)
    schema_name = mapped_column(String(255), nullable=True)
    dataset_ids = mapped_column(JSON, nullable=False)
    filename = mapped_column(String(512), nullable=False)
    content_type = mapped_column(String(255), nullable=False)
    size_bytes = mapped_column(Integer, nullable=False)
    checksum = mapped_column(String(128), nullable=False, index=True)
    storage_uri = mapped_column(String(1024), nullable=False)
    status = mapped_column(String(64), nullable=False, index=True)
    summary = mapped_column(Text, nullable=True)
    extracted_text = mapped_column(Text, nullable=True)
    extracted_text_preview = mapped_column(Text, nullable=True)
    warnings = mapped_column(JSON, nullable=False)
    error = mapped_column(Text, nullable=True)
    created_at = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at = mapped_column(DateTime(timezone=True), nullable=False)


class AiAgentSemanticUpdate(Base):
    __tablename__ = "ai_agent_semantic_updates"

    id = mapped_column(String(36), primary_key=True)
    project_id = mapped_column(String(36), index=True, nullable=True)
    document_id = mapped_column(
        String(36),
        ForeignKey("ai_agent_semantic_documents.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    owner_id = mapped_column(String(255), index=True, nullable=False)
    kind = mapped_column(String(64), nullable=False)
    target = mapped_column(JSON, nullable=False)
    value = mapped_column(JSON, nullable=False)
    confidence = mapped_column(Float, nullable=True)
    reviewed = mapped_column(Boolean, nullable=False, default=False)
    approved = mapped_column(Boolean, nullable=False, default=False)
    reviewer_id = mapped_column(String(255), nullable=True)
    review_notes = mapped_column(Text, nullable=True)
    created_at = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at = mapped_column(DateTime(timezone=True), nullable=False)
    reviewed_at = mapped_column(DateTime(timezone=True), nullable=True)
```

Wren/cache/version tables:

```python
class AiAgentSemanticLayerVersion(Base):
    __tablename__ = "ai_agent_semantic_layer_versions"

    id = mapped_column(String(36), primary_key=True)
    project_id = mapped_column(String(36), index=True, nullable=True)
    owner_id = mapped_column(String(255), index=True, nullable=False)
    database_id = mapped_column(Integer, nullable=False, index=True)
    catalog_name = mapped_column(String(255), nullable=True)
    schema_name = mapped_column(String(255), nullable=True)
    dataset_ids = mapped_column(JSON, nullable=False)
    scope_hash = mapped_column(String(128), index=True, nullable=False)
    version = mapped_column(String(64), nullable=False)
    status = mapped_column(String(64), nullable=False)
    mdl = mapped_column(JSON, nullable=True)
    wren_context = mapped_column(JSON, nullable=True)
    source_update_ids = mapped_column(JSON, nullable=False)
    published_semantic_layer_uuid = mapped_column(String(36), nullable=True)
    created_at = mapped_column(DateTime(timezone=True), nullable=False)


class AiAgentWrenContextCache(Base):
    __tablename__ = "ai_agent_wren_context_cache"

    id = mapped_column(String(36), primary_key=True)
    project_id = mapped_column(String(36), index=True, nullable=True)
    owner_id = mapped_column(String(255), index=True, nullable=False)
    scope_hash = mapped_column(String(128), index=True, nullable=False)
    question_hash = mapped_column(String(128), index=True, nullable=False)
    context = mapped_column(JSON, nullable=False)
    created_at = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at = mapped_column(DateTime(timezone=True), nullable=True, index=True)


class AiAgentEvent(Base):
    __tablename__ = "ai_agent_events"

    id = mapped_column(String(36), primary_key=True)
    project_id = mapped_column(String(36), index=True, nullable=True)
    owner_id = mapped_column(String(255), index=True, nullable=False)
    scope = mapped_column(JSON, nullable=False)
    type = mapped_column(String(128), nullable=False, index=True)
    payload = mapped_column(JSON, nullable=False)
    created_at = mapped_column(DateTime(timezone=True), nullable=False, index=True)
```

### Persistent Store Implementations

Add:

```text
superset_ai_agent/conversations/sqlalchemy_store.py
superset_ai_agent/semantic_layer/sqlalchemy_store.py
superset_ai_agent/semantic_layer/file_storage.py
```

`SqlAlchemyConversationStore` must implement the existing
`ConversationStore` protocol:

```python
class SqlAlchemyConversationStore:
    def __init__(self, session_factory: sessionmaker[Session]):
        self.session_factory = session_factory

    def create(self, scope: ConversationScope, *, owner_id: str) -> Conversation: ...
    def list(self, *, owner_id: str) -> list[ConversationSummary]: ...
    def get(self, conversation_id: str, *, owner_id: str) -> Conversation: ...
    def update_scope(...): ...
    def append(...): ...
    def replace_artifact(...): ...
    def delete(...): ...
```

Implementation notes:

- Always filter by both `id` and `owner_id`.
- Use soft delete for conversations (`deleted_at`) unless hard delete is
  explicitly requested.
- Preserve message sequence order with an integer `sequence`.
- Serialize Pydantic models with `model_dump(mode="json")`.
- Rehydrate with `Conversation.model_validate(...)`.
- Keep tests for `InMemoryConversationStore` and run the same behavior tests
  against `SqlAlchemyConversationStore`.

`SqlAlchemySemanticLayerStore` should implement the `SemanticLayerStore`
protocol from the document section and own document/update/version/event state.
Every project-aware method must filter by both `project_id` and `owner_id` or by
an access decision from `SemanticAccessService`; direct project ID lookup alone
is not sufficient.

`file_storage.py` should keep raw uploaded bytes outside the DB:

```python
class DocumentStorage(Protocol):
    def write(self, *, document_id: str, filename: str, content: bytes) -> str: ...
    def read(self, storage_uri: str) -> bytes: ...
    def delete(self, storage_uri: str) -> None: ...
```

Phase 1 implementation:

```python
class LocalDocumentStorage:
    """Stores uploaded files under AI_AGENT_STORAGE_DIR/documents."""
```

Store only `storage_uri`, checksum, extracted text, and review metadata in the
database.

## 1. Backend Schema Changes

Modify `superset_ai_agent/schemas.py`.

Add reusable analytics artifact models:

```python
class InsightCard(BaseModel):
    """Small insight shown next to an executed analytics result."""

    title: str
    value: str | int | float | None = None
    metric: str | None = None
    category: str | None = None
    description: str | None = None
    severity: Literal["info", "success", "warning"] = "info"


class ChartEncoding(BaseModel):
    """Minimal frontend chart encoding."""

    x: str | None = None
    y: str | list[str] | None = None
    series: str | None = None
    time: str | None = None
    label: str | None = None


class ChartSpec(BaseModel):
    """Lightweight chart preview contract for returned rows."""

    type: Literal["bar", "line", "table"]
    title: str | None = None
    encoding: ChartEncoding = Field(default_factory=ChartEncoding)
    options: dict[str, Any] = Field(default_factory=dict)


class AuditInfo(BaseModel):
    """Audit metadata propagated from the governed Superset execution path."""

    adapter: Literal["rest", "mcp", "local"] | None = None
    query_id: int | str | None = None
    results_key: str | None = None
    executed_sql: str | None = None
    database_id: int | None = None
    catalog_name: str | None = None
    schema_name: str | None = None
    row_limit: int | None = None
    timeout_seconds: int | None = None
    source: str | None = None


class WrenContextArtifact(BaseModel):
    """Wren context, examples, planning, and semantic-layer metadata."""

    enabled: bool = False
    available: bool = False
    matched_models: list[str] = Field(default_factory=list)
    example_ids: list[str] = Field(default_factory=list)
    document_ids: list[str] = Field(default_factory=list)
    semantic_layer_version: str | None = None
    indexing_status: str | None = None
    dry_plan: dict[str, Any] | None = None
    warnings: list[str] = Field(default_factory=list)
```

Extend `AgentQueryRequest` without breaking existing callers:

```python
catalog_name: str | None = None
schema_name: str | None = None
```

Extend `ConversationScope` in
`superset_ai_agent/conversations/schemas.py` the same way. Existing callers can
omit `catalog_name`; Wren-backed retrieval should require `schema_name` and
should include `catalog_name` when Superset/database metadata provides it.

Extend `ExecutionResult`:

```python
class ExecutionResult(BaseModel):
    """Small, model-safe SQL execution result."""

    columns: list[str]
    rows: list[dict[str, Any]]
    row_count: int
    audit: AuditInfo | None = None
    is_truncated: bool = False
```

Extend `AgentQueryResponse` with optional fields:

```python
answer_summary: str | None = None
insight_cards: list[InsightCard] = Field(default_factory=list)
chart_spec: ChartSpec | None = None
data_preview: ExecutionResult | None = None
audit: AuditInfo | None = None
recommended_followups: list[str] = Field(default_factory=list)
wren_context: WrenContextArtifact | None = None
```

Modify `superset_ai_agent/conversations/schemas.py`.

Import:

```python
from superset_ai_agent.schemas import (
    AuditInfo,
    ChartSpec,
    ExecutionResult,
    InsightCard,
    SqlValidation,
    TraceEvent,
    WrenContextArtifact,
)
```

Extend `ConversationArtifact`:

```python
answer_summary: str | None = None
insight_cards: list[InsightCard] = Field(default_factory=list)
chart_spec: ChartSpec | None = None
data_preview: ExecutionResult | None = None
audit: AuditInfo | None = None
recommended_followups: list[str] = Field(default_factory=list)
wren_context: WrenContextArtifact | None = None
```

Backward compatibility:

- Existing fields remain unchanged.
- Existing request bodies remain unchanged.
- New response fields are optional.
- Existing frontend clients continue to render SQL, validation, results, and
  trace even if they ignore the new fields.

## 2. Backend Post-Execution Artifact Generation

Add:

```text
superset_ai_agent/artifacts/__init__.py
superset_ai_agent/artifacts/insights.py
```

`insights.py` should contain deterministic result profiling and summary logic.
Do not call the LLM from this module. The LLM reflection step can still use the
execution observations for prose, but deterministic artifacts should be stable
and testable.

Proposed classes:

```python
@dataclass(frozen=True)
class ColumnProfile:
    name: str
    kind: Literal["categorical", "numeric", "time", "unknown"]
    non_null_count: int
    null_count: int


@dataclass(frozen=True)
class ResultAnalysis:
    row_count: int
    profiles: list[ColumnProfile]
    category_column: str | None
    time_column: str | None
    numeric_columns: list[str]
    primary_metric: str | None
    is_empty: bool
    is_single_row: bool
    is_likely_truncated: bool


@dataclass(frozen=True)
class ArtifactBundle:
    answer_summary: str
    insight_cards: list[InsightCard]
    data_preview: ExecutionResult
    recommended_followups: list[str]
```

Proposed functions:

```python
def profile_result(
    result: ExecutionResult,
    *,
    row_limit: int,
) -> ResultAnalysis:
    """Classify returned columns and identify primary dimensions/measures."""


def is_numeric_value(value: Any) -> bool:
    """Return true for int, float, Decimal, and numeric strings."""


def is_time_value(value: Any) -> bool:
    """Return true for date/datetime values and parseable date-like strings."""


def detect_category_column(
    result: ExecutionResult,
    analysis: ResultAnalysis,
) -> str | None:
    """Choose the best string-like grouping column."""


def detect_primary_metric(
    question: str,
    analysis: ResultAnalysis,
) -> str | None:
    """Choose the numeric measure most relevant to the user question."""


def compute_category_stats(
    result: ExecutionResult,
    *,
    category_column: str,
    metric_column: str,
) -> list[dict[str, Any]]:
    """Aggregate returned rows by category and metric."""


def build_answer_summary(
    *,
    question: str,
    result: ExecutionResult,
    analysis: ResultAnalysis,
) -> str:
    """Create a concise deterministic answer summary."""


def build_insight_cards(
    *,
    result: ExecutionResult,
    analysis: ResultAnalysis,
) -> list[InsightCard]:
    """Create 2 to 3 cards for top, spread, and lowest insights."""


def build_recommended_followups(
    *,
    question: str,
    analysis: ResultAnalysis,
) -> list[str]:
    """Suggest safe follow-up analytics questions."""


def build_artifact_bundle(
    *,
    question: str,
    result: ExecutionResult,
    row_limit: int,
) -> ArtifactBundle:
    """Build summary, cards, preview, and follow-up suggestions."""
```

Handling rules:

| Case | Behavior |
| --- | --- |
| Zero rows | Return an empty-state summary, no top/lowest card, suggest broader filters or alternate grouping. |
| One row | Show row-level metric cards; skip spread and lowest category. |
| Many rows | Analyze returned rows; phrase claims as "in returned rows" when likely truncated. |
| Multiple numeric columns | Pick a primary metric by matching question tokens to column names; otherwise choose first non-id numeric column. |
| Nulls | Ignore nulls in numeric stats; add a warning card only when nulls materially affect the result. |
| Non-numeric measures | Use row count or frequency cards instead of sum/percent/gap cards. |
| Row limits | Set `ExecutionResult.is_truncated` when `row_count >= row_limit` or adapter metadata proves truncation. |

Skeleton:

```python
def build_artifact_bundle(
    *,
    question: str,
    result: ExecutionResult,
    row_limit: int,
) -> ArtifactBundle:
    analysis = profile_result(result, row_limit=row_limit)
    data_preview = result.model_copy(
        update={
            "rows": result.rows[:row_limit],
            "is_truncated": analysis.is_likely_truncated,
        }
    )
    return ArtifactBundle(
        answer_summary=build_answer_summary(
            question=question,
            result=result,
            analysis=analysis,
        ),
        insight_cards=build_insight_cards(result=result, analysis=analysis),
        data_preview=data_preview,
        recommended_followups=build_recommended_followups(
            question=question,
            analysis=analysis,
        ),
    )
```

## 3. Chart-Spec Generation

Add:

```text
superset_ai_agent/artifacts/charts.py
```

Functions:

```python
def infer_chart_spec(
    *,
    question: str,
    result: ExecutionResult,
    analysis: ResultAnalysis,
) -> ChartSpec | None:
    """Infer a small chart spec from returned rows."""


def can_render_bar(analysis: ResultAnalysis) -> bool:
    """Return true when categorical x and numeric y columns exist."""


def can_render_line(analysis: ResultAnalysis) -> bool:
    """Return true when time x and numeric y columns exist."""
```

Minimal Phase 1 contract:

```json
{
  "type": "bar",
  "title": "Gross moves by stage",
  "encoding": {
    "x": "stage",
    "y": "gross_moves",
    "label": "stage"
  },
  "options": {
    "max_categories": 20,
    "sort": "desc"
  }
}
```

Inference rules:

| Result shape | Chart spec |
| --- | --- |
| Time column plus numeric metric | `line` |
| Categorical column plus numeric metric | `bar` |
| No reliable dimension/metric pair | `table` fallback |
| Zero rows | `None` or `table` empty state |
| Too many categories | `table` fallback unless top-N result is already sorted and limited |

Use a lightweight custom chart spec for Phase 1. Do not create Superset chart
objects or saved chart metadata in Phase 1. A later phase can translate this
spec into Explore form data or use MCP chart-generation tools.

## 4. Graph Integration

Modify `superset_ai_agent/graph.py::AgentState`:

```python
answer_summary: str | None
insight_cards: list[InsightCard]
chart_spec: ChartSpec | None
data_preview: ExecutionResult | None
audit: AuditInfo | None
recommended_followups: list[str]
wren_context: WrenContextArtifact | None
```

Modify `TextToSqlGraph._compile_graph`:

```python
graph.add_node("load_wren_context", self._load_wren_context)
graph.add_node("dry_plan_with_wren", self._dry_plan_with_wren)
graph.add_node("build_artifacts", self._build_artifacts)

graph.add_edge("load_context", "load_wren_context")
graph.add_edge("load_wren_context", "draft_sql")
graph.add_edge("draft_sql", "dry_plan_with_wren")
graph.add_edge("dry_plan_with_wren", "validate_sql")
graph.add_edge("execute_sql", "build_artifacts")
graph.add_edge("build_artifacts", END)
```

If Wren is disabled or unavailable, `_load_wren_context` and
`_dry_plan_with_wren` return the state with warning trace events, not errors.

Modify `TextToSqlGraph.run` to copy new state fields into `AgentQueryResponse`.

Modify `superset_ai_agent/conversation_graph.py::ConversationState` with the
same artifact and Wren fields.

Modify `ConversationGraph._compile_graph`:

```python
graph.add_node("load_wren_context", self._load_wren_context)
graph.add_node("dry_plan_with_wren", self._dry_plan_with_wren)
graph.add_node("build_artifacts", self._build_artifacts)

graph.add_edge("load_context", "load_wren_context")
graph.add_edge("load_wren_context", "draft_response")
graph.add_edge("draft_response", "dry_plan_with_wren")  # only for SQL drafts
```

For conversation execution, route:

```text
execute_sql -> build_artifacts -> reflect_sql_outcome
```

instead of:

```text
execute_sql -> reflect_sql_outcome
```

Update `_route_after_execution`:

```python
def _route_after_execution(self, state: ConversationState) -> str:
    if state.get("error"):
        return "end"
    if state.get("execution_result") is not None:
        return "build"
    return "reflect"
```

Add `_build_artifacts`:

```python
def _build_artifacts(self, state: ConversationState) -> ConversationState:
    result = state.get("execution_result")
    if result is None:
        return state

    bundle = build_artifact_bundle(
        question=state["request"].message,
        result=result,
        row_limit=self.config.default_sql_limit,
    )
    analysis = profile_result(result, row_limit=self.config.default_sql_limit)
    chart_spec = infer_chart_spec(
        question=state["request"].message,
        result=result,
        analysis=analysis,
    )
    trace = [
        *state.get("trace", []),
        TraceEvent(
            step="build_artifacts",
            summary="Built conversational analytics artifacts.",
            details={
                "insight_card_count": len(bundle.insight_cards),
                "chart_type": chart_spec.type if chart_spec else None,
            },
        ),
    ]
    artifacts = list(state.get("artifacts", []))
    if artifacts:
        artifacts[-1] = artifacts[-1].model_copy(
            update={
                "answer_summary": bundle.answer_summary,
                "insight_cards": bundle.insight_cards,
                "chart_spec": chart_spec,
                "data_preview": bundle.data_preview,
                "audit": result.audit,
                "recommended_followups": bundle.recommended_followups,
                "wren_context": state.get("wren_context"),
                "trace": trace,
            }
        )
    return {
        **state,
        "artifacts": artifacts,
        "trace": trace,
    }
```

Update `_artifact_with_execution_state` so approved SQL artifact replacement
copies:

- `answer_summary`
- `insight_cards`
- `chart_spec`
- `data_preview`
- `audit`
- `recommended_followups`
- `wren_context`
- `trace`

Preserve manual mode behavior: when `execute=false` or `execution_mode` is
`manual`, validated SQL artifacts are returned for user approval and no result
analysis is generated until execution occurs.

## 5. Wren Integration

Add:

```text
superset_ai_agent/integrations/wren/__init__.py
superset_ai_agent/integrations/wren/client.py
superset_ai_agent/integrations/wren/factory.py
```

`client.py`:

```python
class WrenClient(Protocol):
    """Read-only Wren integration used for context and planning."""

    def is_available(self) -> bool:
        """Return whether Wren assets and dependencies are usable."""

    def list_models(self) -> list[str]:
        """Return semantic model names known to Wren."""

    def fetch_context(
        self,
        *,
        question: str,
        superset_context: AgentContext,
        project: SemanticProject | None = None,
        materialized_project_path: str | None = None,
    ) -> WrenContextArtifact:
        """Fetch Wren semantic context for an already permission-filtered scope."""

    def recall_examples(
        self,
        *,
        question: str,
        limit: int,
        project: SemanticProject | None = None,
    ) -> list[dict[str, Any]]:
        """Return example questions, SQL patterns, or semantic memories."""

    def dry_plan(
        self,
        *,
        question: str,
        sql: str | None,
        context: AgentContext,
        project: SemanticProject | None = None,
        materialized_project_path: str | None = None,
    ) -> dict[str, Any]:
        """Return Wren planning metadata without executing SQL."""


class DisabledWrenClient:
    """Fail-closed Wren client used when Wren is disabled."""

    def is_available(self) -> bool:
        return False
```

The existing file-backed client can keep the older `superset_context`-only
signature as a compatibility shim while the project-aware implementation lands,
but the finalized schema-scoped runtime should pass both the resolved
`SemanticProject` and the materialized project directory into Wren. This
prevents Wren from loading a global MDL path when a user selected only one
database/schema.

Do not include an execution method. If a future Wren SDK requires an execution
capability on a lower-level object, keep it private and make `factory.py` fail
when execution is enabled:

```python
if config.wren_execution_enabled:
    raise ValueError("Wren execution is not supported by the Superset AI agent.")
```

Modify `superset_ai_agent/config.py::AgentConfig`:

```python
wren_enabled: bool = True
wren_project_path: str | None = None
wren_mdl_path: str | None = None
wren_memory_path: str | None = None
wren_dry_plan_enabled: bool = False
wren_execution_enabled: bool = False
wren_context_limit: int = 8
wren_example_limit: int = 5
wren_semantic_doc_store_path: str | None = None
wren_max_document_bytes: int = 2_000_000
wren_mdl_upload_allowed_document_types: tuple[str, ...] = (
    "application/x-yaml",
    "text/yaml",
    "text/markdown",
)
wren_allowed_document_types: tuple[str, ...] = (
    "text/plain",
    "text/markdown",
    "text/csv",
    "application/json",
)
```

Read env vars:

```text
WREN_ENABLED
WREN_PROJECT_PATH
WREN_MDL_PATH
WREN_MEMORY_PATH
WREN_DRY_PLAN_ENABLED
WREN_EXECUTION_ENABLED
WREN_CONTEXT_LIMIT
WREN_EXAMPLE_LIMIT
WREN_SEMANTIC_DOC_STORE_PATH
WREN_MAX_DOCUMENT_BYTES
WREN_MDL_UPLOAD_ALLOWED_DOCUMENT_TYPES
WREN_ALLOWED_DOCUMENT_TYPES
```

Modify `superset_ai_agent/app.py::create_app`:

```python
from superset_ai_agent.integrations.wren.factory import create_wren_client

wren_client = create_wren_client(app_config)

graph = TextToSqlGraph(
    config=app_config,
    model_client=active_model_client,
    context_provider=context_provider,
    superset_client=superset_client,
    wren_client=wren_client,
)
active_conversation_graph = ConversationGraph(
    config=app_config,
    model_client=active_model_client,
    context_provider=context_provider,
    superset_client=superset_client,
    conversation_store=active_conversation_store,
    wren_client=wren_client,
)
```

Safer Phase 1 choice: add Wren as a graph node after `load_context`, not as a
wrapper around `SupersetMetadataContextProvider`. This guarantees Superset
permission-filtered context is loaded before any Wren semantic retrieval.

## 6. Document-Driven Semantic Layer

Wren can use documents to build a better semantic layer. The Superset AI agent
should expose that as a governed authoring workflow:

```text
upload document
  -> extract text
  -> propose semantic updates
  -> user reviews updates
  -> approved updates are indexed
  -> graph retrieves approved Wren context
  -> SQL validates and executes through SupersetClient
```

This section describes the transitional scope-based document workflow already
started in the agent. The finalized product model is project-aware:

- documents, proposed updates, generated MDL, versions, events, and Wren
  materialization are scoped to a `SemanticProject`;
- existing scope-based routes should remain only as compatibility wrappers that
  resolve or create a project from `(database_id, catalog, schema_name)`;
- the SQL Lab semantic editor tab is the primary UI for upload, review,
  validation, deletion, and activation.

Add:

```text
superset_ai_agent/semantic_layer/__init__.py
superset_ai_agent/semantic_layer/schemas.py
superset_ai_agent/semantic_layer/store.py
superset_ai_agent/semantic_layer/memory.py
superset_ai_agent/semantic_layer/sqlalchemy_store.py
superset_ai_agent/semantic_layer/file_storage.py
superset_ai_agent/semantic_layer/documents.py
superset_ai_agent/semantic_layer/extractors.py
superset_ai_agent/semantic_layer/review.py
superset_ai_agent/semantic_layer/indexer.py
superset_ai_agent/semantic_layer/events.py
```

Schemas in `semantic_layer/schemas.py`:

```python
SemanticDocumentStatus = Literal[
    "uploaded",
    "extracted",
    "needs_review",
    "approved",
    "indexed",
    "error",
]


class SemanticUpdate(BaseModel):
    id: str = Field(default_factory=_new_id)
    kind: Literal[
        "model_description",
        "field_description",
        "metric",
        "synonym",
        "example",
        "relationship",
    ]
    target: dict[str, Any]
    value: dict[str, Any]
    confidence: float | None = Field(default=None, ge=0, le=1)
    source_document_id: str
    reviewed: bool = False
    approved: bool = False


class SemanticDocument(BaseModel):
    id: str = Field(default_factory=_new_id)
    filename: str
    content_type: str
    size_bytes: int
    status: SemanticDocumentStatus = "uploaded"
    project_id: str | None = None
    scope: ConversationScope
    checksum: str
    storage_uri: str
    summary: str | None = None
    extracted_text: str | None = None
    extracted_text_preview: str | None = None
    proposed_updates: list[SemanticUpdate] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    error: str | None = None
    created_at: datetime = Field(default_factory=_utc_now)
    updated_at: datetime = Field(default_factory=_utc_now)


class SemanticLayerReviewRequest(BaseModel):
    approved_update_ids: list[str] = Field(default_factory=list)
    rejected_update_ids: list[str] = Field(default_factory=list)
    edited_updates: list[SemanticUpdate] = Field(default_factory=list)
    notes: str | None = None


class SemanticLayerState(BaseModel):
    project_id: str | None = None
    database_id: int
    catalog_name: str | None = None
    schema_name: str | None = None
    dataset_ids: list[int] = Field(default_factory=list)
    document_count: int
    approved_document_count: int
    indexed_document_count: int
    semantic_layer_version: str | None = None
    indexing_status: Literal["idle", "running", "error"] = "idle"
    last_error: str | None = None


class SemanticLayerVersion(BaseModel):
    id: str = Field(default_factory=_new_id)
    project_id: str | None = None
    scope: ConversationScope
    scope_hash: str
    version: str
    status: Literal["idle", "running", "error"] = "idle"
    mdl: dict[str, Any] | None = None
    wren_context: WrenContextArtifact | None = None
    source_update_ids: list[str] = Field(default_factory=list)
    published_semantic_layer_uuid: str | None = None
    created_at: datetime = Field(default_factory=_utc_now)


class SemanticLayerEvent(BaseModel):
    type: Literal[
        "document_uploaded",
        "document_extracted",
        "review_required",
        "review_saved",
        "index_started",
        "index_completed",
        "index_failed",
    ]
    project_id: str | None = None
    document_id: str | None = None
    state: SemanticLayerState | None = None
    message: str
    created_at: datetime = Field(default_factory=_utc_now)
```

Store protocol in `semantic_layer/store.py`:

```python
class SemanticLayerStore(Protocol):
    def save_document(
        self,
        document: SemanticDocument,
        *,
        owner_id: str,
    ) -> SemanticDocument: ...

    def list_documents(
        self,
        scope: ConversationScope,
        *,
        owner_id: str,
    ) -> list[SemanticDocument]: ...

    def list_project_documents(
        self,
        project_id: str,
        *,
        owner_id: str,
    ) -> list[SemanticDocument]: ...

    def get_document(
        self,
        document_id: str,
        *,
        owner_id: str,
    ) -> SemanticDocument: ...

    def update_document(
        self,
        document: SemanticDocument,
        *,
        owner_id: str,
    ) -> SemanticDocument: ...

    def save_updates(
        self,
        document_id: str,
        updates: list[SemanticUpdate],
        *,
        owner_id: str,
    ) -> list[SemanticUpdate]: ...

    def get_state(
        self,
        scope: ConversationScope,
        *,
        owner_id: str,
    ) -> SemanticLayerState: ...

    def get_project_state(
        self,
        project_id: str,
        *,
        owner_id: str,
    ) -> SemanticLayerState: ...

    def append_event(
        self,
        event: SemanticLayerEvent,
        *,
        owner_id: str,
    ) -> None: ...

    def list_events(
        self,
        scope: ConversationScope,
        *,
        owner_id: str,
    ) -> list[SemanticLayerEvent]: ...

    def list_project_events(
        self,
        project_id: str,
        *,
        owner_id: str,
    ) -> list[SemanticLayerEvent]: ...
```

The scope-based methods remain for compatibility. New SQL Lab semantic editor
code should call the project-aware methods and should fail closed if a document,
event, version, or MDL file references a project the user cannot access.

Phase 1 should use `semantic_layer/sqlalchemy_store.py` plus
`semantic_layer/file_storage.py`. The in-memory semantic-layer store should be
test-only. Uploaded document and review state should survive process restarts
because users need to review and refine the semantic layer over time.

Wire the semantic-layer store in `superset_ai_agent/app.py::create_app` next to
the conversation store:

```python
active_semantic_layer_store = semantic_layer_store or _create_semantic_layer_store(
    app_config,
    session_factory=session_factory,
)
```

Add a helper:

```python
def _create_semantic_layer_store(
    config: AgentConfig,
    *,
    session_factory: sessionmaker[Session] | None,
) -> SemanticLayerStore:
    if config.semantic_layer_store == "sqlalchemy":
        if session_factory is None:
            raise ValueError("SQLAlchemy semantic layer store requires a DB.")
        return SqlAlchemySemanticLayerStore(session_factory)
    if config.semantic_layer_store == "memory":
        return InMemorySemanticLayerStore()
    raise ValueError(...)
```

Production should use `sqlalchemy`, not `memory`.

Document extraction in `semantic_layer/extractors.py`:

```python
class DocumentExtractor(Protocol):
    def extract_text(self, *, filename: str, content_type: str, content: bytes) -> str:
        """Extract safe plain text from an uploaded document."""


class PlainTextExtractor:
    ...


class JsonExtractor:
    ...


class CsvExtractor:
    ...
```

The generic backend extractor may continue to support plain text, Markdown,
CSV, and JSON for compatibility and tests. The finalized SQL Lab
`SemanticLayerEditor` upload UI should expose only:

- valid MDL YAML (`.yaml`, `.yml`);
- Markdown business context (`.md`, `.markdown`) for Wren enrichment.

Plain text, CSV, and JSON should not appear in the first SQL Lab editor UI
unless a later enrichment design explains how users review the generated MDL.
PDF and DOCX can be added behind optional dependencies after the review
workflow is stable.

Review/index workflow:

- `documents.py::create_document` validates content type and size, computes a
  checksum, extracts safe text, and creates `SemanticDocument` under a resolved
  `SemanticProject`.
- `review.py::propose_updates` calls `WrenClient.preview_document_updates`
  using the permission-filtered `AgentContext`.
- `review.py::apply_review` persists approved or edited `SemanticUpdate`
  objects.
- `indexer.py::rebuild_index` calls `WrenClient.apply_reviewed_updates` to
  write an approved semantic overlay or Wren memory/index.
- `events.py` exposes in-process events for server-sent events.

Add FastAPI routes in `app.py`:

```text
POST  /agent/semantic-layer/documents
GET   /agent/semantic-layer/documents
GET   /agent/semantic-layer/documents/{document_id}
PATCH /agent/semantic-layer/documents/{document_id}/review
POST  /agent/semantic-layer/index/rebuild
GET   /agent/semantic-layer/state
GET   /agent/semantic-layer/events
```

These scope-based routes are compatibility wrappers. New UI work should use the
project routes in sections 16 and 21:

```text
POST  /agent/semantic-layer/projects/{project_id}/documents
GET   /agent/semantic-layer/projects/{project_id}/documents
PATCH /agent/semantic-layer/projects/{project_id}/documents/{document_id}/review
POST  /agent/semantic-layer/projects/{project_id}/index/rebuild
```

`POST /agent/semantic-layer/documents` should accept `UploadFile` and a
serialized `ConversationScope`; project-aware upload should accept `UploadFile`
plus the resolved `project_id`. `python-multipart` is required for both.

Real-time behavior:

- Use `GET /agent/semantic-layer/events` with server-sent events in Phase 1.
- Emit events for upload, extraction, review-required, review-saved, indexing
  started, indexing completed, and indexing failed.
- Avoid WebSockets until bidirectional editing is required.

Security requirements for documents:

- Scope every document to `project_id`, `database_id`, optional `catalog_name`,
  `schema_name`, and `dataset_ids`.
- Only approved updates affect Wren retrieval.
- Never render uploaded HTML directly.
- Enforce content-type and size allowlists.
- Treat document content as untrusted prompt input.
- Do not let document-derived data change Superset permissions.
- Do not build a global Wren context containing datasets outside the current
  permission-filtered Superset context.

## 7. Superset-To-Wren MDL Generation

Add:

```text
superset_ai_agent/integrations/wren/mdl_exporter.py
```

Functions:

```python
def export_agent_context_to_mdl(context: AgentContext) -> dict[str, Any]:
    """Convert permission-filtered Superset context into a minimal Wren MDL."""


def write_mdl(context: AgentContext, output_path: Path) -> None:
    """Write a minimal mdl.json for the supplied context."""


def model_from_dataset(dataset: DatasetMetadata) -> dict[str, Any]:
    """Map a Superset dataset to a Wren model."""


def measure_from_metric(metric: MetricSummary) -> dict[str, Any]:
    """Map a Superset metric to a Wren measure."""


def column_to_field(column: ColumnSummary) -> dict[str, Any]:
    """Map a Superset column to a Wren field."""
```

Input:

- `AgentContext`
- Superset database summary
- Superset datasets
- columns
- metrics
- later: virtual dataset SQL and calculated columns if exposed in
  `DatasetMetadata`

Output:

- minimal `mdl.json`
- optional semantic overlay generated from approved documents

Mapping:

| Superset object | Wren Phase 1 mapping |
| --- | --- |
| database | data source metadata |
| physical dataset | Wren model |
| virtual dataset | omit unless SQL is exposed in context |
| column | field |
| metric | measure |
| calculated column | omit until context includes it |
| description | model/field/measure description |
| relationship | omit unless explicitly provided |
| owner/certification | optional display metadata only |
| RLS/permissions | do not map as enforcement rules |

Permission handling:

- Generate MDL only from the current permission-filtered `AgentContext`.
- Do not export all Superset datasets into a shared Wren model by default.
- A future admin-managed semantic model can exist, but request-time retrieval
  must still filter to objects the current Superset principal can access.

## 8. Superset Adapter Audit Propagation

Modify `superset_ai_agent/integrations/superset/rest.py`.

Current `SupersetRestClient.execute_sql_raw` can receive SQL Lab `query`
metadata and may poll `/api/v1/sqllab/results/`. `_normalize_execution_result`
currently keeps rows, columns, and row count only.

Add audit extraction:

```python
def _normalize_audit_info(payload: dict[str, Any]) -> AuditInfo | None:
    result = _result(payload)
    query = result.get("query") if isinstance(result.get("query"), dict) else {}
    return AuditInfo(
        adapter="rest",
        query_id=query.get("id") or result.get("query_id"),
        results_key=query.get("resultsKey") or result.get("resultsKey"),
        executed_sql=query.get("executedSql") or query.get("executed_sql"),
        database_id=query.get("database_id"),
        catalog_name=query.get("catalog"),
        schema_name=query.get("schema"),
        row_limit=query.get("limit"),
        source="sqllab_rest",
    )
```

Attach this to `ExecutionResult.audit`.

When polling by `resultsKey`, preserve original query metadata:

```python
response = self.request("POST", "/api/v1/sqllab/execute/", json=payload)
query = response.get("query")
if needs_poll:
    results = self.get_sqllab_results_raw(str(query["resultsKey"]))
    if isinstance(query, dict):
        results.setdefault("query", query)
    return results
```

Modify `superset_ai_agent/integrations/superset/mcp.py` to map MCP execution
metadata into `AuditInfo(adapter="mcp", source="mcp_execute_sql")` when
available.

Document `LocalSupersetClient` as development-only. It imports Superset and
calls `database.get_df`; it should not be the recommended governed adapter for
Wren-enabled deployments.

## 9. Frontend API and Type Changes

Modify `superset-frontend/src/SqlLab/components/AiAgentPanel/api.ts`.

Add:

```ts
export interface InsightCard {
  title: string;
  value?: string | number | null;
  metric?: string | null;
  category?: string | null;
  description?: string | null;
  severity: 'info' | 'success' | 'warning';
}

export interface ChartEncoding {
  x?: string | null;
  y?: string | string[] | null;
  series?: string | null;
  time?: string | null;
  label?: string | null;
}

export interface ChartSpec {
  type: 'bar' | 'line' | 'table';
  title?: string | null;
  encoding: ChartEncoding;
  options: Record<string, unknown>;
}

export interface AuditInfo {
  adapter?: 'rest' | 'mcp' | 'local' | null;
  query_id?: string | number | null;
  results_key?: string | null;
  executed_sql?: string | null;
  database_id?: number | null;
  catalog_name?: string | null;
  schema_name?: string | null;
  row_limit?: number | null;
  timeout_seconds?: number | null;
  source?: string | null;
}

export interface WrenContextArtifact {
  enabled: boolean;
  available: boolean;
  matched_models: string[];
  example_ids: string[];
  document_ids: string[];
  semantic_layer_version?: string | null;
  indexing_status?: string | null;
  dry_plan?: Record<string, unknown> | null;
  warnings: string[];
}

export interface ExecutionResult {
  columns: string[];
  rows: Record<string, unknown>[];
  row_count: number;
  audit?: AuditInfo | null;
  is_truncated?: boolean;
}

export interface ConversationScope {
  database_id: number;
  catalog_name?: string | null;
  schema_name?: string | null;
  dataset_ids: number[];
}
```

Replace inline execution-result shapes in `AgentQueryResponse` and
`ConversationArtifact` with `ExecutionResult`.

Extend both response types with:

```ts
answer_summary?: string | null;
insight_cards?: InsightCard[];
chart_spec?: ChartSpec | null;
data_preview?: ExecutionResult | null;
audit?: AuditInfo | null;
recommended_followups?: string[];
wren_context?: WrenContextArtifact | null;
```

Add semantic-layer API types:

```ts
export interface SemanticUpdate { ... }
export interface SemanticDocument { ... }
export interface SemanticLayerReviewRequest { ... }
export interface SemanticLayerState { ... }
```

Add API functions:

```ts
uploadSemanticDocument(...)
listSemanticDocuments(...)
getSemanticDocument(...)
reviewSemanticDocument(...)
rebuildSemanticLayerIndex(...)
getSemanticLayerState(...)
createSemanticLayerEventSource(...)
```

## 10. Frontend Renderer Changes

Keep `superset-frontend/src/SqlLab/components/AiAgentPanel/index.tsx` as the
container, but move new rendering into focused sibling components.

Add:

```text
superset-frontend/src/SqlLab/components/AiAgentPanel/InsightCards.tsx
superset-frontend/src/SqlLab/components/AiAgentPanel/AiChartPreview.tsx
superset-frontend/src/SqlLab/components/AiAgentPanel/DataPreviewToggle.tsx
superset-frontend/src/SqlLab/components/AiAgentPanel/AuditInfoPanel.tsx
superset-frontend/src/SqlLab/components/AiAgentPanel/FollowupQuestions.tsx
superset-frontend/src/SqlLab/components/AiAgentPanel/SemanticLayerStateBadge.tsx
```

Artifact render order:

1. answer summary card
2. insight cards
3. chart preview
4. `Data - N rows` toggle and table
5. SQL collapsible
6. validation collapsible
7. trace collapsible
8. audit collapsible
9. Wren context collapsible when warnings/planning metadata exist
10. recommended follow-up buttons

Phase 1 chart renderer:

- Use a lightweight custom renderer in `AiChartPreview.tsx`.
- For `bar`, render horizontal or vertical bars from returned rows.
- For `line`, render a simple SVG polyline.
- For `table`, show a table-only fallback message.

Do not wire `@superset-ui/core` `SuperChart` in Phase 1. Use `SuperChart` when
the artifact can be translated into chart plugin form data or when saved chart
creation is in scope.

Semantic-layer UI target:

- The AI panel should remain the conversation and artifact rendering surface.
- Show the selected database/schema and semantic-layer status with
  `SemanticLayerStateBadge`.
- Add an "Open semantic layer" action that dispatches the SQL Lab
  `openSemanticLayerEditor` action described in section 21.
- Put MDL directory browsing, upload, delete, validation, enrichment review, and
  materialization in `SemanticLayerEditor`, not in the AI panel.
- The existing `SemanticLayerDrawer` can remain as a transitional UI for the
  implemented upload/review baseline, but new MDL-directory behavior should be
  built in the SQL Lab editor tab.

## 11. Governance and Security Checks

Execution checks:

- `TextToSqlGraph._execute_sql` continues to call only
  `self.superset_client.execute_sql`.
- `ConversationGraph._execute_sql` continues to call only
  `self.superset_client.execute_sql`.
- Wren client has no public execution method.
- `WREN_EXECUTION_ENABLED=true` fails at startup.

Superset enforcement remains in Superset:

- REST SQL Lab path:
  - `superset/sqllab/api.py::SqlLabRestApi.execute_sql_query`
  - `superset/sqllab/validators.py::CanAccessQueryValidatorImpl.validate`
  - `superset/security/manager.py::raise_for_access`
  - `superset/sql_lab.py` for row limits, timeouts, DML checks, RLS in SQL Lab
- MCP path:
  - `superset/mcp_service/sql_lab/tool/execute_sql.py::execute_sql`
  - governed by MCP auth/tool permissions and `Database.execute`
  - document differences from REST strict dataset matching
- Local path:
  - development only
  - not recommended for governed Wren deployments

Document security:

- Store checksums.
- Enforce file size and content-type allowlists.
- Extract plain text only.
- Strip or escape HTML.
- Never use unreviewed proposed updates in Wren context.
- Never infer permissions from documents.
- Keep source document IDs on all semantic updates.

Audit display:

- Show adapter, query id, result key, row limit, schema, and source when
  available.
- Do not expose secrets, tokens, full connection URIs, or hidden Superset
  metadata.
- Prefer query id over raw audit payloads in UI.

## 12. Test Plan

Backend tests:

```text
tests/unit_tests/superset_ai_agent/test_schemas.py
tests/unit_tests/superset_ai_agent/test_artifact_insights.py
tests/unit_tests/superset_ai_agent/test_artifact_charts.py
tests/unit_tests/superset_ai_agent/test_graph.py
tests/unit_tests/superset_ai_agent/test_conversation_graph.py
tests/unit_tests/superset_ai_agent/test_conversation_sqlalchemy_store.py
tests/unit_tests/superset_ai_agent/test_config.py
tests/unit_tests/superset_ai_agent/test_identity.py
tests/unit_tests/superset_ai_agent/test_persistence_database.py
tests/unit_tests/superset_ai_agent/test_wren_client.py
tests/unit_tests/superset_ai_agent/test_wren_mdl_exporter.py
tests/unit_tests/superset_ai_agent/test_semantic_layer_documents.py
tests/unit_tests/superset_ai_agent/test_semantic_layer_review.py
tests/unit_tests/superset_ai_agent/test_semantic_layer_indexer.py
tests/unit_tests/superset_ai_agent/test_semantic_layer_sqlalchemy_store.py
tests/unit_tests/superset_ai_agent/test_semantic_layer_file_storage.py
tests/unit_tests/superset_ai_agent/test_superset_client.py
```

Required backend coverage:

- schema compatibility with old response payloads
- identity provider resolves static identity in development
- signed identity provider rejects unsigned or tampered headers
- SQLAlchemy migrations create all agent persistence tables
- SQLAlchemy conversation store matches in-memory store behavior
- SQLAlchemy conversation store filters every read/write by `owner_id`
- persisted messages preserve sequence order
- artifact replacement preserves new analytics fields
- soft-deleted conversations are excluded from list/get
- insight generation for zero rows
- insight generation for one row
- insight generation for categorical plus numeric rows
- chart inference for bar, line, and table fallback
- `TextToSqlGraph` with `execute=false`
- `TextToSqlGraph` with `execute=true`
- `ConversationGraph` artifact generation before reflection
- approved SQL artifact replacement preserves new fields
- Wren disabled
- Wren enabled but unavailable
- Wren dry-plan success
- Wren dry-plan failure
- `WREN_EXECUTION_ENABLED=true` fails closed
- database-backed persistence fails when static identity is used without the
  explicit development override
- document upload allowlist rejection
- document file storage writes bytes outside the DB and records `storage_uri`
- document extraction and proposed update creation
- review approval and rejection
- index rebuild emits events
- semantic-layer SQLAlchemy store persists documents, updates, versions, and
  events across store instances
- unreviewed document updates are not indexed
- unapproved documents do not affect Wren context
- REST audit normalization preserves query metadata
- invalid SQL never reaches `SupersetClient.execute_sql`
- row limit passed to `SupersetClient.execute_sql`

Frontend tests:

```text
superset-frontend/src/SqlLab/components/AiAgentPanel/api.test.ts
superset-frontend/src/SqlLab/components/AiAgentPanel/index.test.tsx
superset-frontend/src/SqlLab/components/AiAgentPanel/InsightCards.test.tsx
superset-frontend/src/SqlLab/components/AiAgentPanel/AiChartPreview.test.tsx
superset-frontend/src/SqlLab/components/AiAgentPanel/DataPreviewToggle.test.tsx
superset-frontend/src/SqlLab/components/AiAgentPanel/AuditInfoPanel.test.tsx
superset-frontend/src/SqlLab/components/AiAgentPanel/FollowupQuestions.test.tsx
superset-frontend/src/SqlLab/components/AiAgentPanel/SemanticLayerStateBadge.test.tsx
```

Required frontend coverage:

- existing SQL rendering remains
- validation status remains
- execution result table still renders
- insight cards render
- chart renders or falls back
- data toggle opens and closes
- audit info panel renders available metadata
- follow-up click sends a conversation message
- semantic-layer status renders the selected database/schema scope
- "Open semantic layer" dispatches the SQL Lab semantic editor action
- full MDL editor behavior is covered by the `SemanticLayerEditor` tests in
  section 21

Governance tests:

- Wren direct execution is not called.
- Unauthorized SQL failures still surface through Superset adapter errors.
- Invalid or non-read-only SQL does not reach `SupersetClient.execute_sql`.
- REST row limits are preserved through `queryLimit`.
- MCP/local adapter differences are documented and tested at the adapter layer.

## 13. Rollout Plan

### Phase 0: Persistence And Identity Foundation

Implemented baseline:

- `superset_ai_agent/auth.py`
- `SupersetSessionIdentityProvider`
- signed identity support for trusted proxy deployments
- `superset_ai_agent/persistence/*`
- SQLAlchemy/Alembic dependencies
- `SqlAlchemyConversationStore`
- `SqlAlchemySemanticLayerStore`
- local file document storage
- Docker volume for SQLite development
- Postgres-ready `AI_AGENT_DATABASE_URL`

Remaining follow-up:

- replace `Base.metadata.create_all` with real Alembic revisions;
- add semantic project/grant/access-proof tables;
- add object storage for uploaded document bytes.

Keep `InMemoryConversationStore` available for tests and local throwaway runs,
but document that production persistence requires non-static identity.

### Phase 1A: Analytics Artifacts, No Wren

Implemented baseline:

- schema extensions
- deterministic result profiling
- insight cards
- answer summary
- chart spec
- data preview
- audit normalization
- frontend renderers

No Wren dependency is required.

### Phase 1B: Wren Context and Examples, Read-Only

Implement:

- `WrenClient`
- config/env wiring
- graph node after `load_context`
- Wren context artifact in traces/responses
- example recall

Implemented baseline:

- `superset_ai_agent/integrations/wren/client.py::FileWrenClient` reads local
  MDL and memory files.
- `superset_ai_agent/integrations/wren/factory.py::create_wren_client` fails
  closed when `WREN_EXECUTION_ENABLED=true`.
- `TextToSqlGraph._load_wren_context` and
  `ConversationGraph._load_wren_context` attach optional context artifacts.

Remaining follow-up:

- add a real Wren API adapter once the stable Wren document/context/dry-plan
  contracts are confirmed;
- keep the file-backed adapter as the deterministic local/test fallback.

### Phase 1C: Wren Dry-Plan, Optional

Implement:

- optional dry-plan node before local validation
- dry-plan metadata in `WrenContextArtifact`
- fail-soft behavior when Wren planning fails

Superset validation and execution remain authoritative.

Implemented baseline:

- `TextToSqlGraph._dry_plan_with_wren` and
  `ConversationGraph._dry_plan_with_wren` collect planning-only metadata when
  `WREN_DRY_PLAN_ENABLED=true`.
- The result is stored in `WrenContextArtifact.dry_plan`.

Remaining follow-up:

- replace the file-backed dry-plan metadata with a real Wren dry-plan adapter
  when available;
- add contract tests that prove Wren planning cannot veto, rewrite, or execute
  SQL outside Superset validation/execution.

### Phase 1D: Document Upload, Review, and Real-Time Indexing

Implement:

- semantic-layer API routes
- SQLAlchemy-backed document/review store
- local file document storage
- extraction allowlist
- proposed semantic updates
- review UI
- SSE events
- approved update indexing into Wren context

Only approved updates affect context.

Implemented baseline:

- `superset_ai_agent/semantic_layer/documents.py::create_document` validates,
  stores, extracts, and proposes deterministic semantic updates.
- `superset_ai_agent/semantic_layer/review.py::apply_review` persists human
  review decisions.
- `superset_ai_agent/semantic_layer/indexer.py::rebuild_index` creates a
  reviewed semantic overlay and `WrenContextArtifact.context_items`.
- `superset_ai_agent/semantic_layer/runtime.py::merge_indexed_semantic_context`
  merges the latest reviewed semantic overlay into one-shot and conversation
  Wren context.
- `superset-frontend/src/SqlLab/components/AiAgentPanel/SemanticLayerDrawer.tsx`
  supports upload, approve-all review, rebuild, and state refresh.

Remaining follow-up:

- replace the transitional drawer flow with the SQL Lab semantic editor tab in
  section 21;
- add granular frontend review/edit/reject controls for MDL files and
  Markdown-enriched proposals;
- continue hardening object-storage-backed document retrieval and checksum
  verification for any future raw-download route;
- validate the Wren HTTP document-ingestion adapter against the deployed Wren
  API contract;
- merge reviewed semantic context into one-shot `/agent/query` without losing
  per-user scoping.

### Phase 2: Superset-To-Wren MDL Export

Implement:

- `mdl_exporter.py`
- permission-filtered MDL generation
- semantic overlay from reviewed documents
- versioned `semantic_layer_version`

### Phase 3: Rich Semantic Model Lifecycle

Implement:

- admin-managed semantic models
- richer memory lifecycle
- examples gallery
- conflict resolution for metric/description updates
- optional translation from `ChartSpec` to Superset Explore/chart flows

## 14. Revised Product Direction: Schema-Scoped Wren Semantic Layers

The next increment should treat Wren semantic layers as collaborative,
schema-scoped assets rather than private conversation attachments. A database
URI identifies the physical database, but the semantic-layer unit should be one
schema within that database because a schema is the natural collection of
related tables. A user who can prove access to a database should be able to
discover all Wren semantic layers associated with schemas in that database, and
then run the agent against exactly one selected database/schema pair.

The access model is intentionally separate from SQL execution:

```text
Superset / browser identity
  -> proves user identity and request-scoped Superset credentials
  -> proves database access through Superset or URI validation
  -> resolves matching schema-level Wren semantic projects by DB fingerprint
  -> user selects one database/schema for the agent run
  -> filters semantic context to that schema and the user's partial/full access
  -> still executes SQL only through SupersetClient.execute_sql
```

Terms used in the follow-up design:

| Term | Meaning |
| --- | --- |
| Analytics database | The user's warehouse or OLTP database queried through Superset SQL Lab. |
| Analytics catalog | Optional namespace used by engines that expose catalogs. It should be included in the project key where supported. |
| Analytics schema | The selected schema inside the analytics database/catalog. This is the primary Wren semantic-layer boundary. |
| Superset metadata DB | Superset's own metadata database containing dashboards, datasets, users, databases, SQL Lab queries, semantic layers, etc. |
| Agent-owned DB | The operational DB configured by `AI_AGENT_DATABASE_URL`, used by `superset_ai_agent` for conversations, documents, Wren semantic projects, versions, access proofs, events, and caches. |
| Wren semantic project | A shareable semantic-layer project keyed by a canonical database URI fingerprint plus optional catalog and schema name. There should normally be one active project per database fingerprint/catalog/schema tuple. |
| URI fingerprint | A one-way hash of a normalized database connection target. It must not include credentials or raw secrets. |
| Access proof | A durable or short-lived record that a Superset user can access a database, schema, dataset subset, or matching URI. |

This design allows the AI agent to behave as a database wrapper/editor while
preserving the important governance rule: Wren controls semantics and planning,
Superset controls execution.

Relationship between databases, schemas, and semantic layers:

```text
database URI / Superset Database
  -> one database URI fingerprint
  -> optional catalogs
  -> many discovered schemas
  -> one Wren semantic project per catalog/schema tuple
  -> many tables, metrics, examples, documents, and versions inside that schema project
```

The user-facing label can remain `<database_label>.<schema_name>` for engines
without catalogs. For catalog-aware engines, display
`<database_label>.<catalog_name>.<schema_name>` where that avoids ambiguity.

Query-time rule:

- the UI must require a database and schema selection before a Wren-backed agent
  run;
- the backend should reject Wren semantic retrieval when `schema_name` is
  missing and more than one schema project is available for the database;
- the graph should load at most one schema-level semantic project for a run;
- cross-schema analysis should be treated as a later explicit workflow, not the
  default conversational query path.

Large-schema rule:

- a schema project can cover hundreds of tables, but the graph must not place
  the full project in the prompt;
- Wren retrieval should return a small ranked subset of models, fields,
  metrics, examples, and documents within the selected schema;
- the prompt should receive a schema-level synopsis plus top-k table/model
  candidates, not every table in the schema;
- table discovery and disambiguation should happen inside the selected schema
  before SQL generation.

## 15. Clarification: Agent-Owned DB Migrations

The "agent-owned DB migrations" are not migrations for user data warehouses.
They are also not Superset core metadata migrations unless an operator
intentionally points `AI_AGENT_DATABASE_URL` at the same physical database with
separate agent-owned tables.

They are Alembic migrations for the standalone AI-agent operational database:

```text
AI_AGENT_DATABASE_URL=sqlite:///./.data/ai_agent.db
AI_AGENT_DATABASE_URL=postgresql+psycopg://superset_ai_agent:...@host/db
```

Data stored in this DB:

| Data | Current/planned tables | Notes |
| --- | --- | --- |
| Conversations | `ai_agent_conversations`, `ai_agent_messages`, `ai_agent_artifacts` | Chat history and optional artifacts. |
| Uploaded documents | `ai_agent_semantic_documents`, `ai_agent_semantic_updates` | Metadata, extracted text, review status, checksums, storage URI, and `project_id`. Raw bytes should live in file/object storage. |
| Semantic versions | `ai_agent_semantic_layer_versions` | Reviewed Wren context overlays, materialization metadata, source update IDs, and `project_id`. |
| MDL files | New `ai_agent_semantic_mdl_files` | One row per MDL YAML file in a schema project, including content, path, status, validation state, source type, and source document reference. |
| Wren retrieval/cache | `ai_agent_wren_context_cache` | Optional derived context cache. |
| Semantic events | `ai_agent_events` | Upload/review/index events for UI polling/SSE. |
| Wren project catalog | New `ai_agent_semantic_projects` | Shareable schema-scoped semantic layer registry keyed by DB fingerprint plus optional catalog plus schema. |
| Access and sharing | New `ai_agent_semantic_project_grants`, `ai_agent_semantic_access_proofs` | Read/write/admin access and DB URI/database/schema proof records. |

Data not stored in this DB:

- user warehouse table data, except small result previews already persisted in
  assistant artifacts when enabled;
- Superset passwords, database credentials, or raw SQLAlchemy URIs;
- Superset RBAC role definitions;
- Wren direct query results.

Replace `superset_ai_agent/persistence/database.py::run_migrations`, which
currently calls `Base.metadata.create_all`, with a real Alembic lifecycle.

Add:

```text
superset_ai_agent/persistence/alembic.ini
superset_ai_agent/persistence/migrations/env.py
superset_ai_agent/persistence/migrations/script.py.mako
superset_ai_agent/persistence/migrations/versions/0001_initial_agent_tables.py
superset_ai_agent/persistence/migrations/versions/0002_semantic_projects_access.py
```

`0001_initial_agent_tables.py` should create the tables already represented by
`superset_ai_agent/persistence/models.py`.

`0002_semantic_projects_access.py` should add:

```python
ai_agent_semantic_projects
  id: string uuid primary key
  name: string
  description: text nullable
  owner_id: string index
  database_uri_fingerprint: string index not null
  database_backend: string nullable
  database_label: string nullable
  catalog_name: string nullable
  schema_name: string index not null
  schema_display_name: string nullable
  default_database_id: int nullable
  visibility: "private" | "db_access" | "custom"
  status: "active" | "archived"
  current_version_id: string nullable
  created_at, updated_at, deleted_at
  partial unique index on
    (database_uri_fingerprint, coalesce(catalog_name, ''), schema_name)
    where deleted_at is null

ai_agent_semantic_project_grants
  id: string uuid primary key
  project_id: string index
  grantee_type: "user" | "group" | "db_access"
  grantee_id: string
  permission: "read" | "write" | "admin"
  created_by: string
  created_at

ai_agent_semantic_access_proofs
  id: string uuid primary key
  owner_id: string index
  proof_type: "superset_database" | "superset_dataset" | "validated_uri"
  database_id: int nullable
  schema_names: json not null
  dataset_ids: json not null
  database_uri_fingerprint: string index not null
  access_level: "partial" | "full"
  expires_at: datetime nullable
  created_at

ai_agent_semantic_mdl_files
  id: string uuid primary key
  project_id: string index not null
  path: string not null
  filename: string not null
  content: text not null
  content_type: "application/x-yaml" | "text/yaml"
  source_type: "uploaded_mdl" | "manual" | "enriched_markdown"
  status: "draft" | "active" | "deleted"
  validation: json nullable
  checksum: string index not null
  source_document_id: string nullable
  created_by: string
  updated_by: string
  created_at, updated_at, deleted_at
  partial unique index on (project_id, path) where deleted_at is null

existing tables
  ai_agent_semantic_documents.project_id: string nullable index
  ai_agent_semantic_documents.catalog_name: string nullable
  ai_agent_semantic_updates.project_id: string nullable index
  ai_agent_semantic_layer_versions.project_id: string nullable index
  ai_agent_semantic_layer_versions.catalog_name: string nullable
  ai_agent_events.project_id: string nullable index
```

Implementation files:

```text
superset_ai_agent/persistence/database.py
superset_ai_agent/persistence/models.py
superset_ai_agent/semantic_layer/projects.py
superset_ai_agent/semantic_layer/access.py
tests/unit_tests/superset_ai_agent/test_persistence_migrations.py
tests/unit_tests/superset_ai_agent/test_semantic_layer_access.py
tests/unit_tests/superset_ai_agent/test_semantic_layer_projects.py
```

Required behavior:

- `run_migrations(config)` runs Alembic to head.
- `create_all_for_tests(engine)` may exist only for isolated unit tests.
- SQLAlchemy stores must work after Alembic migration without calling
  `Base.metadata.create_all`.
- Migrations are idempotent.
- Raw database URIs and credentials are never persisted.
- A semantic project is uniquely identified by
  `(database_uri_fingerprint, catalog_name, schema_name)` while active. For
  engines without catalogs, `catalog_name` is null/empty.
- Active MDL files must contain validated YAML. Markdown uploads are source
  documents and may create proposed YAML MDL files only after review.
- Existing scope-based semantic documents, versions, and events may keep
  `project_id` null after the migration. Compatibility routes should resolve a
  schema project from the stored scope and backfill `project_id` lazily before
  exposing those records in the SQL Lab semantic editor.
- DB-level URI proofs can grant read discovery for all schema projects under the
  matching fingerprint, but a query run still chooses one schema project.

## 16. URI-Derived Semantic-Layer Access

The proposed Wren scoping model should be implemented as a semantic access
resolver that admits users into semantic projects when they can prove database
access. The resolver has two jobs:

1. discover schema projects the user can see for a database fingerprint;
2. select exactly one schema project for an agent run.

Access sources:

| Source | Proof | Access effect |
| --- | --- | --- |
| Superset database access | `security_manager.raise_for_access(database=database)` succeeds through Superset REST/session behavior, or SQL Lab execution permissions prove access indirectly. | Read discovery for all schema projects under the matching DB fingerprint; project creation for schemas the user can access; optional write on existing `visibility=db_access` projects only when full schema/database proof and `semantic_full_access_grants_write=true`. |
| Superset schema/dataset/table access | Permission-filtered `AgentContext.datasets` contains only authorized datasets for the requested schema. | Partial project read access filtered to the requested schema and authorized dataset/table scopes. |
| Valid user-provided URI | Superset can validate a connection to the URI and the normalized URI fingerprint matches existing schema projects. | Read discovery for all schema projects found for that DB fingerprint; query-time use still requires choosing one schema. |
| Explicit semantic grant | Agent project grant or future Superset semantic-layer permission. | Read/write/admin as granted. |

For the standalone FastAPI agent, Superset permission proof should come through
Superset REST calls made with the current user's session, not by importing
`superset.security_manager` directly. Direct `security_manager` checks belong
only in Superset core endpoints or commands if URI-derived semantic access is
later promoted into Superset itself.

Do not compare raw URIs. Add:

```text
superset_ai_agent/semantic_layer/uri_fingerprint.py
```

Functions:

```python
def normalize_database_uri_for_fingerprint(uri: str) -> NormalizedDatabaseUri:
    """Strip credentials and normalize driver, host, port, database, and query keys."""


def database_uri_fingerprint(uri: str, *, salt: str | None = None) -> str:
    """Return a one-way fingerprint suitable for matching semantic projects."""


def fingerprint_superset_database(database: DatabaseSummary) -> str | None:
    """Compute a fingerprint when Superset exposes enough non-secret connection metadata."""
```

Normalization rules:

- strip username, password, tokens, and secret query parameters;
- lowercase scheme/driver and host;
- normalize default ports;
- preserve database name, catalog, and schema where they identify a logical DB;
- include non-secret connection parameters only if they affect the logical
  target;
- use an operator-configured salt so hashes are not portable across deployments.

Config additions:

```python
semantic_access_mode: Literal[
    "superset_only",
    "db_uri_match",
    "superset_or_uri",
] = "superset_or_uri"
semantic_uri_fingerprint_salt: str | None = None
semantic_uri_proof_ttl_seconds: int = 3600
semantic_uri_match_requires_validation: bool = True
semantic_partial_access_enabled: bool = True
semantic_full_access_grants_write: bool = False
```

Environment variables:

```text
AI_AGENT_SEMANTIC_ACCESS_MODE=superset_or_uri
AI_AGENT_SEMANTIC_URI_FINGERPRINT_SALT
AI_AGENT_SEMANTIC_URI_PROOF_TTL_SECONDS
AI_AGENT_SEMANTIC_URI_MATCH_REQUIRES_VALIDATION=true
AI_AGENT_SEMANTIC_PARTIAL_ACCESS_ENABLED=true
AI_AGENT_SEMANTIC_FULL_ACCESS_GRANTS_WRITE=false
```

Add:

```text
superset_ai_agent/semantic_layer/access.py
```

Core types:

```python
class SemanticPermission(str, Enum):
    READ = "read"
    WRITE = "write"
    ADMIN = "admin"


class SemanticAccessLevel(str, Enum):
    PARTIAL = "partial"
    FULL = "full"


class SemanticAccessProof(BaseModel):
    owner_id: str
    proof_type: Literal["superset_database", "superset_dataset", "validated_uri"]
    database_id: int | None = None
    catalog_names: list[str] = Field(default_factory=list)
    schema_names: list[str] = Field(default_factory=list)
    dataset_ids: list[int] = Field(default_factory=list)
    database_uri_fingerprint: str
    access_level: SemanticAccessLevel
    expires_at: datetime | None = None


class SemanticAccessDecision(BaseModel):
    project_id: str
    catalog_name: str | None = None
    schema_name: str
    permission: SemanticPermission
    access_level: SemanticAccessLevel
    allowed_dataset_ids: list[int] = Field(default_factory=list)
    reason: str
```

Core service:

```python
class SemanticAccessService:
    def resolve_projects_for_scope(
        self,
        *,
        identity: AgentIdentity,
        agent_context: AgentContext,
        supplied_uri: str | None = None,
        requested_permission: SemanticPermission = SemanticPermission.READ,
    ) -> list[SemanticAccessDecision]:
        """Return schema projects the user can discover for this DB/scope."""

    def resolve_project_for_run(
        self,
        *,
        identity: AgentIdentity,
        agent_context: AgentContext,
        catalog_name: str | None = None,
        schema_name: str,
        supplied_uri: str | None = None,
    ) -> SemanticAccessDecision:
        """Return exactly one schema project for an agent run."""

    def assert_project_permission(
        self,
        *,
        identity: AgentIdentity,
        project_id: str,
        permission: SemanticPermission,
        agent_context: AgentContext | None,
        supplied_uri: str | None = None,
    ) -> SemanticAccessDecision:
        """Raise if the user cannot perform the semantic-layer operation."""
```

Graph integration:

- `TextToSqlGraph._load_wren_context` and
  `ConversationGraph._load_wren_context` should call the access service after
  `load_context`.
- One-shot and conversation requests should call `resolve_project_for_run`
  using `request.schema_name`.
- Wren receives only the selected schema project for the current run.
- If `schema_name` is missing, the graph should skip Wren context or return a
  structured error asking the user to select a schema; it should not load all
  projects for the database into one prompt.
- If access is partial, filter Wren context items by
  `allowed_dataset_ids`, table names, or model scopes before prompt assembly.

Large-schema query-time retrieval:

```text
selected database + optional catalog + selected schema
  -> resolve one schema project
  -> retrieve schema synopsis
  -> rank candidate tables/models/metrics/examples for the question
  -> include only top-k candidates in the prompt
  -> generate SQL restricted to the selected schema
```

Add retrieval controls to `superset_ai_agent/config.py::AgentConfig`:

```python
wren_schema_table_candidate_limit: int = 12
wren_schema_metric_candidate_limit: int = 20
wren_schema_example_candidate_limit: int = 5
wren_schema_document_candidate_limit: int = 5
wren_schema_context_token_budget: int = 6000
```

Environment variables:

```text
WREN_SCHEMA_TABLE_CANDIDATE_LIMIT
WREN_SCHEMA_METRIC_CANDIDATE_LIMIT
WREN_SCHEMA_EXAMPLE_CANDIDATE_LIMIT
WREN_SCHEMA_DOCUMENT_CANDIDATE_LIMIT
WREN_SCHEMA_CONTEXT_TOKEN_BUDGET
```

Extend `WrenContextArtifact` or add a nested retrieval artifact:

```python
class WrenRetrievalArtifact(BaseModel):
    project_id: str
    schema_name: str
    candidate_table_names: list[str] = Field(default_factory=list)
    candidate_metric_names: list[str] = Field(default_factory=list)
    candidate_example_ids: list[str] = Field(default_factory=list)
    omitted_table_count: int = 0
    context_truncated: bool = False
```

Implementation files:

```text
superset_ai_agent/semantic_layer/retrieval.py
superset_ai_agent/integrations/wren/client.py
superset_ai_agent/graph.py
superset_ai_agent/conversation_graph.py
tests/unit_tests/superset_ai_agent/test_semantic_layer_retrieval.py
```

Required behavior:

- Retrieval never returns the full schema project when candidate counts exceed
  configured limits.
- The trace records selected schema, selected project, candidate counts, and
  truncation.
- SQL prompts include the selected schema name and should prefer fully
  qualified table references when the dialect supports them.
- Follow-up questions remain scoped to the selected schema unless the user
  explicitly changes schema.

Backend request-scope changes:

- `superset_ai_agent/schemas.py::AgentQueryRequest.schema_name` can remain
  optional for backwards compatibility, but Wren-backed retrieval should require
  it.
- Add optional `catalog_name` to `AgentQueryRequest` and `ConversationScope`.
  It may remain null for engines without catalogs, but must be included in
  semantic project resolution for catalog-aware engines.
- `superset_ai_agent/conversations/schemas.py::ConversationScope.schema_name`
  can remain optional for legacy callers, but the conversation graph should
  require it before resolving a schema project.
- Extend `superset_ai_agent/integrations/superset/client.py::SupersetClient`
  context methods to accept `catalog_name` and `schema_name`:

```python
def list_datasets(
    *,
    database_id: int,
    catalog_name: str | None = None,
    schema_name: str | None = None,
    dataset_ids: list[int] | None = None,
    limit: int = 8,
) -> list[DatasetMetadata]: ...

def get_agent_context(
    *,
    database_id: int,
    catalog_name: str | None = None,
    schema_name: str | None = None,
    dataset_ids: list[int] | None = None,
) -> AgentContext: ...
```

- Update `SupersetMetadataContextProvider.get_context` to pass both catalog and
  schema into the adapter.
- Update REST/MCP/local adapters so automatic dataset discovery is scoped to
  the selected schema where the transport supports schema filtering. If an
  adapter cannot filter by schema, filter normalized `DatasetMetadata` before
  prompt assembly and record a warning trace.
- Add a validation helper:

```python
def require_schema_for_wren(
    *,
    schema_name: str | None,
    wren_enabled: bool,
    semantic_access_enabled: bool,
) -> str:
    """Return schema_name or raise a user-facing validation error."""
```

- Use this helper in:
  - `TextToSqlGraph._load_wren_context`
  - `ConversationGraph._load_wren_context`
  - semantic project document upload/review/index routes
  - frontend submit validation

API additions for schema selection:

```text
GET /agent/databases/{database_id}/schemas
POST /agent/semantic-layer/projects/resolve
```

`GET /agent/databases/{database_id}/schemas` should delegate to Superset REST
or metadata already returned by Superset where possible, using the current
user's session. It should not enumerate schemas with a service account when the
agent is running in user-session mode.

API integration:

```text
POST /agent/semantic-layer/projects/resolve
GET  /agent/semantic-layer/projects
POST /agent/semantic-layer/projects
GET  /agent/semantic-layer/projects/{project_id}
PATCH /agent/semantic-layer/projects/{project_id}
DELETE /agent/semantic-layer/projects/{project_id}
POST /agent/semantic-layer/projects/{project_id}/grants
DELETE /agent/semantic-layer/projects/{project_id}/grants/{grant_id}
```

Document upload should move from only scope-based routes to project-aware
routes:

```text
POST  /agent/semantic-layer/projects/{project_id}/documents
GET   /agent/semantic-layer/projects/{project_id}/documents
PATCH /agent/semantic-layer/projects/{project_id}/documents/{document_id}/review
POST  /agent/semantic-layer/projects/{project_id}/index/rebuild
```

Compatibility:

- Keep the existing scope-based document routes as wrappers that resolve or
  create a project for the scope.
- Do not break existing frontend calls in Phase 1.

Tests:

```text
tests/unit_tests/superset_ai_agent/test_semantic_layer_uri_fingerprint.py
tests/unit_tests/superset_ai_agent/test_semantic_layer_access.py
tests/unit_tests/superset_ai_agent/test_semantic_layer_projects.py
tests/unit_tests/superset_ai_agent/test_graph.py
tests/unit_tests/superset_ai_agent/test_conversation_graph.py
```

Required coverage:

- two URIs with different credentials but same logical target produce the same
  fingerprint;
- two different database targets do not collide in tests;
- a user with Superset database access can discover all `visibility=db_access`
  schema projects under the matching DB fingerprint;
- a user with only schema/dataset access receives filtered context for the
  selected schema project only;
- a user with a valid URI proof can discover matching schema projects but must
  choose one schema for query-time retrieval;
- write/admin actions require explicit project grant or ownership;
- raw URI strings are not persisted.

## 17. Identity And Proxying Recommendation

There are three viable identity/deployment patterns. The recommendation is a
hybrid of Option A and the semantic access resolver above.

| Option | Description | Pros | Cons | Recommendation |
| --- | --- | --- | --- | --- |
| A. Same-origin session pass-through | Browser calls `/ai-agent/*` through the Superset origin. Agent validates `/api/v1/me/` and forwards request cookies/CSRF to Superset REST. | Best behavior parity with Superset users. SQL Lab permissions, database permissions, row limits, RLS, and audit identity stay user-scoped. | Requires reverse proxy or same-origin route setup. URI-derived sharing needs an additional access resolver. | Recommended default. |
| B. Superset signed bridge | A Superset-protected backend route injects short-lived signed identity headers and request-scoped Superset auth material. | Useful when the agent is not directly browser-facing. Avoids trusting browser-provided identity headers. | More moving parts. Must add expiry/nonce and strict header stripping at proxy boundary. | Acceptable enterprise pattern. |
| C. Agent-issued access-proof token | After Superset DB access or URI validation, the agent issues a short-lived token representing semantic-layer access only. | Supports mixed access: basic DB proof unlocks shared Wren semantics even when the user is not a traditional owner of the semantic layer. | Must never grant SQL execution rights. Must be scoped to semantic project, fingerprint, access level, and TTL. | Recommended for URI-derived semantic-layer collaboration, not for SQL execution. |
| D. Service-account-only agent | Agent uses a service account for all Superset calls. | Simple operationally. | Poor user parity; risks bypassing per-user SQL Lab/database/RLS behavior. | Avoid for governed analytics. |

Recommended deployment:

```text
Browser
  -> Superset same-origin /ai-agent proxy
  -> FastAPI agent
      identity: SupersetSessionIdentityProvider
      Superset execution: SUPERSET_AUTH_MODE=user_session
      semantic access: Superset DB proof OR validated URI proof
      Wren execution: disabled
```

Follow-up identity hardening:

- Add `iat`, `exp`, and optional `jti` to signed identity payloads.
- Add `AI_AGENT_SIGNED_IDENTITY_MAX_AGE_SECONDS`.
- Reject role/permission claims in signed identity payloads.
- Add `SemanticAccessProof` tokens only for semantic-layer access, never for
  SQL execution.
- Log semantic access decisions separately from SQL execution audit.

Files:

```text
superset_ai_agent/auth.py
superset_ai_agent/config.py
superset_ai_agent/semantic_layer/access.py
superset_ai_agent/persistence/models.py
tests/unit_tests/superset_ai_agent/test_auth.py
tests/unit_tests/superset_ai_agent/test_semantic_layer_access.py
```

## 18. SQL Lab Audit Marker: Codebase Analysis And Proposal

Confirmed SQL Lab request schema:

- `superset/sqllab/schemas.py::ExecutePayloadSchema` accepts:
  - `database_id`
  - `sql`
  - `client_id`
  - `queryLimit`
  - `sql_editor_id`
  - `catalog`
  - `schema`
  - `tab`
  - `ctas_method`
  - `templateParams`
  - `tmp_table_name`
  - `select_as_cta`
  - `runAsync`
  - `expand_data`
- It does not accept `extra_json`.

Confirmed persistence path:

- `superset/sqllab/sqllab_execution_context.py::SqlJsonExecutionContext`
  reads `client_id`, `sql_editor_id`, and `tab`.
- `SqlJsonExecutionContext.create_query` persists those values on
  `superset/models/sql_lab.py::Query`.
- `Query.client_id` is `String(11)`, unique, and appears as `query.id` in
  `Query.to_dict`.
- `Query.sql_editor_id` is `String(256)`, indexed, and appears as
  `query.sqlEditorId`.
- `Query.tab_name` appears as `query.tab`.
- `Query.to_dict` returns `extra`, but SQL Lab execute does not accept arbitrary
  `extra_json` in the execute payload.

Do not use a fixed `client_id="superset_ai_agent"` because `client_id` is
unique and limited to 11 characters.

Recommended marker strategy:

```python
client_id = short_ai_query_id()  # e.g. "ai" + 9 chars
sql_editor_id = "ai_agent:" + source_hash  # <= 256 chars
tab = "AI Agent"
```

`source_hash` should be a short hash of non-secret metadata:

```text
conversation_id
message_id
artifact_id
request_id
semantic_project_id
```

Add a typed source context:

```python
class SqlExecutionSource(BaseModel):
    source: Literal["superset_ai_agent"] = "superset_ai_agent"
    request_id: str | None = None
    conversation_id: str | None = None
    message_id: str | None = None
    artifact_id: str | None = None
    semantic_project_id: str | None = None
```

Modify `superset_ai_agent/integrations/superset/client.py::SupersetClient`:

```python
def execute_sql(
    self,
    *,
    database_id: int,
    sql: str,
    catalog_name: str | None = None,
    schema_name: str | None = None,
    limit: int = 1000,
    source: SqlExecutionSource | None = None,
) -> ExecutionResult: ...
```

Modify `superset_ai_agent/integrations/superset/rest.py::SupersetRestClient.execute_sql_raw`:

```python
payload = {
    "database_id": database_id,
    "sql": sql,
    "catalog": catalog_name,
    "schema": schema_name,
    "queryLimit": limit,
    "runAsync": False,
    "expand_data": True,
    "client_id": short_ai_query_id(source),
    "sql_editor_id": ai_sql_editor_id(source),
    "tab": "AI Agent",
}
```

Extend `superset_ai_agent/schemas.py::AuditInfo`:

```python
client_id: str | None = None
sql_editor_id: str | None = None
tab: str | None = None
source_hash: str | None = None
```

Extend `_normalize_audit_info` in
`superset_ai_agent/integrations/superset/rest.py` to read:

```python
client_id = query.get("id")
sql_editor_id = query.get("sqlEditorId") or query.get("sql_editor_id")
tab = query.get("tab")
```

Fallback only if needed:

- If Superset rejects `sql_editor_id` or `tab` in a future version, use a SQL
  comment marker.
- Prefer payload fields over SQL comments because comments become part of user
  SQL and can complicate parser behavior or warehouse query logs.

Tests:

```text
tests/unit_tests/superset_ai_agent/test_superset_client.py
tests/unit_tests/superset_ai_agent/test_graph.py
tests/unit_tests/superset_ai_agent/test_conversation_graph.py
```

Required coverage:

- REST payload includes `client_id`, `sql_editor_id`, and `tab`.
- `client_id` is unique and length <= 11.
- Graph execution passes source metadata for conversation and one-shot calls.
- `AuditInfo` includes query id, client id, sql editor id, tab, adapter, row
  limit, and source.
- Non-read-only SQL still never reaches `SupersetClient.execute_sql`.

## 19. Semantic Layer CRUD Target

The semantic-layer target should not be limited to "publication" from the agent
DB into Superset. Users should be able to create, read, update, delete, share,
review, and version semantic layers they have access to, where access can be
derived from database access.

Current Superset semantic-layer facts:

- `superset/semantic_layers/api.py::SemanticLayerRestApi` exposes create,
  update, delete, schema, runtime schema, views, and connections routes.
- `superset/semantic_layers/api.py::SemanticViewRestApi` exposes bulk create,
  update, delete, bulk delete, and structure routes.
- `superset/commands/semantic_layer/create.py::CreateSemanticLayerCommand` and
  `CreateSemanticViewCommand` validate type/uniqueness and create models.
- `superset/commands/semantic_layer/update.py::UpdateSemanticViewCommand`
  checks ownership with `security_manager.raise_for_ownership`.
- `superset/commands/semantic_layer/delete.py::DeleteSemanticViewCommand`
  checks ownership with `security_manager.raise_for_ownership`.
- `superset/semantic_layers/models.py::SemanticView.raise_for_access` allows
  access through all-datasource permission, view `perm`, or layer `perm`.
- `SemanticLayerRestApi.connections` currently filters semantic layers by
  `SemanticLayer.perm` unless the user can access all datasources.

Gap:

- Existing Superset semantic-layer access is permission-string oriented. It
  does not yet encode "user can access a database URI, therefore user can
  access semantic layers built for the same URI."

Recommended next implementation:

1. Add agent-owned Wren semantic project CRUD now.
2. Use Superset REST semantic-layer APIs only for Superset-native objects and
   only through the current user's session.
3. Add a Superset-side semantic access enhancement later if this model should
   become first-class in Superset core.

Agent CRUD routes:

```text
GET    /agent/semantic-layer/projects
POST   /agent/semantic-layer/projects
GET    /agent/semantic-layer/projects/{project_id}
PATCH  /agent/semantic-layer/projects/{project_id}
DELETE /agent/semantic-layer/projects/{project_id}
POST   /agent/semantic-layer/projects/{project_id}/share
DELETE /agent/semantic-layer/projects/{project_id}/share/{grant_id}
GET    /agent/semantic-layer/projects/{project_id}/versions
POST   /agent/semantic-layer/projects/{project_id}/versions/{version_id}/activate
```

Permissions:

| Permission | Allowed actions |
| --- | --- |
| `read` | discover, retrieve context, use in text-to-SQL, view documents/versions. |
| `write` | upload documents, review updates, rebuild index, create versions. |
| `admin` | rename/delete project, manage sharing, archive, publish to Superset. |

Default policy:

- full database/schema proof may create a new schema project;
- existing shared projects grant `read` by default through DB-derived access;
- DB-derived `write` should be opt-in through
  `AI_AGENT_SEMANTIC_FULL_ACCESS_GRANTS_WRITE=true` and only for full
  schema/database proof, never for partial dataset proof;
- `admin`, delete, archive, and sharing changes require ownership or explicit
  project grant.

Semantic project schema:

```python
class SemanticProject(BaseModel):
    id: str
    name: str
    description: str | None = None
    owner_id: str
    database_uri_fingerprint: str
    database_backend: str | None = None
    database_label: str | None = None
    catalog_name: str | None = None
    schema_name: str
    schema_display_name: str | None = None
    default_database_id: int | None = None
    visibility: Literal["private", "db_access", "custom"] = "db_access"
    current_version_id: str | None = None
    status: Literal["active", "archived"] = "active"
```

Superset REST bridge methods:

```python
class SupersetClient(Protocol):
    def list_semantic_layers(self) -> list[dict[str, Any]]: ...
    def create_semantic_layer(self, payload: dict[str, Any]) -> dict[str, Any]: ...
    def update_semantic_layer(self, uuid: str, payload: dict[str, Any]) -> dict[str, Any]: ...
    def delete_semantic_layer(self, uuid: str) -> None: ...
    def create_semantic_views(self, views: list[dict[str, Any]]) -> dict[str, Any]: ...
    def update_semantic_view(self, view_id: int, payload: dict[str, Any]) -> dict[str, Any]: ...
    def delete_semantic_view(self, view_id: int) -> None: ...
```

Implement first in:

```text
superset_ai_agent/integrations/superset/rest.py
```

Do not implement these methods in the local adapter for production use. The
local adapter can raise `NotImplementedError` or remain test-only.

Superset core follow-up if URI-derived sharing becomes first-class:

```text
superset/semantic_layers/models.py
superset/semantic_layers/api.py
superset/commands/semantic_layer/create.py
superset/commands/semantic_layer/update.py
superset/commands/semantic_layer/delete.py
superset/daos/semantic_layer.py
```

Potential Superset model additions:

- `SemanticLayer.database_uri_fingerprint`
- `SemanticLayer.catalog_name`
- `SemanticLayer.schema_name`
- `SemanticLayer.visibility`
- `SemanticLayerAccessGrant`
- `SemanticLayerSourceDatabase`

Potential Superset access function:

```python
def can_access_semantic_layer_via_database(
    *,
    user: User,
    layer: SemanticLayer,
    database: Database | None,
    uri_fingerprint: str | None,
) -> bool:
    """Grant semantic access when the user can access a matching database."""
```

Command hardening:

- Add ownership/access checks to `UpdateSemanticLayerCommand.validate`.
- Add ownership/access checks to `DeleteSemanticLayerCommand.validate`.
- Add create-view permission checks so a user cannot add views to a layer they
  cannot write.
- Keep `SemanticView.raise_for_access` as the read guard for semantic views,
  extended with DB-derived semantic access if accepted.

Tests:

```text
tests/unit_tests/superset_ai_agent/test_semantic_layer_projects.py
tests/unit_tests/superset_ai_agent/test_semantic_layer_access.py
tests/unit_tests/superset_ai_agent/test_superset_client.py
tests/integration_tests/semantic_layers/api_tests.py
tests/integration_tests/semantic_layers/security_tests.py
```

Required coverage:

- users can discover schema projects for matching DB fingerprints;
- DB URI proof lists all schema projects associated with that database
  fingerprint;
- agent runs resolve exactly one selected schema project;
- users can CRUD projects they own or administer;
- users with only read access cannot upload/review/delete;
- users with DB access but no explicit grant can read `visibility=db_access`
  schema projects;
- users with no DB proof cannot read matching project metadata;
- Superset REST CRUD failures propagate cleanly to the agent UI;
- Superset semantic-layer update/delete commands enforce ownership or accepted
  semantic admin access.

## 20. Wren API Adapter And Document Understanding

The current Wren implementation is intentionally file-backed:

- `superset_ai_agent/integrations/wren/client.py::FileWrenClient`
- `superset_ai_agent/semantic_layer/review.py::propose_updates`
- `superset_ai_agent/semantic_layer/indexer.py::rebuild_index`

The next Wren-native increment should add an HTTP adapter while preserving the
no-execution contract.

Config:

```python
wren_adapter: Literal["file", "http"] = "file"
wren_base_url: str | None = None
wren_api_key: str | None = None
wren_timeout_seconds: float = 30.0
```

Add:

```text
superset_ai_agent/integrations/wren/http_client.py
tests/unit_tests/superset_ai_agent/test_wren_http_client.py
```

Allowed methods:

```python
def fetch_context(
    ...,
    project: SemanticProject,
    materialized_project_path: str,
) -> WrenContextArtifact: ...
def recall_examples(..., project: SemanticProject, limit: int) -> list[dict[str, Any]]: ...
def dry_plan(
    ...,
    project: SemanticProject,
    materialized_project_path: str,
) -> dict[str, Any]: ...
def preview_document_updates(..., project: SemanticProject, document: SemanticDocument) -> list[SemanticUpdate]: ...
def build_semantic_overlay(..., project: SemanticProject, approved_updates: list[SemanticUpdate]) -> WrenContextArtifact: ...
```

`materialized_project_path` must point to the selected schema project's
validated active MDL directory, not a global Wren project path.

Forbidden methods:

- `execute`
- `run_sql`
- `query`
- `query_preview`
- any method that returns warehouse rows by bypassing `SupersetClient`.

Tests must assert:

- the public client protocol has no execution method;
- `WREN_EXECUTION_ENABLED=true` fails startup;
- Wren document proposals are not indexed until approved;
- Wren planning failure does not block Superset SQL validation/execution;
- Wren receives only the selected schema project authorized by
  `SemanticAccessService`.

## 21. SQL Lab Semantic Layer Editor UI

### Confirmed SQL Lab Frontend Baseline

Confirmed code facts:

- `superset-frontend/src/SqlLab/components/SqlEditorLeftBar/index.tsx`
  renders the database/catalog/schema selector and then
  `TableExploreTree`.
- `superset-frontend/src/SqlLab/components/TableExploreTree/index.tsx`
  owns the database browser tree state and delegates row rendering to
  `TreeNodeRenderer`.
- `superset-frontend/src/SqlLab/components/TableExploreTree/TreeNodeRenderer.tsx`
  already renders schema row hover actions for refresh and pin/unpin. Schema
  nodes are identified by `identifier === 'schema'`.
- `superset-frontend/src/SqlLab/components/TabbedSqlEditors/index.tsx`
  currently maps `sqlLab.queryEditors` directly to `SqlEditor` tabs. It has no
  mixed tab type for non-SQL editor panels.
- `superset-frontend/src/SqlLab/components/EditorWrapper/index.tsx`
  wraps `EditorHost` but hardcodes `language="sql"`, SQL annotations, SQL
  keyword completion, and SQL run hotkeys.
- YAML editor support already exists in the shared editor stack:
  `superset-frontend/src/core/editors/AceEditorProvider.tsx` maps `yaml` to
  `ConfigEditor`; `superset-frontend/packages/superset-ui-core/src/components/AsyncAceEditor/index.tsx`
  registers `brace/mode/yaml`; `TemplateParamsEditor` already uses
  `EditorHost` with `language="yaml"`.

### Product Decision

Build the semantic-layer editor as a first-class SQL Lab editor tab, opened
from a schema node in the existing left database browser. Do not put this in
the existing AI chat panel as the primary UX.

This matches the desired workflow:

```text
SQL Lab left browser
  -> user chooses database/catalog/schema
  -> schema row action opens Semantic Layer tab
  -> tab edits the schema's MDL directory
  -> active, validated MDL files are materialized to Wren
  -> Wren context/planning feeds the AI agent
  -> SQL execution still goes through SupersetClient
```

Semantic layer naming:

- canonical name: `<database_label>.<schema_name>` or
  `<database_label>.<catalog_name>.<schema_name>` for catalog-aware engines;
- persistent key: `(database_uri_fingerprint, catalog_name, schema_name)`;
- optional catalog should be included in display labels where the database
  backend supports catalogs, but the access and materialization boundary
  remains the selected schema project.

Large database rule:

- the UI must require a selected schema before opening or running a
  Wren-backed agent workflow;
- the runtime must load only the selected schema's semantic project, then apply
  top-k table/metric/example retrieval inside that schema;
- the agent should not combine multiple schema projects in a single run unless
  a later cross-schema feature explicitly designs that behavior.

### Entrypoint: Schema Browser Action

Modify:

```text
superset-frontend/src/SqlLab/components/TableExploreTree/TreeNodeRenderer.tsx
superset-frontend/src/SqlLab/components/TableExploreTree/index.tsx
superset-frontend/src/SqlLab/components/SqlEditorLeftBar/index.tsx
superset-frontend/src/SqlLab/actions/sqlLab.ts
superset-frontend/src/SqlLab/reducers/sqlLab.ts
superset-frontend/src/SqlLab/types.ts
```

Add a schema-level hover action next to refresh/pin:

```tsx
<ActionButton
  label={`open-semantic-layer-${schema}`}
  tooltip={t('Open semantic layer')}
  icon={<Icons.BookOutlined iconSize="m" />}
  onClick={() =>
    openSemanticLayerEditor({
      databaseId: Number(_dbId),
      databaseName,
      catalog,
      schemaName: schema,
    })
  }
/>
```

Implementation detail:

- `TreeNodeRenderer` does not currently know the database display name. Pass it
  from `TableExploreTree` if available from the active query editor/database
  metadata. If only `dbId` is available, the open action can resolve the
  display name after project resolution.
- Keep row click behavior unchanged. The new action must call
  `e.stopPropagation()` like existing schema actions.
- If the user has no selected schema, the entrypoint should be hidden or
  disabled because semantic projects are schema-scoped.

### Mixed SQL Lab Tab Model

The current `tabHistory: string[]` contains query editor IDs only. A semantic
editor tab should not be represented as a `QueryEditor`, because that would
inherit SQL execution state, query history state, and run hotkeys.

Add explicit tab typing in `superset-frontend/src/SqlLab/types.ts`:

```ts
export type SqlLabTabType = 'query' | 'semanticLayer';

export interface SqlLabTabRef {
  id: string;
  type: SqlLabTabType;
}

export interface SemanticLayerEditorTab {
  id: string;
  type: 'semanticLayer';
  projectId?: string;
  databaseId: number;
  databaseName?: string;
  catalog?: string | null;
  schemaName: string;
  title: string;
  activeFileId?: string;
  dirtyFileIds: string[];
  updatedAt?: number;
}
```

Add state:

```ts
semanticLayerEditors: SemanticLayerEditorTab[];
tabHistory: SqlLabTabRef[];
```

Compatibility migration:

- Redux hydration/local storage may still contain `tabHistory: string[]`.
  Reducer initialization should normalize strings to
  `{ type: 'query', id: value }`.
- Existing query editor actions should keep accepting `QueryEditor`.
- `setActiveQueryEditor(queryEditor)` should push a query tab ref.
- New `setActiveSqlLabTab(tab: SqlLabTabRef)` should be used by mixed tabs.

Add actions:

```ts
export const OPEN_SEMANTIC_LAYER_EDITOR = 'OPEN_SEMANTIC_LAYER_EDITOR';
export const SET_ACTIVE_SQL_LAB_TAB = 'SET_ACTIVE_SQL_LAB_TAB';
export const CLOSE_SEMANTIC_LAYER_EDITOR = 'CLOSE_SEMANTIC_LAYER_EDITOR';
export const UPDATE_SEMANTIC_LAYER_EDITOR = 'UPDATE_SEMANTIC_LAYER_EDITOR';

export function openSemanticLayerEditor(input: {
  databaseId: number;
  databaseName?: string;
  catalog?: string | null;
  schemaName: string;
}): SqlLabThunkAction<SqlLabAction> { ... }
```

`openSemanticLayerEditor` should:

- call `resolveSemanticProject({ databaseId, catalogName: catalog, schemaName })`;
- create the project if no accessible project exists and the user has write
  access for that database/schema;
- open or focus a tab with ID
  `semantic-layer:${databaseId}:${catalog ?? ''}:${schemaName}`;
- set `title` to `<database_label>.<schema_name>` or
  `<database_label>.<catalog_name>.<schema_name>` when catalog is present.

Modify `TabbedSqlEditors`:

- Build `items` from both `queryEditors` and `semanticLayerEditors`.
- Query tabs continue to render `SqlEditorTabHeader` and `SqlEditor`.
- Semantic tabs render `SemanticLayerEditorTabHeader` and
  `SemanticLayerEditor`.
- `handleSelect` should route by `SqlLabTabRef.type`.
- `handleEdit(..., 'remove')` should remove query tabs with
  `removeQueryEditor` and semantic tabs with `closeSemanticLayerEditor`.
- `handleEdit(..., 'add')` should keep creating SQL query tabs.
- If all tabs are closed, keep the existing empty SQL tab behavior.

New files:

```text
superset-frontend/src/SqlLab/components/SemanticLayerEditor/index.tsx
superset-frontend/src/SqlLab/components/SemanticLayerEditor/SemanticLayerEditorTabHeader.tsx
superset-frontend/src/SqlLab/components/SemanticLayerEditor/MdlFileBrowser.tsx
superset-frontend/src/SqlLab/components/SemanticLayerEditor/MdlFileTreeNode.tsx
superset-frontend/src/SqlLab/components/SemanticLayerEditor/MdlUploadDialog.tsx
superset-frontend/src/SqlLab/components/SemanticLayerEditor/MdlEditor.tsx
superset-frontend/src/SqlLab/components/SemanticLayerEditor/MdlValidationPanel.tsx
superset-frontend/src/SqlLab/components/SemanticLayerEditor/MdlEnrichmentReview.tsx
superset-frontend/src/SqlLab/components/SemanticLayerEditor/api.ts
superset-frontend/src/SqlLab/components/SemanticLayerEditor/types.ts
```

### Semantic Layer Editor Layout

The tab should behave like an editor, not a drawer:

```text
SemanticLayerEditor
  left: MDL file browser
    - project title: database.schema or database.catalog.schema
    - files as tree/list entries
    - delete action per file
    - upload button fixed at bottom
  center: MDL YAML editor
  right/bottom: validation, Wren materialization status, and enrichment review
```

MDL browser rules:

- Treat a semantic layer as a directory of MDL YAML files.
- Each non-deleted `ai_agent_semantic_mdl_files` row is one browser entry.
- Default file paths should be stable and readable, for example:
  `models/<table_or_topic>.yaml`, `metrics/<metric_group>.yaml`, or
  `docs/<source_name>.generated.yaml`.
- File delete is soft delete through the backend. The UI should ask for
  confirmation if the file is active or dirty.
- Dirty files should show a visual marker in the tab header and browser row.

### Upload And Enrichment Flow

Upload button location:

- bottom of `MdlFileBrowser`;
- use a modal overlay implemented by `MdlUploadDialog`.

Accepted files:

- valid MDL YAML: `.yaml`, `.yml`, `application/x-yaml`, `text/yaml`;
- raw text source for enrichment: `.md`, `.markdown`, `text/markdown`.

Rejected for Phase 1:

- PDFs, spreadsheets, Word documents, ZIP archives, images, and arbitrary text
  files;
- invalid YAML that is not explicitly uploaded as Markdown.

Two-step behavior:

1. Valid MDL YAML:
   - upload file;
   - backend parses YAML and validates MDL structure;
   - create `ai_agent_semantic_mdl_files` row with
     `source_type="uploaded_mdl"` and `status="draft"`;
   - open the file in `MdlEditor` for review;
   - user must explicitly activate the file before it can be materialized.
2. Markdown/raw business context:
   - upload Markdown as a document/source file;
   - call Wren onboarding/enrichment through the no-execution `WrenClient`;
   - return a proposed MDL YAML file;
   - open `MdlEnrichmentReview` with source Markdown side-by-side with the
     generated YAML;
   - user must explicitly save/approve before a new
     `ai_agent_semantic_mdl_files` row becomes active.

Important guardrail:

- Wren enrichment may propose MDL only. It must not automatically mutate active
  MDL files, materialize a project, or execute SQL.

### MDL Editor

Do not reuse `EditorWrapper` directly because it is SQL-specific. Add
`MdlEditor` around `EditorHost`:

```tsx
<EditorHost
  id={`mdl-editor-${fileId}`}
  value={content}
  language="yaml"
  tabSize={2}
  lineNumbers
  annotations={validationAnnotations}
  hotkeys={saveAndValidateHotkeys}
  height="100%"
  width="100%"
  onChange={handleChange}
  onBlur={handleBlur}
/>
```

Rules:

- YAML syntax highlighting comes from existing `EditorHost` YAML support.
- YAML linting should be backend-driven initially: convert
  `MdlValidationResult.errors` and `warnings` into `EditorAnnotation[]`.
- Do not register SQL Lab run-query hotkeys in `MdlEditor`.
- Do not use SQL autocomplete keywords. MDL-specific completion can be added
  later from Wren schema metadata.
- Save should write a draft; activate should require successful validation or
  explicit user confirmation if warnings remain.

### Frontend API And Types

Add to:

```text
superset-frontend/src/SqlLab/components/SemanticLayerEditor/api.ts
superset-frontend/src/SqlLab/components/SemanticLayerEditor/types.ts
```

Types:

```ts
export type MdlFileStatus = 'draft' | 'active' | 'deleted';
export type MdlFileSourceType = 'uploaded_mdl' | 'manual' | 'enriched_markdown';

export interface SemanticProject {
  id: string;
  name: string;
  databaseId?: number;
  databaseLabel?: string;
  catalogName?: string | null;
  schemaName: string;
  currentVersionId?: string;
  access: 'read' | 'write' | 'admin';
}

export interface MdlValidationMessage {
  line?: number;
  column?: number;
  severity: 'error' | 'warning' | 'info';
  message: string;
  code?: string;
}

export interface MdlValidationResult {
  valid: boolean;
  messages: MdlValidationMessage[];
}

export interface MdlFile {
  id: string;
  projectId: string;
  path: string;
  filename: string;
  content: string;
  contentType: 'application/x-yaml' | 'text/yaml';
  sourceType: MdlFileSourceType;
  status: MdlFileStatus;
  validation?: MdlValidationResult;
  checksum: string;
  sourceDocumentId?: string | null;
  updatedAt: string;
}

export interface SemanticSourceDocument {
  id: string;
  projectId: string;
  filename: string;
  contentType: 'text/markdown';
  extractedTextPreview?: string | null;
  status: 'uploaded' | 'extracted' | 'needs_review' | 'approved' | 'indexed' | 'error';
}

export interface MdlEnrichmentProposal {
  sourceDocumentId: string;
  proposedPath: string;
  proposedYaml: string;
  validation: MdlValidationResult;
  warnings: string[];
}
```

API functions:

```text
resolveSemanticProject({ databaseId, catalogName, schemaName })
listMdlFiles(projectId)
getMdlFile(projectId, fileId)
createMdlFile(projectId, payload)
updateMdlFile(projectId, fileId, payload)
deleteMdlFile(projectId, fileId)
uploadMdlFile(projectId, file)
uploadMarkdownForEnrichment(projectId, file)
enrichMarkdown(projectId, documentId)
validateMdlFile(projectId, fileId)
materializeSemanticProject(projectId)
```

Recommended backend endpoints:

```text
POST /agent/semantic-layer/projects/resolve
GET /agent/semantic-layer/projects/{project_id}/mdl-files
POST /agent/semantic-layer/projects/{project_id}/mdl-files
GET /agent/semantic-layer/projects/{project_id}/mdl-files/{file_id}
PATCH /agent/semantic-layer/projects/{project_id}/mdl-files/{file_id}
DELETE /agent/semantic-layer/projects/{project_id}/mdl-files/{file_id}
POST /agent/semantic-layer/projects/{project_id}/mdl-files/upload
POST /agent/semantic-layer/projects/{project_id}/documents/upload-markdown
POST /agent/semantic-layer/projects/{project_id}/documents/{document_id}/enrich
POST /agent/semantic-layer/projects/{project_id}/mdl-files/{file_id}/validate
POST /agent/semantic-layer/projects/{project_id}/materialize
```

### Runtime Materialization

Add:

```text
superset_ai_agent/semantic_layer/mdl_files.py
superset_ai_agent/semantic_layer/mdl_validation.py
superset_ai_agent/integrations/wren/project_materializer.py
tests/unit_tests/superset_ai_agent/test_semantic_layer_mdl_files.py
tests/unit_tests/superset_ai_agent/test_semantic_layer_mdl_api.py
tests/unit_tests/superset_ai_agent/test_wren_project_materializer.py
```

`WrenProjectMaterializer` should:

- load only `status="active"` MDL files for the selected schema project;
- write them to a schema-specific directory such as
  `{AI_AGENT_STORAGE_DIR}/wren/projects/{database_uri_fingerprint}/{catalog_or_default}/{schema_name}/`;
- sanitize file paths so MDL rows cannot write outside the project directory;
- materialize by writing to a temporary directory and atomically renaming it;
- write a manifest with file checksums and project/version IDs;
- return the materialized project path to `WrenClient.fetch_context` and
  `WrenClient.dry_plan`.

Wren runtime behavior:

- Wren receives one materialized project directory for the selected
  `(database_uri_fingerprint, catalog_name, schema_name)`;
- Wren may return context, examples, onboarding proposals, and dry plans;
- Wren must not execute generated SQL;
- generated SQL is validated and executed only through `SupersetClient`.

### Frontend Tests

Add:

```text
superset-frontend/src/SqlLab/components/TableExploreTree/TreeNodeRenderer.test.tsx
superset-frontend/src/SqlLab/components/TabbedSqlEditors/index.test.tsx
superset-frontend/src/SqlLab/components/SemanticLayerEditor/SemanticLayerEditor.test.tsx
superset-frontend/src/SqlLab/components/SemanticLayerEditor/MdlFileBrowser.test.tsx
superset-frontend/src/SqlLab/components/SemanticLayerEditor/MdlUploadDialog.test.tsx
superset-frontend/src/SqlLab/components/SemanticLayerEditor/MdlEditor.test.tsx
superset-frontend/src/SqlLab/components/SemanticLayerEditor/MdlEnrichmentReview.test.tsx
```

Test assertions:

- schema rows expose an "Open semantic layer" action;
- clicking the action opens a semantic tab scoped to the selected database and
  schema;
- SQL tabs still open, close, select, and run as before;
- semantic tabs do not call SQL run actions or render query result panes;
- YAML editor uses `language="yaml"`;
- upload accepts YAML and Markdown and rejects unsupported file types;
- Markdown enrichment requires explicit review before activation;
- deleting an MDL file uses the semantic-layer API and does not remove query
  editor state.

## 22. Remaining Risk Closure Checklist

This section is the authoritative implementation checklist for the next
session. Complete the items in order unless a lower item is needed to unblock a
test. Each item names the risk, the confirmed source state, implementation
steps, required tests, and acceptance criteria.

### R1. Agent-Owned DB Migration Lifecycle

Status: `[COMPLETE]`

Confirmed source state:

- `superset_ai_agent/persistence/database.py::run_migrations` runs Alembic
  `upgrade head` through an agent-local Alembic configuration.
- `superset_ai_agent/persistence/models.py` already contains the conversation,
  artifact, semantic document, semantic project, access proof, and MDL file
  model definitions.
- Store implementations already rely on those models:
  `SqlAlchemyConversationStore`, `SqlAlchemySemanticLayerStore`,
  `SqlAlchemySemanticProjectStore`, and `SqlAlchemyMdlFileStore`.
- `superset_ai_agent/persistence/database.py::create_all_for_tests` is the only
  helper that calls SQLAlchemy `create_all`.
- `AgentConfig.agent_migration_bootstrap` and
  `AI_AGENT_MIGRATION_BOOTSTRAP=error|stamp_existing` cover legacy development
  DB bootstrap behavior.

Implementation steps:

1. `[COMPLETE]` Add Alembic files:

   ```text
   superset_ai_agent/persistence/alembic.ini
   superset_ai_agent/persistence/migrations/env.py
   superset_ai_agent/persistence/migrations/script.py.mako
   superset_ai_agent/persistence/migrations/versions/0001_initial_agent_tables.py
   ```

2. `[COMPLETE]` Replace `run_migrations(config)` with Alembic
   `command.upgrade(..., "head")`.
3. `[COMPLETE]` Keep `_ensure_sqlite_parent` for SQLite path creation.
4. `[COMPLETE]` Add a test-only helper, not production startup behavior:

   ```python
   def create_all_for_tests(engine: Engine) -> None:
       Base.metadata.create_all(engine)
   ```

5. `[COMPLETE]` Add bootstrap handling for existing development DBs:

   ```python
   agent_migration_bootstrap: Literal["error", "stamp_existing"] = "error"
   ```

6. `[COMPLETE]` Add `AI_AGENT_MIGRATION_BOOTSTRAP=error` to `.env.example`;
   `stamp_existing` is documented as the development-only escape hatch.

Tests:

```text
tests/unit_tests/superset_ai_agent/test_persistence_migrations.py
tests/unit_tests/superset_ai_agent/test_persistence_database.py
tests/unit_tests/superset_ai_agent/test_conversation_sqlalchemy_store.py
tests/unit_tests/superset_ai_agent/test_semantic_layer_sqlalchemy_store.py
```

Acceptance criteria:

- `[COMPLETE]` app startup no longer uses `Base.metadata.create_all`;
- `[COMPLETE]` migrations create all tables required by current SQLAlchemy
  stores;
- `[COMPLETE]` an empty SQLite DB upgrades to head;
- `[COMPLETE]` repeated upgrade is idempotent;
- `[COMPLETE]` existing tests no longer depend on production `create_all`
  behavior.

### R2. Central Semantic Access Service

Status: `[COMPLETE]`

Confirmed source state:

- Route-level helpers exist in `superset_ai_agent/app.py`:
  `authorize_semantic_scope` and `authorize_semantic_project`.
- Central access decisions live in
  `superset_ai_agent/semantic_layer/access.py::SemanticAccessService`.
- `SemanticAccessService` resolves Superset-proven database identity when
  available and records `SemanticAccessProof` details in access decisions.
- Store-level visibility in
  `superset_ai_agent/semantic_layer/projects.py::_with_permission` no longer
  silently grants write access for DB-derived visibility; `db_access` grants
  read permission unless
  `AI_AGENT_SEMANTIC_FULL_ACCESS_GRANTS_WRITE=true` and access proof is full.

Implementation steps:

1. `[COMPLETE]` Add `superset_ai_agent/semantic_layer/access.py`.
2. `[COMPLETE]` Add:

   ```python
   class SemanticPermission(str, Enum): ...
   class SemanticAccessLevel(str, Enum): ...
   class SemanticAccessProof(BaseModel): ...
   class SemanticAccessDecision(BaseModel): ...
   class SemanticAccessService: ...
   ```

3. `[COMPLETE]` Move project permission decisions into
   `SemanticAccessService`.
4. `[COMPLETE]` Replace direct route helper logic in `app.py` with centralized
   service calls:

   ```python
   access_service.require_project_permission(...)
   access_service.require_scope_permission(...)
   access_service.resolve_project_for_run(...)
   ```

5. `[COMPLETE]` Update `SemanticProjectStore` so `_with_permission` treats
   DB-derived access as read by default.
6. `[COMPLETE]` Make write/admin decisions explicit:
   - owner: admin;
   - explicit grant: granted permission;
   - DB-derived access: read by default;
   - DB-derived write only when
     `AI_AGENT_SEMANTIC_FULL_ACCESS_GRANTS_WRITE=true` and access proof is full.

Tests:

```text
tests/unit_tests/superset_ai_agent/test_semantic_layer_access.py
tests/unit_tests/superset_ai_agent/test_semantic_layer_api.py
tests/unit_tests/superset_ai_agent/test_semantic_layer_projects.py
tests/unit_tests/superset_ai_agent/test_semantic_layer_mdl_files.py
```

Acceptance criteria:

- `[COMPLETE]` every semantic project/document/MDL/materialization route calls
  `SemanticAccessService`;
- `[COMPLETE]` read, write, and admin decisions are tested independently;
- `[COMPLETE]` DB-derived access does not silently grant write unless
  configured;
- `[COMPLETE]` direct project ID access is checked through the central service.

### R3. Superset-Proven Database And Schema Access

Status: `[COMPLETE]`

Confirmed source state:

- `superset_ai_agent/semantic_layer/uri_fingerprint.py` fingerprints supplied
  URIs and fallback Superset database IDs.
- `SemanticProjectResolveRequest.supplied_uri` exists in
  `superset_ai_agent/semantic_layer/schemas.py`.
- `superset/ai_agent/api.py::AiAgentRestApi.database_identity` returns
  server-side database identity and a salted URI fingerprint without returning
  raw SQLAlchemy URIs or credentials.
- `SupersetRestClient.get_database_identity` and
  `SupersetClient.list_database_schemas` expose the identity path to the agent.
- `SemanticAccessService` prefers Superset-proven identity and supports
  `AI_AGENT_SEMANTIC_ACCESS_MODE=superset_only|db_uri_match|superset_or_uri`.

Implementation steps:

1. `[COMPLETE]` Add a Superset-side protected identity endpoint:

   ```text
   superset/ai_agent/api.py
   GET /api/v1/ai-agent/database/<database_id>/identity
   ```

2. `[COMPLETE]` The endpoint:
   - use `@protect()`;
   - validate database access through Superset security manager;
   - compute a salted URI fingerprint server-side;
   - return `database_id`, label, backend, fingerprint, and available schemas;
   - never return username, password, token, or raw SQLAlchemy URI.

3. `[COMPLETE]` Extend
   `superset_ai_agent/integrations/superset/client.py`:

   ```python
   class DatabaseIdentity(BaseModel): ...
   def get_database_identity(self, database_id: int) -> DatabaseIdentity: ...
   def list_database_schemas(
       self,
       *,
       database_id: int,
       catalog_name: str | None = None,
   ) -> list[str]: ...
   ```

4. `[COMPLETE]` Implement the methods in `SupersetRestClient`.
5. `[COMPLETE]` Let `SemanticAccessService` create access proofs from Superset
   identity when available.
6. `[COMPLETE]` Keep `supplied_uri` as a development or explicit validation
   path only:

   ```python
   semantic_access_mode: Literal[
       "superset_only",
       "db_uri_match",
       "superset_or_uri",
   ] = "superset_or_uri"
   ```

Tests:

```text
tests/unit_tests/superset_ai_agent/test_semantic_layer_uri_fingerprint.py
tests/unit_tests/superset_ai_agent/test_semantic_layer_access.py
tests/unit_tests/superset_ai_agent/test_superset_client.py
tests/integration_tests/views/api/test_ai_agent_database_identity.py
```

Acceptance criteria:

- `[COMPLETE]` raw URIs are never returned by the Superset identity endpoint;
- `[COMPLETE]` different credentials for the same logical DB produce the same
  fingerprint;
- `[COMPLETE]` schema project discovery uses Superset-proven
  database/schema access when available;
- `[COMPLETE]` URI-derived semantic-layer access cannot grant SQL execution
  permission.

### R4. Schema-Scoped Runtime Retrieval For Large Databases

Status: `[COMPLETE]`

Confirmed source state:

- Requests and scopes include `schema_name`.
- `superset_ai_agent/semantic_layer/retrieval.py::retrieve_schema_context`
  ranks permission-filtered Superset datasets and emits
  `WrenRetrievalArtifact`.
- `superset_ai_agent/context/superset_metadata.py::SupersetMetadataContextProvider`
  scans up to `AgentConfig.wren_schema_table_scan_limit` tables and narrows
  the prompt context to `wren_schema_table_candidate_limit`.
- `TextToSqlGraph._load_wren_context` and
  `ConversationGraph._load_wren_context` skip Wren context when
  `WREN_REQUIRE_SCHEMA_SCOPE=true` and no schema is selected.
- `WrenRetrievalArtifact` is exposed in `superset_ai_agent/schemas.py` and
  `superset-frontend/src/SqlLab/components/AiAgentPanel/api.ts`.

Implementation steps:

1. `[COMPLETE]` Add `superset_ai_agent/semantic_layer/retrieval.py`.
2. `[COMPLETE]` Add retrieval config in `AgentConfig`:

   ```python
   wren_schema_table_scan_limit: int = 100
   wren_schema_table_candidate_limit: int = 12
   wren_schema_metric_candidate_limit: int = 20
   wren_schema_example_candidate_limit: int = 5
   wren_schema_document_candidate_limit: int = 5
   wren_schema_context_token_budget: int = 6000
   wren_require_schema_scope: bool = True
   ```

3. `[COMPLETE]` Add `WrenRetrievalArtifact` to
   `superset_ai_agent/schemas.py`.
4. `[COMPLETE]` Update both graphs:
   - require `schema_name` before Wren retrieval when
     `wren_require_schema_scope=true`;
   - resolve exactly one semantic project through the project materialization
     runtime;
   - retrieve/rank only top-k models, metrics, examples, and document snippets;
   - record omitted counts and truncation in trace.

5. `[COMPLETE]` Do not load multiple schema projects into one prompt.

Tests:

```text
tests/unit_tests/superset_ai_agent/test_semantic_layer_retrieval.py
tests/unit_tests/superset_ai_agent/test_context_provider.py
tests/unit_tests/superset_ai_agent/test_graph.py
tests/unit_tests/superset_ai_agent/test_conversation_graph.py
```

Acceptance criteria:

- `[COMPLETE]` Wren context for a run is bound to one
  database/catalog/schema project.
- `[COMPLETE]` Large schema projects are truncated by configured top-k limits.
- `[COMPLETE]` Missing schema produces a user-facing Wren-scope warning and
  skips Wren context/dry-plan.
- `[COMPLETE]` Follow-up suggestions remain generated from the narrowed
  execution/result context.

### R5. True SQL Lab Semantic Editor Tab

Status: `[PARTIAL]`

Confirmed source state:

- `superset-frontend/src/SqlLab/components/SqlEditorLeftBar/index.tsx`
  contains a `Semantic layer` button and opens `SemanticLayerDrawer`.
- `superset-frontend/src/SqlLab/components/AiAgentPanel/SemanticLayerDrawer.tsx`
  implements project resolve, MDL CRUD, upload, YAML editing, Markdown
  enrichment, and materialization in an overlay.
- `superset-frontend/src/SqlLab/components/TabbedSqlEditors/index.tsx` still
  maps SQL Lab query editors directly to SQL editor tabs.
- There is no `superset-frontend/src/SqlLab/components/SemanticLayerEditor/`
  tab implementation.

Implementation steps:

1. Add mixed SQL Lab tab types in `superset-frontend/src/SqlLab/types.ts`:

   ```ts
   export type SqlLabTabType = 'query' | 'semanticLayer';
   export interface SqlLabTabRef { id: string; type: SqlLabTabType; }
   export interface SemanticLayerEditorTab { ... }
   ```

2. Add Redux actions in `superset-frontend/src/SqlLab/actions/sqlLab.ts`:

   ```ts
   OPEN_SEMANTIC_LAYER_EDITOR
   SET_ACTIVE_SQL_LAB_TAB
   CLOSE_SEMANTIC_LAYER_EDITOR
   UPDATE_SEMANTIC_LAYER_EDITOR
   ```

3. Update `superset-frontend/src/SqlLab/reducers/sqlLab.ts` to normalize legacy
   `tabHistory: string[]` into query tab refs.
4. Modify `TabbedSqlEditors/index.tsx` to render both SQL tabs and semantic
   tabs.
5. Add schema-row entrypoint to:

   ```text
   superset-frontend/src/SqlLab/components/TableExploreTree/TreeNodeRenderer.tsx
   superset-frontend/src/SqlLab/components/TableExploreTree/index.tsx
   ```

6. Add new components:

   ```text
   superset-frontend/src/SqlLab/components/SemanticLayerEditor/index.tsx
   SemanticLayerEditorTabHeader.tsx
   MdlFileBrowser.tsx
   MdlFileTreeNode.tsx
   MdlUploadDialog.tsx
   MdlEditor.tsx
   MdlValidationPanel.tsx
   MdlEnrichmentReview.tsx
   api.ts
   types.ts
   ```

7. Move reusable behavior out of `SemanticLayerDrawer` into the new tab
   components. Keep the drawer only as a temporary wrapper, or remove it once
   the tab flow has parity.
8. Use `EditorHost` with `language="yaml"`; do not use SQL `EditorWrapper` or
   SQL run hotkeys.

Tests:

```text
superset-frontend/src/SqlLab/components/TableExploreTree/TreeNodeRenderer.test.tsx
superset-frontend/src/SqlLab/components/TabbedSqlEditors/index.test.tsx
superset-frontend/src/SqlLab/components/SemanticLayerEditor/index.test.tsx
superset-frontend/src/SqlLab/components/SemanticLayerEditor/MdlFileBrowser.test.tsx
superset-frontend/src/SqlLab/components/SemanticLayerEditor/MdlUploadDialog.test.tsx
superset-frontend/src/SqlLab/components/SemanticLayerEditor/MdlEditor.test.tsx
superset-frontend/src/SqlLab/components/SemanticLayerEditor/MdlEnrichmentReview.test.tsx
```

Acceptance criteria:

- semantic layers open as first-class SQL Lab tabs;
- SQL tabs still open, close, select, and run unchanged;
- semantic tabs do not render SQL result panes or dispatch run-query actions;
- the visible scope is always database plus schema, with catalog where present.

### R6. Wren-Native Document Understanding

Status: `[COMPLETE]`

Confirmed source state:

- `superset_ai_agent/semantic_layer/review.py::propose_updates` still produces
  deterministic semantic updates for upload-time review candidates.
- `superset_ai_agent/app.py` exposes project document upload and enrichment
  routes.
- `superset_ai_agent/integrations/wren/client.py::WrenClient` exposes
  proposal-only methods: `preview_document_updates`,
  `propose_mdl_from_document`, and `validate_mdl_project`.
- `superset_ai_agent/integrations/wren/http_client.py::WrenHttpClient` can call
  Wren context, examples, dry-plan, document update preview, MDL proposal, and
  MDL validation endpoints without any SQL execution method.
- `superset_ai_agent/app.py::enrich_project_document` calls
  `WrenClient.propose_mdl_from_document`; file/disabled clients fall back to a
  deterministic review draft.

Implementation steps:

1. `[COMPLETE]` Extend `superset_ai_agent/integrations/wren/client.py::WrenClient` with
   proposal-only methods:

   ```python
   def preview_document_updates(...): ...
   def propose_mdl_from_document(...): ...
   def validate_mdl_project(...): ...
   ```

2. `[COMPLETE]` Add `superset_ai_agent/integrations/wren/http_client.py`.
3. `[COMPLETE]` Add config:

   ```python
   wren_adapter: Literal["file", "http"] = "file"
   wren_base_url: str | None = None
   wren_api_key: str | None = None
   wren_timeout_seconds: float = 30.0
   wren_onboarding_enabled: bool = False
   ```

4. `[COMPLETE]` Update the document enrichment route to:
   - call the HTTP adapter when available;
   - return proposed MDL as a draft proposal;
   - require explicit user save/activation;
   - fall back to deterministic scaffold with clear warnings.

5. `[COMPLETE]` Ensure only validated YAML MDL files with `status="active"` are materialized.

Tests:

```text
tests/unit_tests/superset_ai_agent/test_wren_http_client.py
tests/unit_tests/superset_ai_agent/test_semantic_layer_review.py
tests/unit_tests/superset_ai_agent/test_semantic_layer_api.py
```

Acceptance criteria:

- `[COMPLETE]` Wren enrichment never mutates active MDL automatically.
- `[COMPLETE]` Wren HTTP dry-plan and validation payloads set
  `execution="disabled"`.
- `[COMPLETE]` HTTP onboarding failures return deterministic review drafts with
  warnings instead of activating content.
- `[PARTIAL]` Frontend review remains implemented inside
  `SemanticLayerDrawer`; a first-class `SemanticLayerEditor` tab test is still
  part of R5.
- Wren enrichment never executes SQL;
- failed Wren enrichment returns a reviewable error or fallback proposal;
- proposal source, warnings, and validation state are visible to the user.

### R7. SQL Lab AI Audit Source Marker

Status: `[COMPLETE]`

Confirmed source state:

- `superset_ai_agent/schemas.py::AuditInfo` already has `client_id`,
  `sql_editor_id`, `tab`, and `source_hash`.
- `superset_ai_agent/integrations/superset/rest.py::SupersetRestClient`
  normalizes audit payloads.
- `superset_ai_agent/schemas.py::SqlExecutionSource` is the typed cross-graph
  execution argument.
- `TextToSqlGraph._execute_sql` and `ConversationGraph._execute_sql` pass
  source metadata only after SQL validation succeeds.
- `SupersetRestClient.execute_sql_raw` sends accepted SQL Lab fields:
  `client_id`, `sql_editor_id`, and `tab`.

Implementation steps:

1. `[COMPLETE]` Add `SqlExecutionSource` to `superset_ai_agent/schemas.py`.
2. `[COMPLETE]` Extend `SupersetClient.execute_sql` in
   `superset_ai_agent/integrations/superset/client.py`:

   ```python
   source: SqlExecutionSource | None = None
   ```

3. `[COMPLETE]` Update REST payload in
   `SupersetRestClient.execute_sql_raw`:

   ```python
   "client_id": short_ai_query_id(source),
   "sql_editor_id": ai_sql_editor_id(source),
   "tab": "AI Agent",
   ```

4. `[COMPLETE]` Thread source metadata from:
   - `TextToSqlGraph._execute_sql`;
   - `ConversationGraph._execute_sql`;
   - conversation manual execution route.

5. `[COMPLETE]` Keep `client_id` unique and <= 11 characters. Do not use a
   fixed `client_id`.

Tests:

```text
tests/unit_tests/superset_ai_agent/test_superset_client.py
tests/unit_tests/superset_ai_agent/test_graph.py
tests/unit_tests/superset_ai_agent/test_conversation_graph.py
```

Acceptance criteria:

- `[COMPLETE]` REST execute payload contains accepted SQL Lab marker fields;
- `[COMPLETE]` `AuditInfo` returns marker values when Superset returns query
  metadata;
- `[COMPLETE]` invalid or non-read-only SQL still never reaches `execute_sql`.

### R8. Object Storage For Uploaded Documents

Status: `[COMPLETE]`

Confirmed source state:

- `superset_ai_agent/semantic_layer/file_storage.py::LocalDocumentStorage`
  stores files under `AI_AGENT_STORAGE_DIR`.
- `superset_ai_agent/semantic_layer/file_storage.py::S3DocumentStorage`
  stores files in an S3-compatible object store and returns `s3://` URIs.
- `superset_ai_agent/app.py::_create_document_storage` wires
  `AI_AGENT_DOCUMENT_STORAGE=local|s3`.
- `SemanticDocument.storage_uri` records the durable pointer.
- Raw bytes remain outside SQLAlchemy stores; the DB stores checksum, metadata,
  extracted text, review state, and `storage_uri`.

Implementation steps:

1. `[COMPLETE]` Extend the existing
   `superset_ai_agent/semantic_layer/file_storage.py::DocumentStorage`
   protocol with local and S3 implementations.
2. `[COMPLETE]` Add app-level storage factory in
   `superset_ai_agent/app.py::_create_document_storage`.
3. `[COMPLETE]` Add config:

   ```python
   document_storage: Literal["local", "s3"] = "local"
   document_s3_bucket: str | None = None
   document_s3_prefix: str = "superset-ai-agent/documents"
   document_s3_endpoint_url: str | None = None
   document_s3_region_name: str | None = None
   ```

4. `[COMPLETE]` Keep raw bytes outside the SQLAlchemy DB.
5. `[COMPLETE]` Keep extracted text, checksum, metadata, and review state in
   the DB.

Tests:

```text
tests/unit_tests/superset_ai_agent/test_semantic_layer_file_storage.py
tests/unit_tests/superset_ai_agent/test_semantic_layer_documents.py
```

Acceptance criteria:

- `[COMPLETE]` Local storage remains default and tested.
- `[COMPLETE]` S3 storage writes, reads, and deletes by `storage_uri`.
- `[COMPLETE]` DB rows never contain raw file bytes.
- `[PARTIAL]` Document checksum is computed at upload time; read-time checksum
  verification can be added before serving downloaded raw bytes.

### R9. Superset Semantic-Layer Publication Bridge

Status: `[PARTIAL]`

Confirmed source state:

- Superset has semantic-layer APIs and models:
  `superset/semantic_layers/api.py::SemanticLayerRestApi`,
  `SemanticViewRestApi`, and
  `superset/semantic_layers/models.py::SemanticLayer`, `SemanticView`.
- The agent has REST bridge primitives in
  `superset_ai_agent/integrations/superset/rest.py::SupersetRestClient`:
  `list_semantic_layers`, `create_semantic_layer`,
  `update_semantic_layer`, `delete_semantic_layer`, and
  `create_semantic_views`.
- Local and MCP adapters raise `SupersetAdapterNotImplementedError` for
  publication methods; publication remains REST-only to preserve Superset
  route-level authorization.
- The agent still does not have an explicit project `publish` route or a
  validated Wren-MDL-to-Superset semantic-layer configuration mapper.

Implementation steps:

1. `[COMPLETE]` Extend `SupersetClient` with semantic-layer methods:

   ```python
   def list_semantic_layers(self) -> list[dict[str, Any]]: ...
   def create_semantic_layer(self, payload: dict[str, Any]) -> dict[str, Any]: ...
   def update_semantic_layer(self, uuid: str, payload: dict[str, Any]) -> dict[str, Any]: ...
   def delete_semantic_layer(self, uuid: str) -> None: ...
   def create_semantic_views(self, views: list[dict[str, Any]]) -> dict[str, Any]: ...
   ```

2. `[COMPLETE]` Implement only in
   `superset_ai_agent/integrations/superset/rest.py` first.
3. `[COMPLETE]` Call Superset REST with the current user's session when the
   adapter is configured for `user_session`.
4. `[TODO]` Add explicit publication route under project admin permission:

   ```text
   POST /agent/semantic-layer/projects/{project_id}/publish
   ```

5. `[TODO]` Store returned Superset UUIDs on `SemanticLayerVersion`.
6. `[TODO]` Define and test the Wren MDL publication mapper. Do not infer a
   lossy mapper without product review of Superset semantic-layer type,
   configuration schema, runtime data, and semantic-view payload shape.

Tests:

```text
tests/unit_tests/superset_ai_agent/test_superset_client.py
tests/unit_tests/superset_ai_agent/test_semantic_layer_api.py
tests/integration_tests/semantic_layers/api_tests.py
```

Acceptance criteria:

- `[PARTIAL]` publication primitives use Superset REST/commands, not direct DB
  mutation;
- `[TODO]` publication is exposed as an explicit user action;
- `[TODO]` Superset REST permission failures propagate through the project
  publish route to the agent UI;
- `[TODO]` agent semantic project remains useful even when publication fails.

### R10. One-Shot And Conversation Semantic Parity

Status: `[COMPLETE]`

Confirmed source state:

- Both graphs have Wren hooks.
- Both graphs use
  `superset_ai_agent/semantic_layer/runtime.py::merge_indexed_semantic_context`
  to merge the latest indexed semantic-layer version into the runtime Wren
  artifact.
- `TextToSqlGraph` receives `semantic_layer_store` from `app.py` and now
  matches `ConversationGraph` behavior for indexed document context.

Implementation steps:

1. `[COMPLETE]` Share a helper used by both graphs:

   ```text
   superset_ai_agent/semantic_layer/runtime.py
   ```

2. `[COMPLETE]` Runtime helper:
   - require or validate schema scope;
   - preserves schema-scoped Wren artifacts from R4;
   - merges latest indexed document context from `SemanticLayerStore`;
   - returns the same `WrenContextArtifact` shape.

3. `[COMPLETE]` Use this helper in:
   - `TextToSqlGraph._load_wren_context`;
   - `ConversationGraph._load_wren_context`.

Tests:

```text
tests/unit_tests/superset_ai_agent/test_graph.py
tests/unit_tests/superset_ai_agent/test_conversation_graph.py
tests/unit_tests/superset_ai_agent/test_semantic_layer_runtime.py
```

Acceptance criteria:

- `[COMPLETE]` one-shot and conversation requests resolve the same semantic
  project for the same database/catalog/schema/user;
- `[COMPLETE]` both flows refuse or warn on missing schema consistently;
- `[COMPLETE]` both flows include the same Wren artifact shape, including
  indexed document context.

## 23. Final Follow-Up Milestone Order

| Order | Milestone | Status | Primary output |
| --- | --- | --- | --- |
| 1 | R1 migrations | [COMPLETE] | Alembic-managed agent DB. |
| 2 | R2 access service | [COMPLETE] | Single audited semantic authorization boundary. |
| 3 | R3 Superset DB/schema proof | [COMPLETE] | User/session-proven URI fingerprint and schema access. |
| 4 | R4 schema retrieval | [COMPLETE] | Bounded top-k context for large schemas. |
| 5 | R5 SQL Lab tab UI | [PARTIAL] | First-class semantic editor tab replacing drawer as target UX. |
| 6 | R7 audit source marker | [COMPLETE] | Traceable AI-origin SQL Lab queries. |
| 7 | R6 Wren document adapter | [COMPLETE] | Real Wren proposal flow, still no execution. |
| 8 | R8 object storage | [COMPLETE] | Multi-replica-safe document bytes. |
| 9 | R10 graph parity | [COMPLETE] | Same semantic runtime in one-shot and conversation flows. |
| 10 | R9 Superset publication bridge | [PARTIAL] | REST bridge primitives exist; explicit publish route and mapper remain. |

## 24. Remaining Risk Register

| Risk | Status | Close-out condition |
| --- | --- | --- |
| Agent DB has no versioned migration lifecycle | [COMPLETE] | Alembic migration lifecycle merged; tests prove migrations create all agent tables and startup does not call production `create_all`. |
| Semantic-layer sharing could leak metadata | [COMPLETE] baseline, [PARTIAL] route context | Every route uses `SemanticAccessService`; project-ID-only routes still rely on the project's stored default Superset DB ID unless the UI first resolves by the user's current DB/schema. |
| URI matching could be spoofed | [COMPLETE] baseline | Superset-side fingerprinting is available through `AiAgentRestApi.database_identity`; raw URI fallback remains configurable and should be disabled with `AI_AGENT_SEMANTIC_ACCESS_MODE=superset_only` in hardened deployments. |
| Users expect CRUD on semantic layers they can access | [COMPLETE] baseline, [PARTIAL] policy | Existing project/MDL CRUD remains; R2 tightens read/write/admin policy; R5 moves CRUD to target tab UI. |
| SQL Lab audit lacks AI source marker | [COMPLETE] | `SqlExecutionSource` is threaded through graph execution and REST SQL Lab payload fields. |
| SQL Lab tab state could break existing query behavior | [TODO] | R5 mixed tab model has reducer migration and tests for legacy SQL tabs. |
| MDL editor could inherit SQL run hotkeys | [PARTIAL] | R5 target implementation must use a dedicated YAML `MdlEditor` and tests proving no run-query dispatch occurs. |
| Existing scope-based semantic records could be orphaned | [PARTIAL] | R2/R10 keep compatibility routes and graph merge behavior; a dedicated migration/backfill for old records remains optional deployment work. |
| Catalog-aware databases could collide on schema names | [COMPLETE] baseline, [PARTIAL] tests | Existing schemas carry `catalog_name`; R2/R3 add access proofs and tests around catalog-qualified project keys. |
| Wren project directory can drift from DB state | [COMPLETE] baseline | Existing materializer writes active files; R4/R10 add selected-project runtime checks and trace metadata. |
| Markdown source documents could be materialized as MDL | [COMPLETE] baseline | Existing materialization reads MDL files, not source documents; keep regression tests in R6/R10. |
| Markdown enrichment could produce incorrect MDL | [PARTIAL] | R6 makes Wren proposals review-only, visible, validated, and explicitly activated. |
| Unsupported uploads could create unclear user expectations | [PARTIAL] | R5 upload dialog accepts only YAML MDL and Markdown for enrichment in the target UI. |
| Large schemas can overwhelm Wren context | [COMPLETE] baseline | R4 top-k retrieval and schema scope requirement merged; deeper Wren-native ranking quality depends on the deployed Wren API. |
| Object storage is missing | [COMPLETE] | R8 adds local and S3 document storage and verifies raw bytes stay outside DB. |
| One-shot and conversation behavior differ | [COMPLETE] | Both graphs share schema-scoped Wren materialization/retrieval and indexed semantic-layer context merge behavior. |
| Superset semantic-layer core access is not DB-derived | [PARTIAL] | R9 REST bridge primitives are implemented; explicit project publication and any Superset core DB-derived semantic access remain separate design work. |

## 25. Avoid For Now

- Wren direct execution.
- Letting Wren validate or override Superset SQL authorization.
- Persisting raw database URIs, usernames, passwords, or tokens.
- Granting SQL execution based on URI proof tokens.
- Treating URI-derived semantic access as Superset DB permission.
- Automatic MDL mutation without human review.
- Treating semantic editor tabs as SQL `QueryEditor` tabs.
- Registering SQL run-query hotkeys in the MDL YAML editor.
- Materializing draft, deleted, invalid, or unreviewed MDL files into Wren.
- Storing Markdown source documents as active MDL files.
- Accepting unsupported document types before the enrichment pipeline is
  designed for them.
- Production persistence with `DEFAULT_OWNER_ID = "local"`.
- Direct mutation of Superset `semantic_layers` or `semantic_views` outside
  Superset commands or REST APIs.
- Global document memory across unrelated database fingerprints.
- Saved Superset chart creation without explicit user action.
- Using `client_id="superset_ai_agent"` as a fixed SQL Lab marker.
