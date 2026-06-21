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
  render,
  screen,
  userEvent,
  waitFor,
} from 'spec/helpers/testing-library';
import { initialState } from 'src/SqlLab/fixtures';
import AiAgentPanel from '.';

const originalAgentUrl = process.env.SUPERSET_AI_AGENT_URL;

beforeEach(() => {
  process.env.SUPERSET_AI_AGENT_URL = 'http://agent.local/';
  fetchMock.get('http://agent.local/health', {
    status: 'ok',
    model_provider: 'ollama',
    base_url: 'http://localhost:11434',
    default_model: 'qwen2.5-coder:7b',
    reachable: true,
  });
  fetchMock.get('http://agent.local/agent/conversations', []);
});

afterEach(() => {
  process.env.SUPERSET_AI_AGENT_URL = originalAgentUrl;
  fetchMock.clearHistory().removeRoutes();
});

test('sends a conversation message and renders SQL artifact', async () => {
  const conversation = {
    id: 'conversation-1',
    title: 'Show top names',
    owner_id: 'local',
    scope: {
      database_id: 1,
      schema_name: null,
      dataset_ids: [],
      query_editor_id: 'dfsadfs',
      current_sql: 'SELECT * FROM main.table',
      selected_text: null,
    },
    messages: [],
    created_at: '2026-06-19T00:00:00Z',
    updated_at: '2026-06-19T00:00:00Z',
  };
  const completedConversation = {
    ...conversation,
    messages: [
      {
        id: 'message-1',
        role: 'user',
        content: 'Show top names',
        created_at: '2026-06-19T00:00:00Z',
        artifacts: [],
      },
      {
        id: 'message-2',
        role: 'assistant',
        content: 'I drafted SQL.',
        created_at: '2026-06-19T00:00:00Z',
        artifacts: [
          {
            type: 'sql',
            sql: 'SELECT name FROM birth_names LIMIT 10',
            explanation: 'Returns names.',
            validation: {
              is_valid: true,
              is_read_only: true,
              normalized_sql: 'SELECT name FROM birth_names LIMIT 10',
              dialect: 'sqlite',
              errors: [],
            },
            execution_result: null,
            trace: [],
          },
        ],
      },
    ],
  };
  const executedConversation = {
    ...conversation,
    messages: [
      ...completedConversation.messages,
      {
        id: 'message-3',
        role: 'user',
        content: 'Execute selected SQL.',
        created_at: '2026-06-19T00:00:00Z',
        artifacts: [],
      },
      {
        id: 'message-4',
        role: 'assistant',
        content: 'The query returned one row.',
        created_at: '2026-06-19T00:00:00Z',
        artifacts: [
          {
            ...completedConversation.messages[1].artifacts[0],
            execution_result: {
              columns: ['name'],
              rows: [{ name: 'Michael' }],
              row_count: 1,
            },
          },
        ],
      },
    ],
  };
  fetchMock.post('http://agent.local/agent/conversations', conversation);
  fetchMock.post(
    'http://agent.local/agent/conversations/conversation-1/messages',
    {
      status: 'needs_review',
      conversation_id: 'conversation-1',
      message: completedConversation.messages[1],
      artifacts: completedConversation.messages[1].artifacts,
      trace: [],
      conversation: completedConversation,
    },
  );
  fetchMock.post(
    'http://agent.local/agent/conversations/conversation-1/execute-sql',
    {
      status: 'ok',
      conversation_id: 'conversation-1',
      message: executedConversation.messages[3],
      artifacts: executedConversation.messages[3].artifacts,
      trace: [],
      conversation: executedConversation,
    },
  );

  render(<AiAgentPanel />, {
    useRedux: true,
    initialState,
  });

  await userEvent.type(
    screen.getByPlaceholderText('Ask about this database'),
    'Show top names',
  );
  await userEvent.click(screen.getByRole('button', { name: 'Send' }));

  await waitFor(() => {
    expect(screen.getByText('I drafted SQL.')).toBeInTheDocument();
  });
  expect(
    screen.getByText('SELECT name FROM birth_names LIMIT 10'),
  ).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Insert' })).toBeInTheDocument();
  expect(
    screen.queryByRole('button', { name: 'Validate' }),
  ).not.toBeInTheDocument();
  const [messageCall] = fetchMock.callHistory.calls(
    'http://agent.local/agent/conversations/conversation-1/messages',
  );
  expect(JSON.parse(String(messageCall.options.body))).toMatchObject({
    message: 'Show top names',
    execution_mode: 'manual',
  });

  await userEvent.click(screen.getByRole('button', { name: 'Execute' }));

  await waitFor(() => {
    expect(screen.getByText('The query returned one row.')).toBeInTheDocument();
  });
  expect(screen.getByText('Michael')).toBeInTheDocument();
  const [executeCall] = fetchMock.callHistory.calls(
    'http://agent.local/agent/conversations/conversation-1/execute-sql',
  );
  expect(JSON.parse(String(executeCall.options.body))).toMatchObject({
    sql: 'SELECT name FROM birth_names LIMIT 10',
    execution_mode: 'manual',
  });
});
