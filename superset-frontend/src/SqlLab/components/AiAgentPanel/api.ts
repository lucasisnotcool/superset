/**
 * Licensed to the Apache Software Foundation (ASF) under one
 * or more contributor license agreements.  See the NOTICE file
 * distributed with this work for additional information
 * regarding copyright ownership.  The ASF licenses this file
 * to you under the Apache License, Version 2.0 (the
 * "License"); you may not use this file except in compliance
 * with the License.  You may obtain a copy of the License at
 *
 *   http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing,
 * software distributed under the License is distributed on an
 * "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
 * KIND, either express or implied.  See the License for the
 * specific language governing permissions and limitations
 * under the License.
 */

import rison from 'rison';
import { SupersetClient } from '@superset-ui/core';

export type AgentStatus = 'ok' | 'needs_review' | 'error';
export type ExecutionMode = 'manual' | 'read_only' | 'auto';

export interface AgentTraceEvent {
  step: string;
  status: 'ok' | 'warning' | 'error';
  summary: string;
  details: Record<string, unknown>;
}

export type SqlClassification =
  | 'read_only'
  | 'mutating'
  | 'opaque'
  | 'multi'
  | 'unparseable';

export interface SqlValidationResult {
  is_valid: boolean;
  is_read_only: boolean;
  // Deterministic verdict from the backend SQL safety policy. Older backends
  // may omit it, so treat as optional.
  classification?: SqlClassification;
  // Human-readable explanation of a block, surfaced to the user.
  reason?: string | null;
  normalized_sql?: string | null;
  dialect?: string | null;
  errors: string[];
}

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
  query_id?: number | string | null;
  results_key?: string | null;
  executed_sql?: string | null;
  database_id?: number | null;
  catalog_name?: string | null;
  schema_name?: string | null;
  row_limit?: number | null;
  timeout_seconds?: number | null;
  client_id?: string | null;
  sql_editor_id?: string | null;
  tab?: string | null;
  source_hash?: string | null;
  source?: string | null;
  // Semantic-engine provenance (wren_full.md Phase 1.5): the LLM-authored
  // semantic SQL, the engine-rewritten native SQL, and which engine rewrote it.
  engine?: string | null;
  semantic_sql?: string | null;
  native_sql?: string | null;
}

export interface WrenRetrievalArtifact {
  project_id?: string | null;
  database_id?: number | null;
  catalog_name?: string | null;
  schema_name?: string | null;
  candidate_table_names: string[];
  candidate_metric_names: string[];
  candidate_example_ids: string[];
  candidate_document_ids: string[];
  scanned_table_count: number;
  omitted_table_count: number;
  context_truncated: boolean;
}

export interface WrenContextArtifact {
  enabled: boolean;
  available: boolean;
  project_id?: string | null;
  mdl_path?: string | null;
  materialized_file_count?: number | null;
  materialized_checksum?: string | null;
  matched_models: string[];
  example_ids: string[];
  document_ids: string[];
  context_items: Record<string, unknown>[];
  // Which retriever produced the context items (keyword | embedding), stamped
  // when the Retriever seam contributes context (wren_full.md RV2).
  retrieval_mode?: string | null;
  // How many MDL schema chunks the retriever contributed this turn (RV3/G8).
  retrieved_item_count?: number | null;
  // How many confirmed NL->SQL examples the memory seam recalled for this turn
  // (0 when learning is off); surfaced as a UI badge (wren_full.md RV3).
  recalled_example_count?: number | null;
  retrieval?: WrenRetrievalArtifact | null;
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

// --- Explain & audit timeline (ai_agent_explain_and_audit.md) ----------------
// One typed, ordered step in the message->response chain. `detail` is a
// discriminated union keyed on `detail.kind` (a shape tag decoupled from the
// node name `step.kind`, so several nodes can share one shape). An unknown
// `step.kind` carries `detail: null` and renders as its bare summary.

export interface LoadContextDetail {
  kind: 'load_context';
  dataset_count: number;
  database_name?: string | null;
  retrieval?: WrenRetrievalArtifact | null;
}
export interface IntentDetail {
  kind: 'intent';
  intent?: string | null;
  reason?: string | null;
}
export interface RetrievedChunk {
  kind?: string | null;
  name?: string | null;
  model?: string | null;
  text: string;
  retriever?: string | null;
  score?: number | null;
}
export interface LoadWrenContextDetail {
  kind: 'wren_context';
  available: boolean;
  project_id?: string | null;
  mdl_path?: string | null;
  matched_models: string[];
  retrieval_mode?: string | null;
  retrieved_item_count: number;
  context_item_count: number;
  recalled_example_count: number;
  retrieved_chunks?: RetrievedChunk[] | null;
  warnings?: string[] | null;
}
export interface RecalledExample {
  question: string;
  native_sql?: string | null;
}
export interface DraftDetail {
  kind: 'draft';
  response_type?: string | null;
  model?: string | null;
  recalled_example_count: number;
  recalled_examples?: RecalledExample[] | null;
}
export interface DryPlanDetail {
  kind: 'dry_plan';
  available: boolean;
  diagnostics: string[];
}
export interface PlanSemanticSqlDetail {
  kind: 'plan_semantic_sql';
  engine?: string | null;
  rewritten: boolean;
  semantic_sql?: string | null;
  native_sql?: string | null;
  referenced_tables: string[];
  warnings: string[];
}
export interface ValidateSqlDetail {
  kind: 'validate_sql';
  is_valid: boolean;
  dialect?: string | null;
  errors: string[];
}
export interface RepairDetail {
  kind: 'repair';
  errors: string[];
  dry_plan_diagnostics: string[];
  attempt?: number | null;
}
export interface ExecuteSqlDetail {
  kind: 'execute';
  row_count?: number | null;
  sql?: string | null;
  executed_sql?: string | null;
  query_id?: number | string | null;
  adapter?: string | null;
  error?: string | null;
  is_duplicate: boolean;
}
export interface BuildArtifactsDetail {
  kind: 'build_artifacts';
  insight_card_count: number;
  chart_type?: string | null;
  has_data_preview: boolean;
}
export interface ReflectDetail {
  kind: 'reflect';
  outcome?: string | null;
  remaining_sql_iterations?: number | null;
  retry_feedback?: string | null;
}

export type AgentStepDetail =
  | LoadContextDetail
  | IntentDetail
  | LoadWrenContextDetail
  | DraftDetail
  | DryPlanDetail
  | PlanSemanticSqlDetail
  | ValidateSqlDetail
  | RepairDetail
  | ExecuteSqlDetail
  | BuildArtifactsDetail
  | ReflectDetail;

export interface AgentStep {
  kind: string;
  status: 'ok' | 'warning' | 'error';
  summary: string;
  started_at: string;
  duration_ms?: number | null;
  attempt_index: number;
  artifact_id?: string | null;
  detail?: AgentStepDetail | null;
}

export interface AgentQueryRequest {
  question: string;
  database_id: number;
  catalog_name?: string | null;
  schema_name?: string | null;
  dataset_ids: number[];
  execute: boolean;
  model?: string | null;
}

export interface AgentQueryResponse {
  status: AgentStatus;
  sql?: string | null;
  explanation?: string | null;
  validation: SqlValidationResult;
  execution_result?: ExecutionResult | null;
  trace: AgentTraceEvent[];
  answer_summary?: string | null;
  insight_cards?: InsightCard[];
  chart_spec?: ChartSpec | null;
  data_preview?: ExecutionResult | null;
  audit?: AuditInfo | null;
  recommended_followups?: string[];
  wren_context?: WrenContextArtifact | null;
  // Ordered explain-and-audit timeline of the message->response chain.
  timeline?: AgentStep[];
}

export interface ConversationScope {
  database_id: number;
  catalog_name?: string | null;
  schema_name?: string | null;
  /** Full schema set for a multi-schema semantic project (primary first). */
  schema_names?: string[] | null;
  dataset_ids: number[];
  /** Explicit semantic-layer project to ground on when a schema is covered by
   * more than one. The backend honors it only after re-checking access + schema
   * coverage; `null`/absent means "let the backend resolve". */
  project_id?: string | null;
  query_editor_id?: string | null;
  current_sql?: string | null;
  selected_text?: string | null;
}

export interface ConversationArtifact {
  id: string;
  // Free-form agent discriminator: 'sql' for the AI SQL agent, 'changeset' for
  // the MDL Copilot. Non-SQL agents carry their data in `payload`.
  type: string;
  sql?: string | null;
  // Opaque per-agent payload (e.g. a serialized Copilot Changeset).
  payload?: Record<string, unknown> | null;
  explanation?: string | null;
  validation?: SqlValidationResult | null;
  execution_result?: ExecutionResult | null;
  trace: AgentTraceEvent[];
  answer_summary?: string | null;
  insight_cards?: InsightCard[];
  chart_spec?: ChartSpec | null;
  data_preview?: ExecutionResult | null;
  audit?: AuditInfo | null;
  recommended_followups?: string[];
  wren_context?: WrenContextArtifact | null;
  // Per-artifact explain-and-audit timeline so reopened chats re-render.
  timeline?: AgentStep[];
}

export type SemanticDocumentStatus =
  | 'uploaded'
  | 'extracting'
  | 'extracted'
  | 'needs_ocr'
  | 'needs_review'
  | 'approved'
  | 'indexed'
  | 'error';

export interface SemanticDocument {
  id: string;
  project_id?: string | null;
  filename: string;
  content_type: string;
  size_bytes: number;
  status: SemanticDocumentStatus;
  scope: ConversationScope;
  checksum: string;
  storage_uri: string;
  summary?: string | null;
  extracted_text?: string | null;
  extracted_text_preview?: string | null;
  warnings: string[];
  error?: string | null;
  created_at: string;
  updated_at: string;
  /**
   * Transient, response-only flag set by the upload endpoint when the bytes were
   * byte-identical to an existing document in the project (content-hash dedup), so
   * no new document/chunks/vectors were created. Never present on a reloaded
   * document — only on the immediate upload response.
   */
  deduplicated?: boolean;
}

export interface DocumentChunk {
  id: string;
  document_id: string;
  chunk_index: number;
  text: string;
  checksum: string;
  char_start: number;
  char_end: number;
  embedded: boolean;
}

export interface DocumentChunkMatch {
  chunk_id: string;
  other_chunk_id: string;
  document_id: string;
  other_document_id: string;
  score: number;
  exact: boolean;
}

export interface SemanticLayerState {
  project_id?: string | null;
  database_id: number;
  catalog_name?: string | null;
  schema_name?: string | null;
  schema_names?: string[] | null;
  dataset_ids: number[];
  document_count: number;
  last_error?: string | null;
}

export interface ConversationMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  created_at: string;
  artifacts: ConversationArtifact[];
}

