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
}

export type SemanticProjectVisibility = 'private' | 'db_access' | 'custom';
export type SemanticProjectPermission = 'read' | 'write' | 'admin';
export type MdlFileStatus = 'draft' | 'active' | 'deleted';
export type MdlFileSourceType = 'uploaded_mdl' | 'manual' | 'enriched_markdown';

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

export const enrichProjectDocument = (projectId: string, documentId: string) =>
  requestJson<MdlEnrichmentProposal>(
    `/agent/semantic-layer/projects/${projectId}/documents/${documentId}/enrich`,
    { method: 'POST' },
  );

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
