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
- semantic-layer status, document upload, review, and indexing state

## Persistence And Identity Recommendation

The current code intentionally keeps conversation state process-local:

- `superset_ai_agent/conversations/store.py::ConversationStore` is a clean
  protocol boundary.
- `superset_ai_agent/conversations/memory.py::InMemoryConversationStore` is the
  only implementation.
- `superset_ai_agent/config.py::ConversationStoreMode` only allows `"memory"`.
- `superset_ai_agent/app.py::_create_conversation_store` always returns
  `InMemoryConversationStore`.
- All FastAPI conversation routes currently pass
  `superset_ai_agent/conversations/store.py::DEFAULT_OWNER_ID`, which is
  `"local"`.

Persisting conversations or uploaded semantic-layer documents without changing
identity would create shared state across users. Identity must be added before a
database-backed store is enabled outside local development.

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

- the AI-agent Docker image currently installs no SQLAlchemy or Alembic
  dependency;
- the standalone agent has no configured metadata database URI;
- direct Superset DB writes would couple the agent to Superset app context,
  migrations, encryption configuration, and model internals;
- Superset core tables do not solve the current owner identity problem;
- uploaded document review state has different lifecycle and retention needs
  from Superset semantic-layer objects.

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
    owner_id = mapped_column(String(255), index=True, nullable=False)
    database_id = mapped_column(Integer, nullable=False, index=True)
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
    owner_id = mapped_column(String(255), index=True, nullable=False)
    database_id = mapped_column(Integer, nullable=False, index=True)
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
    owner_id = mapped_column(String(255), index=True, nullable=False)
    scope_hash = mapped_column(String(128), index=True, nullable=False)
    question_hash = mapped_column(String(128), index=True, nullable=False)
    context = mapped_column(JSON, nullable=False)
    created_at = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at = mapped_column(DateTime(timezone=True), nullable=True, index=True)