export interface Conversation {
  id: string;
  title: string;
  owner_id: string;
  kind: string;
  project_id?: string | null;
  scope: ConversationScope;
  messages: ConversationMessage[];
  created_at: string;
  updated_at: string;
}

export interface ConversationSummary {
  id: string;
  title: string;
  owner_id: string;
  kind: string;
  project_id?: string | null;
  database_id: number;
  catalog_name?: string | null;
  schema_name?: string | null;
  updated_at: string;
  last_message?: string | null;
}

export interface ConversationTurnRequest {
  message: string;
  scope: ConversationScope;
  execution_mode: ExecutionMode;
  execute?: boolean;
  approved_sql?: string | null;
  model?: string | null;
}

export interface ConversationSqlExecutionRequest {
  sql: string;
  scope: ConversationScope;
  execution_mode: ExecutionMode;
  artifact_id?: string | null;
  model?: string | null;
}

export interface ConversationTurnResponse {
  status: AgentStatus;
  conversation_id: string;
  message: ConversationMessage;
  artifacts: ConversationArtifact[];
  trace: AgentTraceEvent[];
  // Turn-level explain-and-audit timeline of the whole message->response chain.
  timeline?: AgentStep[];
  conversation: Conversation;
}

export interface AgentHealthResponse {
  status: 'ok' | 'degraded';
  model_provider: string;
  base_url: string;
  default_model: string;
  reachable: boolean;
  ollama_base_url?: string | null;
  ollama_reachable?: boolean | null;
  // False when the semantic layer runs in-memory (models lost on restart), so
  // the UI can warn before users model against an ephemeral store (RV3).
  semantic_layer_persistent?: boolean;
  // Effective embedding vector index: 'memory' | 'lancedb' | 'memory_fallback'.
  // 'memory_fallback' = LanceDB was configured but did not connect (C1).
  vector_index?: string;
  // Effective max upload size for source documents (WREN_MAX_DOCUMENT_BYTES); the
  // UI uses it to reject oversized files before the upload round-trip.
  max_document_bytes?: number;
}

export type SemanticProjectVisibility = 'private' | 'db_access' | 'custom';
export type SemanticProjectPermission = 'read' | 'write' | 'admin';
export type MdlFileStatus = 'draft' | 'active' | 'deleted';
export type MdlFileSourceType =
  | 'uploaded_mdl'
  | 'manual'
  | 'enriched_markdown'
  | 'onboarding';

export interface SemanticProject {
  id: string;
  name: string;
  /** URL/identity-safe handle, unique within (database, catalog). */
  slug?: string;
  description?: string | null;
  owner_id: string;
  database_uri_fingerprint: string;
  database_backend?: string | null;
  database_label?: string | null;
  catalog_name?: string | null;
  schema_name: string;
  /** Full schema set the project covers (primary first). Defaults to
   * `[schema_name]` for single-schema projects. */
  schema_names?: string[];
  schema_display_name?: string | null;
  default_database_id?: number | null;
  visibility: SemanticProjectVisibility;
  current_version_id?: string | null;
  status: 'active' | 'archived';
  permission: SemanticProjectPermission;
  created_at: string;
  updated_at: string;
  deleted_at?: string | null;
  /** Latest complete coverage score (0–1) for the project's active MDL, supplied
   * by the list/get routes for the browser badge. `null`/absent when coverage has
   * never completed. */
  coverage_score?: number | null;
}

export interface SemanticProjectResolveRequest {
  database_id: number;
  database_label?: string | null;
  database_backend?: string | null;
  catalog_name?: string | null;
  schema_name: string;
  /** Optional additional schemas to scope the project to (primary stays
   * `schema_name`). Back-compat: callers may send only `schema_name`. */
  schema_names?: string[];
  /** User-chosen project name (MDL Lab "New project"); server derives one if blank. */
  name?: string | null;
  supplied_uri?: string | null;
  create_if_missing?: boolean;
}

export interface MdlValidationMessage {
  line?: number | null;
  column?: number | null;
  severity: 'error' | 'warning' | 'info';
  message: string;
  code?: string | null;
}

export interface MdlValidationResult {
  valid: boolean;
  messages: MdlValidationMessage[];
}

