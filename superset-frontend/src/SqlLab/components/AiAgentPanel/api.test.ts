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
import fetchMock from 'fetch-mock';
import {
  createConversation,
  createSemanticLayerEventSource,
  getSemanticLayerState,
  deleteConversation,
  executeConversationSql,
  getAgentBaseUrl,
  getAgentHealth,
  getConversation,
  listConversations,
  listSemanticDocuments,
  queryAgent,
  rebuildSemanticLayerIndex,
  reviewSemanticDocument,
  sendConversationMessage,
  uploadSemanticDocument,
  validateSql,
} from './api';

const originalAgentUrl = process.env.SUPERSET_AI_AGENT_URL;

beforeEach(() => {
  process.env.SUPERSET_AI_AGENT_URL = 'http://agent.local/';
});

afterEach(() => {
  process.env.SUPERSET_AI_AGENT_URL = originalAgentUrl;
  fetchMock.clearHistory().removeRoutes();
});

test('getAgentBaseUrl trims trailing slash', () => {
  expect(getAgentBaseUrl()).toBe('http://agent.local');
});

test('getAgentHealth requests health endpoint', async () => {
  fetchMock.get('http://agent.local/health', {
    status: 'ok',
    model_provider: 'ollama',
    base_url: 'http://localhost:11434',
    default_model: 'qwen2.5-coder:7b',
    reachable: true,
  });

  const response = await getAgentHealth();

  expect(response.reachable).toBe(true);
  expect(response.model_provider).toBe('ollama');
  expect(fetchMock.callHistory.calls('http://agent.local/health')).toHaveLength(
    1,
  );
});

test('queryAgent posts typed payload to agent backend', async () => {
  fetchMock.post('http://agent.local/agent/query', {
    status: 'needs_review',
    sql: 'select 1',
    explanation: 'Returns one row.',
    validation: {
      is_valid: true,
      is_read_only: true,
      normalized_sql: 'select 1',
      dialect: 'sqlite',
      errors: [],
    },
    trace: [],
  });

  const response = await queryAgent({
    question: 'show one',
    database_id: 1,
    schema_name: null,
    dataset_ids: [16],
    execute: false,
  });

  const [call] = fetchMock.callHistory.calls('http://agent.local/agent/query');
  expect(response.sql).toBe('select 1');
  expect(call.options.credentials).toBe('include');
  expect(JSON.parse(String(call.options.body))).toEqual({
    question: 'show one',
    database_id: 1,
    schema_name: null,
    dataset_ids: [16],
    execute: false,
  });
});

test('validateSql posts SQL validation payload', async () => {
  fetchMock.post('http://agent.local/agent/validate-sql', {
    is_valid: true,
    is_read_only: true,
    normalized_sql: 'select 1',
    dialect: 'sqlite',
    errors: [],
  });

  const response = await validateSql('select 1', 'sqlite');

  const [call] = fetchMock.callHistory.calls(
    'http://agent.local/agent/validate-sql',
  );
  expect(response.is_valid).toBe(true);
  expect(call.options.credentials).toBe('include');
  expect(JSON.parse(String(call.options.body))).toEqual({
    sql: 'select 1',
    dialect: 'sqlite',
  });
});

test('conversation API helpers use typed conversation endpoints', async () => {
  const conversation = {
    id: 'conversation-1',
    title: 'Show top names',
    owner_id: 'local',
    scope: {
      database_id: 1,
      schema_name: null,
      dataset_ids: [16],
      query_editor_id: 'editor-1',
      current_sql: 'select 1',
      selected_text: null,
    },
    messages: [],
    created_at: '2026-06-19T00:00:00Z',
    updated_at: '2026-06-19T00:00:00Z',
  };
  fetchMock.post('http://agent.local/agent/conversations', conversation);
  fetchMock.get('http://agent.local/agent/conversations', [
    {
      id: 'conversation-1',
      title: 'Show top names',
      owner_id: 'local',
      database_id: 1,
      schema_name: null,
      updated_at: '2026-06-19T00:00:00Z',
      last_message: null,
    },
  ]);
  fetchMock.get(
    'http://agent.local/agent/conversations/conversation-1',
    conversation,
  );
  fetchMock.post(
    'http://agent.local/agent/conversations/conversation-1/messages',
    {
      status: 'ok',
      conversation_id: 'conversation-1',
      message: {
        id: 'message-2',
        role: 'assistant',
        content: 'Answer',
        created_at: '2026-06-19T00:00:00Z',
        artifacts: [],
      },
      artifacts: [],
      trace: [],
      conversation,
    },
  );
  fetchMock.delete('http://agent.local/agent/conversations/conversation-1', {
    deleted: true,
  });
  fetchMock.post(
    'http://agent.local/agent/conversations/conversation-1/execute-sql',
    {
      status: 'ok',
      conversation_id: 'conversation-1',
      message: {
        id: 'message-3',
        role: 'assistant',
        content: 'Executed',
        created_at: '2026-06-19T00:00:00Z',
        artifacts: [],
      },
      artifacts: [],
      trace: [],
      conversation,
    },
  );

  const scope = {
    database_id: 1,
    schema_name: null,
    dataset_ids: [16],
    query_editor_id: 'editor-1',
    current_sql: 'select 1',
    selected_text: null,
  };

  expect((await createConversation(scope)).id).toBe('conversation-1');
  expect(await listConversations()).toHaveLength(1);
  expect((await getConversation('conversation-1')).title).toBe(
    'Show top names',
  );
  expect(
    (
      await sendConversationMessage('conversation-1', {
        message: 'What columns?',
        scope,
        execution_mode: 'manual',
      })
    ).message.content,
  ).toBe('Answer');
  expect(
    (
      await executeConversationSql('conversation-1', {
        sql: 'select 1',
        scope,
        execution_mode: 'manual',
        artifact_id: 'artifact-1',
      })
    ).message.content,
  ).toBe('Executed');
  expect((await deleteConversation('conversation-1')).deleted).toBe(true);

  const [messageCall] = fetchMock.callHistory.calls(
    'http://agent.local/agent/conversations/conversation-1/messages',
  );
  expect(messageCall.options.credentials).toBe('include');
  expect(JSON.parse(String(messageCall.options.body))).toEqual({
    message: 'What columns?',
    scope,
    execution_mode: 'manual',
  });
  const [executeCall] = fetchMock.callHistory.calls(
    'http://agent.local/agent/conversations/conversation-1/execute-sql',
  );
  expect(executeCall.options.credentials).toBe('include');
  expect(JSON.parse(String(executeCall.options.body))).toEqual({
    sql: 'select 1',
    scope,
    execution_mode: 'manual',
    artifact_id: 'artifact-1',
  });
});

