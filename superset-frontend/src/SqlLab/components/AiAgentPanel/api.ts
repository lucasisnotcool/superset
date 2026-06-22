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

export type AgentStatus = 'ok' | 'needs_review' | 'error';
export type ExecutionMode = 'manual' | 'read_only' | 'auto';

export interface AgentTraceEvent {
  step: string;
  status: 'ok' | 'warning' | 'error';
  summary: string;
  details: Record<string, unknown>;
}

export interface SqlValidationResult {
  is_valid: boolean;
  is_read_only: boolean;
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
  semantic_layer_version?: string | null;
  indexing_status?: string | null;
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
}

export interface ConversationScope {
  database_id: number;
  catalog_name?: string | null;
  schema_name?: string | null;
  dataset_ids: number[];
  query_editor_id?: string | null;
  current_sql?: string | null;
  selected_text?: string | null;
}

export interface ConversationArtifact {
  id: string;
  type: 'sql';
  sql: string;
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
}

export type SemanticDocumentStatus =
  | 'uploaded'
  | 'extracted'
  | 'needs_review'
  | 'approved'
  | 'indexed'
  | 'error';

export interface SemanticUpdate {
  id: string;
  kind:
    | 'model_description'
    | 'field_description'
    | 'metric'
    | 'synonym'
    | 'example'
    | 'relationship';
  target: Record<string, unknown>;
  value: Record<string, unknown>;
  confidence?: number | null;
  source_document_id: string;
  reviewed: boolean;
  approved: boolean;
  reviewer_id?: string | null;
  review_notes?: string | null;
  created_at: string;
  updated_at: string;
  reviewed_at?: string | null;
}

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
  proposed_updates: SemanticUpdate[];
  warnings: string[];
  error?: string | null;
  created_at: string;
  updated_at: string;
}

export interface SemanticLayerReviewRequest {
  approved_update_ids: string[];
  rejected_update_ids: string[];
  edited_updates: SemanticUpdate[];
  notes?: string | null;
}

export interface SemanticLayerState {
  project_id?: string | null;
  database_id: number;
  catalog_name?: string | null;
  schema_name?: string | null;
  dataset_ids: number[];
  document_count: number;
  approved_document_count: number;
  indexed_document_count: number;
  semantic_layer_version?: string | null;
  indexing_status: 'idle' | 'running' | 'error';
  last_error?: string | null;
}

export interface SemanticLayerVersion {
  id: string;
  project_id?: string | null;
  scope: ConversationScope;
  scope_hash: string;
  version: string;
  status: 'idle' | 'running' | 'error';
  mdl?: Record<string, unknown> | null;
  wren_context?: WrenContextArtifact | null;
  source_update_ids: string[];
  published_semantic_layer_uuid?: string | null;
  created_at: string;
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
  scope: ConversationScope;
  messages: ConversationMessage[];
  created_at: string;
  updated_at: string;
}

export interface ConversationSummary {
  id: string;
  title: string;
  owner_id: string;
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
  description?: string | null;
  owner_id: string;
  database_uri_fingerprint: string;
  database_backend?: string | null;
  database_label?: string | null;
  catalog_name?: string | null;
  schema_name: string;
  schema_display_name?: string | null;
  default_database_id?: number | null;
  visibility: SemanticProjectVisibility;
  current_version_id?: string | null;
  status: 'active' | 'archived';
  permission: SemanticProjectPermission;
  created_at: string;
  updated_at: string;
  deleted_at?: string | null;
}

export interface SemanticProjectResolveRequest {
  database_id: number;
  database_label?: string | null;
  database_backend?: string | null;
  catalog_name?: string | null;
  schema_name: string;
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
  content_type: 'application/x-yaml' | 'text/yaml';
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
  proposed_yaml: string;
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

const trimTrailingSlash = (url: string) => url.replace(/\/+$/, '');

export const getAgentBaseUrl = () =>
  trimTrailingSlash(process.env.SUPERSET_AI_AGENT_URL || '/ai-agent');

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
    throw new Error(await getAgentErrorMessage(response));
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
    throw new Error(await getAgentErrorMessage(response));
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

export const onboardSemanticProject = (projectId: string) =>
  requestJson<SemanticJob>(
    `/agent/semantic-layer/projects/${projectId}/onboard`,
    { method: 'POST' },
  );

export const getSemanticJob = (projectId: string, jobId: string) =>
  requestJson<SemanticJob>(
    `/agent/semantic-layer/projects/${projectId}/jobs/${jobId}`,
    { method: 'GET' },
  );

/**
 * Start onboarding and resolve once the async job reaches a terminal state.
 * Polls the job endpoint; an inline backend completes on the first response.
 */
export const runOnboarding = async (
  projectId: string,
  {
    intervalMs = 1000,
    attempts = 60,
  }: { intervalMs?: number; attempts?: number } = {},
): Promise<SemanticJob> => {
  let job = await onboardSemanticProject(projectId);
  let remaining = attempts;
  while (job.status === 'running' && remaining > 0) {
    // eslint-disable-next-line no-await-in-loop
    await new Promise(resolve => {
      setTimeout(resolve, intervalMs);
    });
    // eslint-disable-next-line no-await-in-loop
    job = await getSemanticJob(projectId, job.id);
    remaining -= 1;
  }
  return job;
};

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

export const reviewSemanticDocument = (
  documentId: string,
  payload: SemanticLayerReviewRequest,
) =>
  requestJson<SemanticDocument>(
    `/agent/semantic-layer/documents/${documentId}/review`,
    {
      method: 'PATCH',
      body: JSON.stringify(payload),
    },
  );

export const rebuildSemanticLayerIndex = (scope: ConversationScope) =>
  requestJson<SemanticLayerVersion>('/agent/semantic-layer/index/rebuild', {
    method: 'POST',
    body: JSON.stringify({ scope }),
  });

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