export interface MdlFile {
  id: string;
  project_id: string;
  path: string;
  filename: string;
  content: string;
  content_type: 'application/json';
  source_type: MdlFileSourceType;
  status: MdlFileStatus;
  validation?: MdlValidationResult | null;
  checksum: string;
  source_document_id?: string | null;
  created_by?: string | null;
  updated_by?: string | null;
  created_at: string;
  updated_at: string;
  deleted_at?: string | null;
}

export interface MdlFileCreateRequest {
  path: string;
  content: string;
  source_type?: MdlFileSourceType;
  source_document_id?: string | null;
}

export interface MdlFileUpdateRequest {
  path?: string | null;
  content?: string | null;
  status?: MdlFileStatus | null;
}

export interface MdlEnrichmentProposal {
  source_document_id: string;
  proposed_path: string;
  proposed_content: string;
  validation: MdlValidationResult;
  warnings: string[];
}

export interface WrenMaterializationResult {
  project_id: string;
  path: string;
  file_count: number;
  checksum: string;
  warnings: string[];
}

export interface OnboardingResult {
  project_id: string;
  files: MdlFile[];
  model_count: number;
  activated_count?: number;
  warnings: string[];
}

export type SemanticJobStatus = 'running' | 'completed' | 'failed';

export interface SemanticJob {
  id: string;
  kind: string;
  status: SemanticJobStatus;
  project_id?: string | null;
  result?: OnboardingResult | null;
  error?: string | null;
  created_at: string;
  updated_at: string;
}

export interface Instruction {
  id: string;
  instruction: string;
  is_global: boolean;
  project_id?: string | null;
  created_at: string;
}

export type SemanticProjectReadinessStatus =
  | 'empty'
  | 'indexing'
  | 'ready'
  | 'failed';

export interface SemanticProjectReadiness {
  status: SemanticProjectReadinessStatus;
  ready: boolean;
  has_active_models: boolean;
  active_model_count: number;
  running_job_id?: string | null;
  detail: string;
}

const trimTrailingSlash = (url: string) => url.replace(/\/+$/, '');

export const getAgentBaseUrl = () =>
  trimTrailingSlash(process.env.SUPERSET_AI_AGENT_URL || '/ai-agent');

/** Error carrying the HTTP status so callers can branch (e.g. handle 404). */
export class AgentApiError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = 'AgentApiError';
    this.status = status;
  }
}

const requestJson = async <T>(path: string, init?: RequestInit): Promise<T> => {
  const response = await fetch(`${getAgentBaseUrl()}${path}`, {
    ...init,
    credentials: 'include',
    headers: {
      'Content-Type': 'application/json',
      ...(init?.headers || {}),
    },
  });

  if (!response.ok) {
    throw new AgentApiError(
      await getAgentErrorMessage(response),
      response.status,
    );
  }

  return response.json() as Promise<T>;
};

const requestForm = async <T>(path: string, body: FormData): Promise<T> => {
  const response = await fetch(`${getAgentBaseUrl()}${path}`, {
    method: 'POST',
    credentials: 'include',
    body,
  });

  if (!response.ok) {
    throw new AgentApiError(
      await getAgentErrorMessage(response),
      response.status,
    );
  }

  return response.json() as Promise<T>;
};

const getAgentErrorMessage = async (response: Response) => {
  const detail = await response.text();
  if (!detail) {
    return `Agent API request failed: ${response.status}`;
  }
  try {
    const parsed = JSON.parse(detail) as { detail?: unknown };
    if (typeof parsed.detail === 'string' && parsed.detail.trim()) {
      return parsed.detail;
    }
  } catch {
    return detail;
  }
  return detail;
};

const semanticScopeParams = (scope: ConversationScope) => {
  const params = new URLSearchParams({
    database_id: String(scope.database_id),
  });
  if (scope.schema_name) {
    params.set('schema_name', scope.schema_name);
  }
  if (scope.catalog_name) {
    params.set('catalog_name', scope.catalog_name);
  }
  if (scope.dataset_ids.length > 0) {
    params.set('dataset_ids', scope.dataset_ids.join(','));
  }
  return params.toString();
};

export const getAgentHealth = () =>
  requestJson<AgentHealthResponse>('/health', { method: 'GET' });

// Short-lived health memo so repeat callers (e.g. opening the upload dialog
// multiple times) reuse one recent result instead of refetching /health each
// time (RG3, plan_document_upload_residual_gaps.md). Failures are NOT cached, so
// a later call can retry.
let agentHealthCache: { at: number; value: AgentHealthResponse } | null = null;
let agentHealthInFlight: Promise<AgentHealthResponse | null> | null = null;

/** Reset the health memo (test hook). */
export const resetAgentHealthCache = () => {
  agentHealthCache = null;
  agentHealthInFlight = null;
};

/**
 * Best-effort cached health. Returns `null` (never rejects) when /health is
 * unavailable so callers degrade to their own defaults — the fallback is silent
 * for the user by design (the backend stays the source of truth); only a
 * dev-facing debug line is emitted (RG4).
 */
export const getAgentHealthCached = (
  maxAgeMs = 60_000,
): Promise<AgentHealthResponse | null> => {
  if (agentHealthCache && Date.now() - agentHealthCache.at < maxAgeMs) {
    return Promise.resolve(agentHealthCache.value);
  }
  if (agentHealthInFlight) {
    return agentHealthInFlight;
  }
  agentHealthInFlight = getAgentHealth()
    .then(value => {
      agentHealthCache = { at: Date.now(), value };
      agentHealthInFlight = null;
      return value;
    })
    .catch(() => {
      // Degrade-closed: optional signal, never a user-facing error or a block.
      // eslint-disable-next-line no-console
      console.debug('[ai-agent] /health unavailable; using default limits');
      agentHealthInFlight = null;
      return null;
    });
  return agentHealthInFlight;
};

export const queryAgent = (payload: AgentQueryRequest) =>
  requestJson<AgentQueryResponse>('/agent/query', {
    method: 'POST',
    body: JSON.stringify(payload),
  });

export const validateSql = (sql: string, dialect?: string | null) =>
  requestJson<SqlValidationResult>('/agent/validate-sql', {
    method: 'POST',
    body: JSON.stringify({ sql, dialect: dialect || null }),
  });

export const createConversation = (scope: ConversationScope) =>
  requestJson<Conversation>('/agent/conversations', {
    method: 'POST',
    body: JSON.stringify({ scope }),
  });

export const listConversations = () =>
  requestJson<ConversationSummary[]>('/agent/conversations', { method: 'GET' });

export const getConversation = (conversationId: string) =>
  requestJson<Conversation>(`/agent/conversations/${conversationId}`, {
    method: 'GET',
  });

export const updateConversationTitle = (
  conversationId: string,
  title: string,
) =>
  requestJson<Conversation>(`/agent/conversations/${conversationId}`, {
    method: 'PATCH',
    body: JSON.stringify({ title }),
  });

export const sendConversationMessage = (
  conversationId: string,
  payload: ConversationTurnRequest,
) =>
  requestJson<ConversationTurnResponse>(
    `/agent/conversations/${conversationId}/messages`,
    {
      method: 'POST',
      body: JSON.stringify(payload),
    },
  );

export interface ConversationProgressEvent {
  type: 'progress';
  step: string;
  status: 'ok' | 'warning' | 'error';
  summary: string;
  // Full typed step for the explain-and-audit dialog to fill its sequence live
  // (ai_agent_explain_and_audit.md Seam 1). Optional for backward compatibility.
  agent_step?: AgentStep;
}

/**
 * Raised when the streaming endpoint cannot be used (missing, not OK, or no
 * response body). The caller may safely fall back to the buffered request
 * because the server has not yet persisted the user message in this case.
 */
