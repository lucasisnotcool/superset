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
  deleteConversation,
  executeConversationSql,
  getAgentBaseUrl,
  getAgentHealth,
  getConversation,
  listConversations,
  queryAgent,
  sendConversationMessage,
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
      })
    ).message.content,
  ).toBe('Executed');
  expect((await deleteConversation('conversation-1')).deleted).toBe(true);

  const [messageCall] = fetchMock.callHistory.calls(
    'http://agent.local/agent/conversations/conversation-1/messages',
  );
  expect(JSON.parse(String(messageCall.options.body))).toEqual({
    message: 'What columns?',
    scope,
    execution_mode: 'manual',
  });
  const [executeCall] = fetchMock.callHistory.calls(
    'http://agent.local/agent/conversations/conversation-1/execute-sql',
  );
  expect(JSON.parse(String(executeCall.options.body))).toEqual({
    sql: 'select 1',
    scope,
    execution_mode: 'manual',
  });
});