test('semantic-layer API helpers use typed document endpoints', async () => {
  const scope = {
    database_id: 1,
    schema_name: null,
    dataset_ids: [16],
  };
  const document = {
    id: 'document-1',
    filename: 'notes.md',
    content_type: 'text/markdown',
    size_bytes: 12,
    status: 'needs_review',
    scope,
    checksum: 'abc',
    storage_uri: 'file:///tmp/notes.md',
    proposed_updates: [],
    warnings: [],
    created_at: '2026-06-19T00:00:00Z',
    updated_at: '2026-06-19T00:00:00Z',
  };
  fetchMock.post('http://agent.local/agent/semantic-layer/documents', document);
  fetchMock.get(
    'http://agent.local/agent/semantic-layer/documents?database_id=1&dataset_ids=16',
    [document],
  );
  fetchMock.patch(
    'http://agent.local/agent/semantic-layer/documents/document-1/review',
    { ...document, status: 'approved' },
  );
  fetchMock.post('http://agent.local/agent/semantic-layer/index/rebuild', {
    id: 'version-1',
    scope,
    scope_hash: 'hash',
    version: 'v1',
    status: 'idle',
    mdl: null,
    wren_context: null,
    source_update_ids: [],
    created_at: '2026-06-19T00:00:00Z',
  });
  fetchMock.get(
    'http://agent.local/agent/semantic-layer/state?database_id=1&dataset_ids=16',
    {
      database_id: 1,
      schema_name: null,
      dataset_ids: [16],
      document_count: 1,
      approved_document_count: 1,
      indexed_document_count: 0,
      semantic_layer_version: 'v1',
      indexing_status: 'idle',
      last_error: null,
    },
  );

  expect(
    (
      await uploadSemanticDocument(
        scope,
        new File(['notes'], 'notes.md', { type: 'text/markdown' }),
      )
    ).id,
  ).toBe('document-1');
  expect(await listSemanticDocuments(scope)).toHaveLength(1);
  expect(
    (
      await reviewSemanticDocument('document-1', {
        approved_update_ids: ['update-1'],
        rejected_update_ids: [],
        edited_updates: [],
      })
    ).status,
  ).toBe('approved');
  expect((await rebuildSemanticLayerIndex(scope)).version).toBe('v1');
  expect((await getSemanticLayerState(scope)).document_count).toBe(1);
  const [uploadCall] = fetchMock.callHistory.calls(
    'http://agent.local/agent/semantic-layer/documents',
  );
  expect(uploadCall.options.credentials).toBe('include');
});

test('API helpers surface FastAPI detail errors', async () => {
  fetchMock.get('http://agent.local/agent/conversations', {
    status: 401,
    body: { detail: 'Superset session expired.' },
  });

  await expect(listConversations()).rejects.toThrow(
    'Superset session expired.',
  );
});

test('semantic-layer event source includes Superset credentials', () => {
  const calls: Array<{ url: string; init?: EventSourceInit }> = [];
  const OriginalEventSource = globalThis.EventSource;

  class MockEventSource {
    static CONNECTING = 0;

    static OPEN = 1;

    static CLOSED = 2;

    onerror: ((this: EventSource, ev: Event) => unknown) | null = null;

    onmessage: ((this: EventSource, ev: MessageEvent) => unknown) | null = null;

    onopen: ((this: EventSource, ev: Event) => unknown) | null = null;

    readyState = MockEventSource.CONNECTING;

    url: string;

    withCredentials: boolean;

    constructor(url: string | URL, init?: EventSourceInit) {
      this.url = String(url);
      this.withCredentials = init?.withCredentials ?? false;
      calls.push({ url: this.url, init });
    }

    addEventListener() {}

    close() {}

    dispatchEvent() {
      return true;
    }

    removeEventListener() {}
  }

  Object.defineProperty(globalThis, 'EventSource', {
    configurable: true,
    value: MockEventSource as unknown as typeof EventSource,
  });
  try {
    createSemanticLayerEventSource({
      database_id: 1,
      schema_name: null,
      dataset_ids: [16],
    });
  } finally {
    Object.defineProperty(globalThis, 'EventSource', {
      configurable: true,
      value: OriginalEventSource,
    });
  }

  expect(calls).toEqual([
    {
      url: 'http://agent.local/agent/semantic-layer/events?database_id=1&dataset_ids=16',
      init: { withCredentials: true },
    },
  ]);
});