export class StreamUnavailableError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'StreamUnavailableError';
  }
}

/**
 * Raised when a stream that had already started drops before delivering its
 * terminal `complete` event (e.g. a network blip). The turn may already be
 * running server-side, so the caller should resync the conversation rather than
 * re-send or surface a raw error.
 */
export class StreamInterruptedError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'StreamInterruptedError';
  }
}

interface StreamConversationOptions {
  onProgress?: (event: ConversationProgressEvent) => void;
  signal?: AbortSignal;
}

const isAbortError = (ex: unknown): boolean =>
  ex instanceof DOMException
    ? ex.name === 'AbortError'
    : ex instanceof Error && ex.name === 'AbortError';

const splitSseFrames = (buffer: string): { frames: string[]; rest: string } => {
  const frames: string[] = [];
  let rest = buffer;
  let boundary = rest.indexOf('\n\n');
  while (boundary !== -1) {
    frames.push(rest.slice(0, boundary));
    rest = rest.slice(boundary + 2);
    boundary = rest.indexOf('\n\n');
  }
  return { frames, rest };
};

const parseSseData = (frame: string): unknown => {
  const data = frame
    .split('\n')
    .filter(line => line.startsWith('data:'))
    .map(line => line.slice(5).replace(/^ /, ''))
    .join('\n');
  if (!data) {
    return undefined;
  }
  try {
    return JSON.parse(data);
  } catch {
    return undefined;
  }
};

/**
 * Read a conversation SSE stream to completion, surfacing each `progress` event
 * via `onProgress` and resolving with the final turn response carried by the
 * `complete` event. Shared by the message and execute-sql streaming endpoints.
 */
const consumeConversationStream = async (
  response: Response,
  onProgress?: (event: ConversationProgressEvent) => void,
): Promise<ConversationTurnResponse> => {
  let completed: ConversationTurnResponse | undefined;
  let streamError: string | undefined;
  const handleFrame = (frame: string) => {
    const data = parseSseData(frame) as
      | { type?: string; response?: ConversationTurnResponse; detail?: string }
      | undefined;
    if (!data || typeof data !== 'object') {
      return;
    }
    if (data.type === 'progress') {
      onProgress?.(data as unknown as ConversationProgressEvent);
    } else if (data.type === 'complete' && data.response) {
      completed = data.response;
    } else if (data.type === 'error') {
      streamError = data.detail || 'Agent request failed';
    }
  };

  const reader = response.body?.getReader ? response.body.getReader() : null;
  if (reader) {
    const decoder = new TextDecoder();
    let buffer = '';
    try {
      for (;;) {
        // eslint-disable-next-line no-await-in-loop
        const { value, done } = await reader.read();
        if (done) {
          break;
        }
        buffer += decoder.decode(value, { stream: true });
        const { frames, rest } = splitSseFrames(buffer);
        buffer = rest;
        frames.forEach(handleFrame);
      }
    } catch (ex) {
      // A user-initiated abort must propagate unchanged; any other read failure
      // is a dropped connection mid-turn.
      if (isAbortError(ex)) {
        throw ex;
      }
      throw new StreamInterruptedError(
        ex instanceof Error ? ex.message : 'Stream connection lost',
      );
    }
    splitSseFrames(`${buffer}\n\n`).frames.forEach(handleFrame);
  } else {
    // jsdom and some fetch polyfills do not expose a streaming body; parse the
    // buffered payload so the same code path still resolves the final response.
    const raw = await response.text();
    splitSseFrames(`${raw}\n\n`).frames.forEach(handleFrame);
  }

  // A server-emitted error event is a genuine agent failure.
  if (streamError) {
    throw new Error(streamError);
  }
  // The stream ended without its terminal event — treat as an interruption so
  // the caller resyncs instead of showing a raw error.
  if (!completed) {
    throw new StreamInterruptedError('The agent stream ended unexpectedly.');
  }
  return completed;
};

/**
 * POST to a conversation streaming endpoint and consume the SSE response.
 * Throws StreamUnavailableError when streaming is not possible so the caller can
 * fall back to the buffered request without re-sending. Abort errors are
 * re-thrown unchanged so the caller can distinguish user cancellation from a
 * transport failure.
 */
const streamConversationTurn = async (
  path: string,
  body: unknown,
  options: StreamConversationOptions = {},
): Promise<ConversationTurnResponse> => {
  let response: Response;
  try {
    response = await fetch(`${getAgentBaseUrl()}${path}`, {
      method: 'POST',
      credentials: 'include',
      headers: {
        'Content-Type': 'application/json',
        Accept: 'text/event-stream',
      },
      body: JSON.stringify(body),
      signal: options.signal,
    });
  } catch (ex) {
    if (isAbortError(ex)) {
      throw ex;
    }
    throw new StreamUnavailableError(
      ex instanceof Error ? ex.message : 'Stream request failed',
    );
  }

  if (!response.ok) {
    throw new StreamUnavailableError(`Stream unavailable: ${response.status}`);
  }

  return consumeConversationStream(response, options.onProgress);
};

/**
 * Stream a conversation turn. See {@link streamConversationTurn}; falls back to
 * {@link sendConversationMessage} on StreamUnavailableError.
 */
export const streamConversationMessage = (
  conversationId: string,
  payload: ConversationTurnRequest,
  options: StreamConversationOptions = {},
): Promise<ConversationTurnResponse> =>
  streamConversationTurn(
    `/agent/conversations/${conversationId}/messages/stream`,
    payload,
    options,
  );

/**
 * Stream an approved-SQL execution turn. See {@link streamConversationTurn};
 * falls back to {@link executeConversationSql} on StreamUnavailableError.
 */
export const streamExecuteConversationSql = (
  conversationId: string,
  payload: ConversationSqlExecutionRequest,
  options: StreamConversationOptions = {},
): Promise<ConversationTurnResponse> =>
  streamConversationTurn(
    `/agent/conversations/${conversationId}/execute-sql/stream`,
    payload,
    options,
  );

export const executeConversationSql = (
  conversationId: string,
  payload: ConversationSqlExecutionRequest,
) =>
  requestJson<ConversationTurnResponse>(
    `/agent/conversations/${conversationId}/execute-sql`,
    {
      method: 'POST',
      body: JSON.stringify(payload),
    },
  );

export const deleteConversation = (conversationId: string) =>
  requestJson<{ deleted: boolean }>(`/agent/conversations/${conversationId}`, {
    method: 'DELETE',
  });

export const uploadSemanticDocument = (
  scope: ConversationScope,
  file: File,
) => {
  const formData = new FormData();
  formData.append('scope', JSON.stringify(scope));
  formData.append('file', file);
  return requestForm<SemanticDocument>(
    '/agent/semantic-layer/documents',
    formData,
  );
};

export const resolveSemanticProject = (
  payload: SemanticProjectResolveRequest,
) =>
  requestJson<SemanticProject>('/agent/semantic-layer/projects/resolve', {
    method: 'POST',
    body: JSON.stringify(payload),
  });

export const listSemanticProjects = (
  databaseId: number,
  catalogName?: string | null,
  schemaName?: string | null,
) => {
  const params = new URLSearchParams({ database_id: String(databaseId) });
  if (catalogName) {
    params.set('catalog_name', catalogName);
  }
  if (schemaName) {
    params.set('schema_name', schemaName);
  }
  return requestJson<SemanticProject[]>(
    `/agent/semantic-layer/projects?${params.toString()}`,
    { method: 'GET' },
  );
};