class AiAgentEvent(Base):
    __tablename__ = "ai_agent_events"

    id = mapped_column(String(36), primary_key=True)
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
    ) -> WrenContextArtifact:
        """Fetch Wren semantic context for an already permission-filtered scope."""

    def recall_examples(
        self,
        *,
        question: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        """Return example questions, SQL patterns, or semantic memories."""

    def dry_plan(
        self,
        *,
        question: str,
        sql: str | None,
        context: AgentContext,
    ) -> dict[str, Any]:
        """Return Wren planning metadata without executing SQL."""


class DisabledWrenClient:
    """Fail-closed Wren client used when Wren is disabled."""

    def is_available(self) -> bool:
        return False
```

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
    database_id: int
    schema_name: str | None = None
    dataset_ids: list[int] = Field(default_factory=list)
    document_count: int
    approved_document_count: int
    indexed_document_count: int
    semantic_layer_version: str | None = None
    indexing_status: Literal["idle", "running", "error"] = "idle"
    last_error: str | None = None


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
```

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

Phase 1 should allow plain text, Markdown, CSV, and JSON. PDF and DOCX can be
added behind optional dependencies after the review workflow is stable.

Review/index workflow:

- `documents.py::create_document` validates content type and size, computes a
  checksum, extracts safe text, and creates `SemanticDocument`.
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

`POST /agent/semantic-layer/documents` should accept `UploadFile` and a
serialized `ConversationScope`. Add `python-multipart` to
`requirements-ai-agent.txt` when implementing this route.

Real-time behavior:

- Use `GET /agent/semantic-layer/events` with server-sent events in Phase 1.
- Emit events for upload, extraction, review-required, review-saved, indexing
  started, indexing completed, and indexing failed.
- Avoid WebSockets until bidirectional editing is required.

Security requirements for documents:

- Scope every document to `database_id`, `schema_name`, and `dataset_ids`.
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
superset-frontend/src/SqlLab/components/AiAgentPanel/SemanticLayerDrawer.tsx
superset-frontend/src/SqlLab/components/AiAgentPanel/DocumentUpload.tsx
superset-frontend/src/SqlLab/components/AiAgentPanel/DocumentReviewPanel.tsx
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

Semantic-layer UI:

- Add a semantic-layer button in the panel header.
- Open `SemanticLayerDrawer`.
- Show current state with `SemanticLayerStateBadge`.
- Allow upload through `DocumentUpload`.
- Show proposed updates in `DocumentReviewPanel`.
- Require explicit approval before indexing updates.
- Subscribe to SSE events while the drawer is open.

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
superset-frontend/src/SqlLab/components/AiAgentPanel/SemanticLayerDrawer.test.tsx
superset-frontend/src/SqlLab/components/AiAgentPanel/DocumentUpload.test.tsx
superset-frontend/src/SqlLab/components/AiAgentPanel/DocumentReviewPanel.test.tsx
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
- semantic-layer drawer opens
- document upload calls the API
- proposed updates can be approved or rejected
- SSE events update semantic-layer state

Governance tests:

- Wren direct execution is not called.
- Unauthorized SQL failures still surface through Superset adapter errors.
- Invalid or non-read-only SQL does not reach `SupersetClient.execute_sql`.
- REST row limits are preserved through `queryLimit`.
- MCP/local adapter differences are documented and tested at the adapter layer.

## 13. Rollout Plan

### Phase 0: Persistence And Identity Foundation

Implement before enabling durable conversations or document review:

- `superset_ai_agent/auth.py`
- signed identity support for production deployments
- `superset_ai_agent/persistence/*`
- SQLAlchemy/Alembic dependencies
- agent persistence migrations
- `SqlAlchemyConversationStore`
- `SqlAlchemySemanticLayerStore`
- local file document storage
- Docker volume for SQLite development
- Postgres-ready `AI_AGENT_DATABASE_URL`

Keep `InMemoryConversationStore` available for tests and local throwaway runs,
but document that production persistence requires non-static identity.

### Phase 1A: Analytics Artifacts, No Wren

Implement:

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

No Wren dry-plan and no document ingestion yet.

### Phase 1C: Wren Dry-Plan, Optional

Implement:

- optional dry-plan node before local validation
- dry-plan metadata in `WrenContextArtifact`
- fail-soft behavior when Wren planning fails

Superset validation and execution remain authoritative.

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

## Risks and Open Questions

Implementation risks:

- static `"local"` identity would expose shared persisted history if enabled in
  a multi-user deployment
- operating a separate agent DB creates backup, migration, and retention
  responsibilities outside Superset core
- direct writes to Superset metadata DB could drift from Superset migrations or
  bypass Superset command validation if used incorrectly
- deterministic insights may overstate conclusions on truncated data
- type inference can misclassify string-coded metrics or dates
- chart rendering can grow into a parallel charting framework
- audit payloads differ between REST, MCP, and local adapters
- document-derived semantic updates can pollute context without careful review
- Wren context can leak unauthorized semantic metadata if built globally

Questions for Wren maintainers:

- What is the stable MDL schema for model descriptions, fields, measures, and
  examples?
- Can Wren operate on a request-scoped partial MDL?
- What is the dry-plan API contract and failure mode?
- How are document-derived semantic updates represented and versioned?
- Can Wren expose source document references for retrieved context?
- How should memory/examples be scoped by project, model, or user?

Questions for Superset reviewers:

- Should the agent remain standalone with an agent-owned DB, or should durable
  conversation/document state eventually move into Superset core models?
- What is the preferred authenticated proxy or signed identity mechanism for
  `/ai-agent` routes?
- Should analytics artifacts live on `ConversationArtifact` or a new artifact
  type?
- Should REST SQL Lab queries created by the agent include an explicit AI
  source marker?
- Should local adapter usage be hidden or warned against when Wren is enabled?
- Should MCP execution be documented as governed but not equivalent to REST SQL
  Lab strict dataset matching?
- Should semantic-layer documents persist inside Superset metadata storage or
  remain standalone agent state?

Avoid for now:

- Wren direct execution
- automatic MDL mutation without user review
- production persistence with `DEFAULT_OWNER_ID = "local"`
- arbitrary agent tables inside Superset core without a Superset ownership,
  migration, and API design
- direct mutation of Superset `semantic_layers` or `semantic_views` outside
  Superset commands or REST APIs
- global document memory across unrelated datasets
- document-driven permission changes
- saved Superset chart creation from the agent
- replacing Superset SQL validation with Wren planning
- trusting document text over Superset metadata and permissions
