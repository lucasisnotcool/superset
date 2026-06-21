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

export interface AgentQueryRequest {
  question: string;
  database_id: number;
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
  execution_result?: {
    columns: string[];
    rows: Record<string, unknown>[];
    row_count: number;
  } | null;
  trace: AgentTraceEvent[];
}

export interface ConversationScope {
  database_id: number;
  schema_name?: string | null;
  dataset_ids: number[];
  query_editor_id?: string | null;
  current_sql?: string | null;
  selected_text?: string | null;
}

export interface ConversationArtifact {
  type: 'sql';
  sql: string;
  explanation?: string | null;
  validation?: SqlValidationResult | null;
  execution_result?: {
    columns: string[];
    rows: Record<string, unknown>[];
    row_count: number;
  } | null;
  trace: AgentTraceEvent[];
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

const trimTrailingSlash = (url: string) => url.replace(/\/+$/, '');

export const getAgentBaseUrl = () =>
  trimTrailingSlash(process.env.SUPERSET_AI_AGENT_URL || '/ai-agent');

const requestJson = async <T>(path: string, init?: RequestInit): Promise<T> => {
  const response = await fetch(`${getAgentBaseUrl()}${path}`, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...(init?.headers || {}),
    },
  });

  if (!response.ok) {
    const detail = await response.text();
    throw new Error(detail || `Agent API request failed: ${response.status}`);
  }

  return response.json() as Promise<T>;
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