/** Fetch one semantic project by id (governed by DB access). */
export const getSemanticProject = (projectId: string) =>
  requestJson<SemanticProject>(`/agent/semantic-layer/projects/${projectId}`, {
    method: 'GET',
  });

/** Create a new named semantic project (MDL Lab "New project"). */
export const createSemanticProject = (payload: SemanticProjectResolveRequest) =>
  requestJson<SemanticProject>('/agent/semantic-layer/projects', {
    method: 'POST',
    body: JSON.stringify(payload),
  });

/** Rename a semantic project (re-derives a unique slug server-side). */
export const renameSemanticProject = (projectId: string, name: string) =>
  requestJson<SemanticProject>(`/agent/semantic-layer/projects/${projectId}`, {
    method: 'PATCH',
    body: JSON.stringify({ name }),
  });

/**
 * Duplicate a project's MDL structure into a new project (fresh history).
 * With `includeDocuments`, also copies the BI documents + chunks and re-embeds
 * them under the clone's vector scope (DP6 opt-in); off by default.
 */
export const duplicateSemanticProject = (
  projectId: string,
  name?: string | null,
  includeDocuments = false,
) =>
  requestJson<SemanticProject>(
    `/agent/semantic-layer/projects/${projectId}/duplicate`,
    {
      method: 'POST',
      body: JSON.stringify({
        name: name ?? null,
        include_documents: includeDocuments,
      }),
    },
  );

/** Archive a semantic project. */
export const deleteSemanticProject = (projectId: string) =>
  requestJson<{ deleted: boolean }>(
    `/agent/semantic-layer/projects/${projectId}`,
    { method: 'DELETE' },
  );

export const listMdlFiles = (projectId: string) =>
  requestJson<MdlFile[]>(
    `/agent/semantic-layer/projects/${projectId}/mdl-files`,
    { method: 'GET' },
  );

export const createMdlFile = (
  projectId: string,
  payload: MdlFileCreateRequest,
) =>
  requestJson<MdlFile>(
    `/agent/semantic-layer/projects/${projectId}/mdl-files`,
    {
      method: 'POST',
      body: JSON.stringify(payload),
    },
  );

export const updateMdlFile = (
  projectId: string,
  fileId: string,
  payload: MdlFileUpdateRequest,
) =>
  requestJson<MdlFile>(
    `/agent/semantic-layer/projects/${projectId}/mdl-files/${fileId}`,
    {
      method: 'PATCH',
      body: JSON.stringify(payload),
    },
  );

export interface MdlBulkStatusResult {
  files: MdlFile[];
  changed_count: number;
}

// Activate or deactivate many MDL files atomically. Activation validates the
// whole projected active manifest once on the server, so dependent files (a
// metric and the model it references) are activated together without ordering —
// replacing the per-file loop that raced and failed on dependency order.
export const setMdlFilesStatus = (
  projectId: string,
  status: MdlFileStatus,
  fileIds?: string[],
) =>
  requestJson<MdlBulkStatusResult>(
    `/agent/semantic-layer/projects/${projectId}/mdl-files/bulk-status`,
    {
      method: 'POST',
      body: JSON.stringify({ status, file_ids: fileIds ?? null }),
    },
  );

export const deleteMdlFile = (projectId: string, fileId: string) =>
  requestJson<{ deleted: boolean }>(
    `/agent/semantic-layer/projects/${projectId}/mdl-files/${fileId}`,
    { method: 'DELETE' },
  );

export const validateMdlFile = (projectId: string, fileId: string) =>
  requestJson<MdlValidationResult>(
    `/agent/semantic-layer/projects/${projectId}/mdl-files/${fileId}/validate`,
    { method: 'POST' },
  );

// -- MDL Copilot (wren_mdl_copilot.md) --------------------------------------

export type ChangesetOp = 'create' | 'update' | 'delete';

export type WorkspaceNodeKind =
  | 'folder'
  | 'mdl'
  | 'instructions'
  | 'queries'
  | 'document'
  | 'compiled'
  | 'memory'
  | 'config';

export interface WorkspaceNode {
  path: string;
  name: string;
  kind: WorkspaceNodeKind;
  editable: boolean;
  status?: string | null;
  file_id?: string | null;
  document_id?: string | null;
  validation?: MdlValidationResult | null;
  children: WorkspaceNode[];
}

export interface ChangesetItem {
  op: ChangesetOp;
  path: string;
  file_id?: string | null;
  current_content?: string | null;
  proposed_content?: string | null;
  validation?: MdlValidationResult | null;
  summary: string;
}

export interface Changeset {
  items: ChangesetItem[];
  manifest_validation?: MdlValidationResult | null;
  warnings: string[];
  steps: AgentStep[];
  message: string;
}

export interface MessageAttachment {
  filename: string;
  content_type: string;
  text: string;
  truncated?: boolean;
}

export interface CopilotTurnRequest {
  message: string;
  attachments?: MessageAttachment[];
  conversation_id?: string | null;
  model?: string | null;
  max_steps?: number;
}

export interface CopilotToolDescriptor {
  name: string;
  description: string;
}

export interface CopilotSkillDescriptor {
  name: string;
  text: string;
}

export interface CopilotInstructionView {
  id: string;
  instruction: string;
  is_global: boolean;
}

export interface CopilotInspector {
  system_prompt: string;
  skills: CopilotSkillDescriptor[];
  tools: CopilotToolDescriptor[];
  instructions: CopilotInstructionView[];
}

export const getProjectWorkspace = (projectId: string) =>
  requestJson<WorkspaceNode>(
    `/agent/semantic-layer/projects/${projectId}/workspace`,
    { method: 'GET' },
  );

export const getCopilotInspector = (projectId: string) =>
  requestJson<CopilotInspector>(
    `/agent/semantic-layer/projects/${projectId}/copilot/inspector`,
    { method: 'GET' },
  );

export const getCopilotDeployPreview = (projectId: string) =>
  requestJson<Changeset>(
    `/agent/semantic-layer/projects/${projectId}/copilot/deploy-preview`,
    { method: 'GET' },
  );

// -- Copilot conversations (persistent, multi-turn threads) -----------------
// Parallel to the AI SQL conversation client but project-scoped. The shared
// backend store tags these threads `kind="copilot"`.

const copilotConversationsPath = (projectId: string) =>
  `/agent/semantic-layer/projects/${projectId}/copilot/conversations`;

export const createCopilotConversation = (projectId: string) =>
  requestJson<Conversation>(copilotConversationsPath(projectId), {
    method: 'POST',
  });

export const listCopilotConversations = (projectId: string) =>
  requestJson<ConversationSummary[]>(copilotConversationsPath(projectId), {
    method: 'GET',
  });

export const getCopilotConversation = (
  projectId: string,
  conversationId: string,
) =>
  requestJson<Conversation>(
    `${copilotConversationsPath(projectId)}/${conversationId}`,
    { method: 'GET' },
  );

export const updateCopilotConversationTitle = (
  projectId: string,
  conversationId: string,
  title: string,
) =>
  requestJson<Conversation>(
    `${copilotConversationsPath(projectId)}/${conversationId}`,
    { method: 'PATCH', body: JSON.stringify({ title }) },
  );

export const deleteCopilotConversation = (
  projectId: string,
  conversationId: string,
) =>
  requestJson<{ deleted: boolean }>(
    `${copilotConversationsPath(projectId)}/${conversationId}`,
    { method: 'DELETE' },
  );

export type CoverageClaimKind =
  | 'definition'
  | 'metric'
  | 'synonym'
  | 'relationship'
  | 'filter'
  | 'dimension'
  | 'rule'
  | 'other';

export type CoverageStatus = 'covered' | 'partial' | 'missing';

export interface CoverageClaim {
  kind: CoverageClaimKind;
  subject: string;
  statement: string;
  source_quote?: string;
}

export interface CoverageFinding {
  claim: CoverageClaim;
  status: CoverageStatus;
  matched?: string;
  rationale?: string;
  suggestion?: string;
  /** Source document this claim came from (directory coverage only). */
  document_id?: string | null;
  document_filename?: string;
}

export interface OverreachFinding {
  fact_ref: string;
  fact_kind?: string;
  supported: boolean;
  rationale?: string;
}

export interface CoverageReport {
  document_id?: string | null;
  document_filename: string;
  findings: CoverageFinding[];
  total: number;
  covered: number;
  partial: number;
  missing: number;
  score: number;
  overreach: OverreachFinding[];
  unsupported: number;
  warnings: string[];
}

export const runCoverage = (
  projectId: string,
  documentId: string,
  includeOverreach = false,
) =>
  requestJson<CoverageReport>(
    `/agent/semantic-layer/projects/${projectId}/copilot/coverage`,
    {
      method: 'POST',
      body: JSON.stringify({
        document_id: documentId,
        include_overreach: includeOverreach,
      }),
    },
  );

// -- Background directory coverage (Feature B) ------------------------------

export type CoverageRunStatus =
  | 'pending'
  | 'running'
  | 'complete'
  | 'failed'
  | 'superseded';

export interface CoverageRun {
  id: string;
  project_id: string;
  owner_id: string;
  mdl_checksum: string;
  docs_checksum: string;
  status: CoverageRunStatus;
  score?: number | null;
  report?: CoverageReport | null;
  error?: string | null;
  created_at: string;
  updated_at: string;
}

/** Live, coarse progress of an in-flight coverage run (Feature C). */
export interface CoverageProgressInfo {
  /** Backend stage: extracting | building_facts | judging | checking_overreach | aggregating. */
  stage?: string;
  /** Human-readable detail, e.g. a filename or "142 claims vs 38 facts". */
  detail?: string;
  /** Countable progress within a stage (e.g. document 2 of 5). */
  current?: number;
  total?: number;
  /** Coarse pipeline position for the stepper (0-based). */
  phase_index?: number;
  phase_total?: number;
}

export interface CoverageStatusInfo {
  status: 'analysing' | 'stale' | 'ready' | 'none';
  running: boolean;
  stale: boolean;
  score?: number | null;
  run_id?: string | null;
  /** Present only while a run is in flight; null otherwise (Feature C). */
  progress?: CoverageProgressInfo | null;
}

/** The latest completed directory coverage run (score + report), or null. */
export const getLatestCoverage = (projectId: string) =>
  requestJson<CoverageRun | null>(
    `/agent/semantic-layer/projects/${projectId}/coverage/latest`,
    { method: 'GET' },
  );

/** Fetch one stored coverage run by id (provenance drill-in). */
export const getCoverageRun = (projectId: string, runId: string) =>
  requestJson<CoverageRun>(
    `/agent/semantic-layer/projects/${projectId}/coverage/runs/${runId}`,
    { method: 'GET' },
  );

/** Live coverage state for the editor badge (analysing / stale / ready). */
export const getCoverageStatus = (projectId: string) =>
  requestJson<CoverageStatusInfo>(
    `/agent/semantic-layer/projects/${projectId}/coverage/status`,
    { method: 'GET' },
  );

/** Manually (re)schedule a directory coverage run on the current MDL. */
export const refreshCoverage = (projectId: string) =>
  requestJson<{ scheduled: boolean }>(
    `/agent/semantic-layer/projects/${projectId}/coverage/refresh`,
    { method: 'POST' },
  );

/** Latest coverage score for one MDL version (Feature B label overlay). */
export interface CoverageVersionScore {
  score?: number | null;
  run_id: string;
  status: CoverageRunStatus;
  computed_at: string;
  docs_checksum: string;
}

/** Map of mdl_checksum → latest coverage score, for labelling provenance. */
export type CoverageScoresByVersion = Record<string, CoverageVersionScore>;

/**
 * Coverage score per MDL version (keyed by mdl_checksum). The provenance dialog
 * joins this against each entry's detail.mdl_checksum to render a coverage label
 * and before/after delta — a read-only overlay, never a timeline entry.
 */
export const getCoverageScoresByVersion = (projectId: string) =>
  requestJson<CoverageScoresByVersion>(
    `/agent/semantic-layer/projects/${projectId}/coverage/scores-by-version`,
    { method: 'GET' },
  );

export const runCopilot = (projectId: string, payload: CopilotTurnRequest) =>
  requestJson<Changeset>(
    `/agent/semantic-layer/projects/${projectId}/copilot`,
    { method: 'POST', body: JSON.stringify(payload) },
  );

export const applyCopilotChangeset = (
  projectId: string,
  items: ChangesetItem[],
  conversationId?: string | null,
) =>
  requestJson<MdlFile[]>(
    `/agent/semantic-layer/projects/${projectId}/copilot/apply`,
    {
      method: 'POST',
      body: JSON.stringify({ items, conversation_id: conversationId ?? null }),
    },
  );

/**
 * Stream the agentic edit loop. Each `progress` event delivers an AgentStep via
 * `onStep`; resolves with the final Changeset carried by the `complete` event.
 */
export const streamCopilot = async (
  projectId: string,
  payload: CopilotTurnRequest,
  onStep?: (step: AgentStep) => void,
): Promise<Changeset> => {
  const response = await fetch(
    `${getAgentBaseUrl()}/agent/semantic-layer/projects/${projectId}/copilot/stream`,
    {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    },
  );
  if (!response.ok || !response.body) {
    throw new Error(await getAgentErrorMessage(response));
  }

  let changeset: Changeset | undefined;
  let streamError: string | undefined;
  const handleFrame = (frame: string) => {
    const data = parseSseData(frame) as
      | {
          type?: string;
          agent_step?: AgentStep;
          changeset?: Changeset;
          detail?: string;
        }
      | undefined;
    if (!data || typeof data !== 'object') {
      return;
    }
    if (data.type === 'progress' && data.agent_step) {
      onStep?.(data.agent_step);
    } else if (data.type === 'complete' && data.changeset) {
      changeset = data.changeset;
    } else if (data.type === 'error') {
      streamError = data.detail || 'Copilot stream failed.';
    }
  };

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  for (;;) {
    // eslint-disable-next-line no-await-in-loop
    const { value, done } = await reader.read();
    if (done) {
      break;
    }
    buffer += decoder.decode(value, { stream: true });
    const { frames, rest } = splitSseFrames(buffer);
    buffer = rest;
    frames.forEach(handleFrame);
  }
  splitSseFrames(`${buffer}\n\n`).frames.forEach(handleFrame);

  if (streamError) {
    throw new Error(streamError);
  }
  if (!changeset) {
    throw new Error('Copilot stream ended without a changeset.');
  }
  return changeset;
};

export const uploadMdlFile = (
  projectId: string,
  file: File,
  path?: string | null,
) => {
  const formData = new FormData();
  if (path) {
    formData.append('path', path);
  }
  formData.append('file', file);
  return requestForm<MdlFile>(
    `/agent/semantic-layer/projects/${projectId}/mdl-files/upload`,
    formData,
  );
};

export const listProjectDocuments = (projectId: string) =>
  requestJson<SemanticDocument[]>(
    `/agent/semantic-layer/projects/${projectId}/documents`,
    { method: 'GET' },
  );

export const uploadProjectSourceDocument = (projectId: string, file: File) => {
  const formData = new FormData();
  formData.append('file', file);
  return requestForm<SemanticDocument>(
    `/agent/semantic-layer/projects/${projectId}/documents`,
    formData,
  );
};

export const createProjectDocumentFromText = (
  projectId: string,
  text: string,
  filename = 'document.md',
) =>
  requestJson<SemanticDocument>(
    `/agent/semantic-layer/projects/${projectId}/documents/text`,
    {
      method: 'POST',
      body: JSON.stringify({ filename, text }),
    },
  );

export const enrichProjectDocument = (projectId: string, documentId: string) =>
  requestJson<MdlEnrichmentProposal>(
    `/agent/semantic-layer/projects/${projectId}/documents/${documentId}/enrich`,
    { method: 'POST' },
  );

// -- Superset dataset/table helpers (registration gap, not the agent backend) --
// These call Superset's own REST API (via SupersetClient for CSRF/session), not
// the standalone agent. MDL onboarding consumes registered datasets only, so the
// picker uses these to (a) count the schema's physical tables for the gap banner
// and (b) register an unregistered physical table as a dataset inline.

export interface PhysicalTablesResult {
  count: number;
  /** Physical table/view names in the schema (datasets may or may not exist). */
  names: string[];
}

/**
 * List the schema's physical tables/views (the same endpoint the SQL Lab tree
 * uses). Un-paginated: one call returns the whole schema. Used only to size the
 * "N of M registered" banner, never to render rows.
 */
export const listPhysicalTables = async (
  databaseId: number,
  schema: string,
  catalog?: string | null,
): Promise<PhysicalTablesResult> => {
  const query = rison.encode({
    force: false,
    schema_name: schema,
    ...(catalog ? { catalog_name: catalog } : {}),
  });
  const response = await SupersetClient.get({
    endpoint: `/api/v1/database/${databaseId}/tables/?q=${query}`,
  });
  const result =
    (response.json?.result as { value: string; type: string }[]) ?? [];
  return {
    count: (response.json?.count as number) ?? result.length,
    names: result.map(item => item.value),
  };
};

/**
 * Register a physical table as a Superset dataset (so MDL can onboard it).
 * Mirrors the Add Dataset flow's payload. Resolves with the new dataset id.
 */
export const createDataset = async (params: {
  databaseId: number;
  schema: string;
  tableName: string;
  catalog?: string | null;
}): Promise<number> => {
  const response = await SupersetClient.post({
    endpoint: '/api/v1/dataset/',
    jsonPayload: {
      database: params.databaseId,
      catalog: params.catalog ?? null,
      schema: params.schema,
      table_name: params.tableName,
    },
  });
  return response.json.id as number;
};

export interface RegisteredTable {
  id: number;
  tableName: string;
}

export interface RegisteredTables {
  tables: RegisteredTable[];
  /** True when the cap was hit before all pages were read (possible misclassify). */
  truncated: boolean;
}

export interface RegisteredTableNames {
  names: string[];
  /** True when the cap was hit before all pages were read (possible misclassify). */
  truncated: boolean;
}

// Bound the authoritative-name scan. Registered datasets are typically far fewer
// than physical tables, so this is rarely approached; it caps pathological cases.
export const REGISTERED_NAME_SCAN_CAP = 5000;

/**
 * Fetch the COMPLETE set of registered datasets for a schema (id + table_name
 * only — `columns` projection keeps the payload tiny), paging at the DAO max of
 * 1000/page until exhausted or the cap is reached. This is the authoritative
 * registered set for a schema: it drives both the onboarding tree's rows and the
 * "unregistered" classification (R1), so a registered dataset is never missed by
 * relying on an eventually-consistent display page.
 */
export const listAllRegisteredTables = async (
  databaseId: number,
  schema: string,
  cap: number = REGISTERED_NAME_SCAN_CAP,
): Promise<RegisteredTables> => {
  const PAGE = 1000; // SQLALCHEMY_DAO_MAX_PAGE_SIZE
  const tables: RegisteredTable[] = [];
  let page = 0;
  let total = Infinity;
  let truncated = false;
  while (tables.length < total) {
    const query = rison.encode({
      columns: ['id', 'table_name'],
      filters: [
        { col: 'database', opr: 'rel_o_m', value: databaseId },
        { col: 'schema', opr: 'eq', value: schema },
      ],
      order_column: 'table_name',
      order_direction: 'asc',
      page,
      page_size: PAGE,
    });
    // eslint-disable-next-line no-await-in-loop
    const response = await SupersetClient.get({
      endpoint: `/api/v1/dataset/?q=${query}`,
    });
    const result =
      (response.json?.result as { id: number; table_name: string }[]) ?? [];
    total = (response.json?.count as number) ?? tables.length + result.length;
    result.forEach(item =>
      tables.push({ id: item.id, tableName: item.table_name }),
    );
    if (result.length === 0) break; // defensive: no progress
    if (tables.length >= cap) {
      truncated = tables.length < total;
      break;
    }
    page += 1;
  }
  return { tables, truncated };
};

/**
 * Names-only view of {@link listAllRegisteredTables}, kept for callers that only
 * need the registered-name set.
 */
export const listAllRegisteredTableNames = async (
  databaseId: number,
  schema: string,
  cap: number = REGISTERED_NAME_SCAN_CAP,
): Promise<RegisteredTableNames> => {
  const { tables, truncated } = await listAllRegisteredTables(
    databaseId,
    schema,
    cap,
  );
  return { names: tables.map(table => table.tableName), truncated };
};

/**
 * Whether the current user may create datasets. Reads the FAB `_info` endpoint's
 * `permissions` array (the same signal Superset's Dataset list uses to show its
 * "Create Dataset" button). Used to gate inline registration on the *real*
 * Dataset `can_write` rather than a project-write proxy.
 */
export const getDatasetWritePermission = async (): Promise<boolean> => {
  const query = rison.encode({ keys: ['permissions'] });
  const response = await SupersetClient.get({
    endpoint: `/api/v1/dataset/_info?q=${query}`,
  });
  const permissions = (response.json?.permissions as string[]) ?? [];
  return permissions.includes('can_write');
};

/**
 * Which tables to onboard (Feature A). Absent/`undefined` ≡ whole schema (the
 * legacy behavior). `mode:'include'` onboards exactly `datasetIds`; `mode:'all'`
 * onboards every dataset in the schema minus `excludeDatasetIds`.
 */
export interface OnboardingSelection {
  mode: 'all' | 'include';
  datasetIds?: number[];
  excludeDatasetIds?: number[];
  search?: string | null;
}

const onboardingBody = (selection?: OnboardingSelection) => {
  if (!selection) return {};
  return {
    mode: selection.mode,
    dataset_ids: selection.datasetIds ?? [],
    exclude_dataset_ids: selection.excludeDatasetIds ?? [],
    search: selection.search ?? null,
  };
};

export const onboardSemanticProject = (
  projectId: string,
  selection?: OnboardingSelection,
) =>
  requestJson<SemanticJob>(
    `/agent/semantic-layer/projects/${projectId}/onboard`,
    { method: 'POST', body: JSON.stringify(onboardingBody(selection)) },
  );

/**
 * Whether the project's MDL base layer is onboarded and stable enough for the
 * Copilot. The editor polls this to show a spinner (indexing) or an onboarding
 * prompt (empty/failed) before mounting the Copilot.
 */
export const getProjectReadiness = (projectId: string) =>
  requestJson<SemanticProjectReadiness>(
    `/agent/semantic-layer/projects/${projectId}/readiness`,
    { method: 'GET' },
  );

export const getSemanticJob = (projectId: string, jobId: string) =>
  requestJson<SemanticJob>(
    `/agent/semantic-layer/projects/${projectId}/jobs/${jobId}`,
    { method: 'GET' },
  );

/**
 * Reset a project: delete all MDL so it returns to the un-onboarded (`empty`)
 * state. Does NOT re-onboard — onboarding is always an explicit user action.
 * Resolves with the number of MDL files deleted.
 */
export const resetSemanticProject = (projectId: string) =>
  requestJson<{ deleted: number }>(
    `/agent/semantic-layer/projects/${projectId}/reset`,
    { method: 'POST' },
  );

// NOTE: onboarding is started with ``onboardSemanticProject`` (returns the job in
// its initial state — ``completed`` for an inline backend, ``running`` for the
// threaded prod backend) and then polled with ``getSemanticJob`` until terminal.
// There is deliberately no combined start-and-poll helper here: a fixed
// foreground poll budget would mis-time real onboarding (which can run for
// minutes) and report a still-``running`` job as done. The editor owns the poll
// loop instead, so the spinner and file list stay in sync until the job is truly
// terminal regardless of how long onboarding takes.

/**
 * Reset a project (delete all MDL). No onboarding job is created — the project
 * returns to the `empty` state and the user re-onboards explicitly. Resolves
 * with the number of MDL files deleted.
 */
export const runReset = (projectId: string): Promise<{ deleted: number }> =>
  resetSemanticProject(projectId);

export const materializeSemanticProject = (projectId: string) =>
  requestJson<WrenMaterializationResult>(
    `/agent/semantic-layer/projects/${projectId}/materialize`,
    { method: 'POST' },
  );

export const getProjectSemanticLayerState = (projectId: string) =>
  requestJson<SemanticLayerState>(
    `/agent/semantic-layer/projects/${projectId}/state`,
    { method: 'GET' },
  );

// -- MDL provenance (Feature B) ---------------------------------------------

export type ProvenanceKind =
  | 'onboarding'
  | 'enrichment'
  | 'copilot_edit'
  | 'coverage'
  | 'mdl_created'
  | 'mdl_updated'
  | 'mdl_activated'
  | 'mdl_deleted'
  | 'project_created';

export type ProvenanceActorType = 'user' | 'agent' | 'system';

/** Semantic verb for one MDL-mutating agent tool call (provenance ledger). */
export type ToolActionKind = 'write' | 'delete' | 'onboard' | 'relate';

/**
 * One MDL-mutating tool call an agent made during a turn. Folded into an
 * apply entry's `detail.tool_calls` so the timeline can roll up a per-verb
 * summary and link a written file to its source document (R-B6).
 */
export interface ToolCallRecord {
  tool: string;
  action: ToolActionKind;
  paths: string[];
  source_document_ids: string[];
  args_summary: Record<string, unknown>;
  status: 'ok' | 'error';
  detail?: string | null;
}

export interface ProvenanceEntry {
  id: string;
  kind: ProvenanceKind;
  status: 'ok' | 'warning' | 'error';
  summary: string;
  created_at: string;
  actor?: string | null;
  /** Author's captured display name (username/email); null for system/old entries. */
  actor_name?: string | null;
  actor_type?: ProvenanceActorType;
  /** True when the viewer is the actor (DP10): drives "You" vs the actor's id. */
  is_self?: boolean;
  /** Number of raw events merged into this entry (>1 for coalesced user runs). */
  edit_count?: number;
  /** Earliest timestamp in a coalesced user run (null when edit_count === 1). */
  first_at?: string | null;
  detail: Record<string, unknown>;
}

/**
 * The MDL directory's provenance timeline (onboarding / enrichment / CRUD),
 * newest-first. Excludes document events and resets when the MDL is reset.
 */
export const getMdlProvenance = (projectId: string) =>
  requestJson<ProvenanceEntry[]>(
    `/agent/semantic-layer/projects/${projectId}/provenance`,
    { method: 'GET' },
  );

export const listSemanticDocuments = (scope: ConversationScope) =>
  requestJson<SemanticDocument[]>(
    `/agent/semantic-layer/documents?${semanticScopeParams(scope)}`,
    { method: 'GET' },
  );

export const getSemanticDocument = (documentId: string) =>
  requestJson<SemanticDocument>(
    `/agent/semantic-layer/documents/${documentId}`,
    { method: 'GET' },
  );

export const deleteSemanticDocument = (documentId: string) =>
  requestJson<SemanticDocument>(
    `/agent/semantic-layer/documents/${documentId}`,
    { method: 'DELETE' },
  );

export const listDocumentChunks = (documentId: string) =>
  requestJson<DocumentChunk[]>(
    `/agent/semantic-layer/documents/${documentId}/chunks`,
    { method: 'GET' },
  );

export const retrieveDocumentChunks = (
  documentId: string,
  query: string,
  k?: number,
) => {
  const params = new URLSearchParams({ q: query });
  if (k != null) {
    params.set('k', String(k));
  }
  return requestJson<DocumentChunk[]>(
    `/agent/semantic-layer/documents/${documentId}/retrieve?${params}`,
    { method: 'GET' },
  );
};

export const reindexSemanticDocument = (documentId: string) =>
  requestJson<DocumentChunk[]>(
    `/agent/semantic-layer/documents/${documentId}/reindex`,
    { method: 'POST' },
  );

export const summarizeSemanticDocument = (documentId: string) =>
  requestJson<SemanticDocument>(
    `/agent/semantic-layer/documents/${documentId}/summarize`,
    { method: 'POST' },
  );

export const findProjectDuplicateChunks = (projectId: string) =>
  requestJson<DocumentChunkMatch[]>(
    `/agent/semantic-layer/projects/${projectId}/documents/duplicates`,
    { method: 'POST' },
  );

// Raw-file download bypasses the JSON helper (binary body, Content-Disposition).
export const downloadDocumentUrl = (documentId: string) =>
  `${getAgentBaseUrl()}/agent/semantic-layer/documents/${documentId}/content`;

export const getSemanticLayerState = (scope: ConversationScope) =>
  requestJson<SemanticLayerState>(
    `/agent/semantic-layer/state?${semanticScopeParams(scope)}`,
    { method: 'GET' },
  );

export const createSemanticLayerEventSource = (scope: ConversationScope) =>
  new EventSource(
    `${getAgentBaseUrl()}/agent/semantic-layer/events?${semanticScopeParams(
      scope,
    )}`,
    { withCredentials: true },
  );

export const createProjectSemanticLayerEventSource = (projectId: string) =>
  new EventSource(
    `${getAgentBaseUrl()}/agent/semantic-layer/projects/${projectId}/events`,
    { withCredentials: true },
  );

export const listInstructions = (scope: ConversationScope) =>
  requestJson<Instruction[]>(
    `/agent/semantic-layer/instructions?${semanticScopeParams(scope)}`,
    { method: 'GET' },
  );

export const createInstruction = (
  scope: ConversationScope,
  instruction: string,
  isGlobal: boolean,
) =>
  requestJson<Instruction>('/agent/semantic-layer/instructions', {
    method: 'POST',
    body: JSON.stringify({ scope, instruction, is_global: isGlobal }),
  });

export const deleteInstruction = (instructionId: string) =>
  requestJson<{ deleted: boolean }>(
    `/agent/semantic-layer/instructions/${instructionId}`,
    { method: 'DELETE' },
  );
