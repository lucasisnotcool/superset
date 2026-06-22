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
import reducerIndex from 'spec/helpers/reducerIndex';
import {
  createStore,
  render,
  screen,
  userEvent,
  waitFor,
} from 'spec/helpers/testing-library';
import type { Store } from 'redux';
import { initialState } from 'src/SqlLab/fixtures';
import { buildSemanticLayerEditorId } from 'src/SqlLab/actions/sqlLab';
import type { SqlLabRootState } from 'src/SqlLab/types';
import AiAgentPanel from '.';

const getSqlLabState = (store: Store) =>
  (store.getState() as unknown as { sqlLab: SqlLabRootState['sqlLab'] })
    .sqlLab;

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
            id: 'artifact-1',
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
      {
        ...completedConversation.messages[0],
      },
      {
        ...completedConversation.messages[1],
        artifacts: [
          {
            ...completedConversation.messages[1].artifacts[0],
            execution_result: {
              columns: ['name', 'total_births'],
              rows: [{ name: 'Michael', total_births: 10 }],
              row_count: 1,
              audit: {
                adapter: 'rest',
                query_id: 123,
                results_key: 'result-key',
                executed_sql: 'SELECT name FROM birth_names LIMIT 10',
                database_id: 1,
                schema_name: null,
                row_limit: 1000,
                timeout_seconds: null,
                source: 'sqllab_rest',
              },
              is_truncated: false,
            },
            answer_summary: 'Michael is the top returned name.',
            insight_cards: [
              {
                title: 'Top name',
                value: 'Michael',
                metric: 'total_births',
                category: 'Michael',
                description: 'Michael leads the returned rows.',
                severity: 'success',
              },
            ],
            chart_spec: {
              type: 'bar',
              title: 'Births by name',
              encoding: { x: 'name', y: 'total_births' },
              options: {},
            },
            data_preview: null,
            audit: null,
            recommended_followups: ['Show by year'],
            wren_context: {
              enabled: true,
              available: true,
              matched_models: ['birth_names'],
              example_ids: [],
              document_ids: [],
              semantic_layer_version: null,
              indexing_status: null,
              context_items: [],
              dry_plan: null,
              warnings: [],
            },
            trace: [
              {
                step: 'approved_sql',
                status: 'ok',
                summary: 'Using approved SQL artifact for execution.',
                details: {},
              },
            ],
          },
        ],
      },
      {
        id: 'message-3',
        role: 'assistant',
        content: 'The query returned one row.',
        created_at: '2026-06-19T00:00:00Z',
        artifacts: [],
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
      message: executedConversation.messages[2],
      artifacts: executedConversation.messages[1].artifacts,
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
  expect(
    screen.getByText('Michael is the top returned name.'),
  ).toBeInTheDocument();
  expect(screen.getByText('Top name')).toBeInTheDocument();
  expect(screen.getByText('Births by name')).toBeInTheDocument();
  expect(screen.getByText('Data - 1 rows')).toBeInTheDocument();
  expect(screen.getAllByText('Michael').length).toBeGreaterThan(0);
  expect(screen.queryByText('Execute selected SQL.')).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Executed' })).toBeInTheDocument();
  const [executeCall] = fetchMock.callHistory.calls(
    'http://agent.local/agent/conversations/conversation-1/execute-sql',
  );
  expect(JSON.parse(String(executeCall.options.body))).toMatchObject({
    sql: 'SELECT name FROM birth_names LIMIT 10',
    execution_mode: 'manual',
    artifact_id: 'artifact-1',
  });

  await userEvent.click(screen.getByRole('button', { name: 'Show by year' }));
  await waitFor(() => {
    expect(
      fetchMock.callHistory.calls(
        'http://agent.local/agent/conversations/conversation-1/messages',
      ),
    ).toHaveLength(2);
  });
  const [, followupCall] = fetchMock.callHistory.calls(
    'http://agent.local/agent/conversations/conversation-1/messages',
  );
  expect(JSON.parse(String(followupCall.options.body))).toMatchObject({
    message: 'Show by year',
    execution_mode: 'manual',
  });
});

test('Semantic layer button opens a semantic-layer tab for the current scope', async () => {
  const scopedState = {
    ...initialState,
    sqlLab: {
      ...initialState.sqlLab,
      queryEditors: initialState.sqlLab.queryEditors.map(queryEditor => ({
        ...queryEditor,
        catalog: 'prod',
        schema: 'main',
      })),
    },
  };
  const project = {
    id: 'project-1',
    name: 'Database 1.prod.main',
    owner_id: 'local',
    database_uri_fingerprint: 'fingerprint',
    catalog_name: 'prod',
    schema_name: 'main',
    default_database_id: 1,
    visibility: 'db_access',
    status: 'active',
    permission: 'admin',
    created_at: '2026-06-19T00:00:00Z',
    updated_at: '2026-06-19T00:00:00Z',
  };
  fetchMock.post(
    'http://agent.local/agent/semantic-layer/projects/resolve',
    project,
  );
  fetchMock.get(
    'http://agent.local/agent/semantic-layer/projects/project-1/state',
    {
      project_id: 'project-1',
      database_id: 1,
      catalog_name: 'prod',
      schema_name: 'main',
      dataset_ids: [],
      document_count: 0,
      approved_document_count: 0,
      indexed_document_count: 0,
      semantic_layer_version: null,
      indexing_status: 'idle',
      last_error: null,
    },
  );

  const store = createStore(scopedState, reducerIndex);
  render(<AiAgentPanel />, { store });

  // The status badge fetches project state in the background on mount,
  // using create_if_missing: false since it's a passive read, not user
  // intent to create a project.
  await waitFor(() => {
    expect(
      fetchMock.callHistory.calls(
        'http://agent.local/agent/semantic-layer/projects/resolve',
      ),
    ).toHaveLength(1);
  });
  const [resolveCall] = fetchMock.callHistory.calls(
    'http://agent.local/agent/semantic-layer/projects/resolve',
  );
  expect(JSON.parse(String(resolveCall.options.body))).toMatchObject({
    create_if_missing: false,
  });

  await userEvent.click(screen.getByRole('button', { name: 'Semantic layer' }));

  const expectedId = buildSemanticLayerEditorId(1, 'prod', 'main');
  await waitFor(() => {
    expect(getSqlLabState(store).semanticLayerEditors).toEqual([
      { id: expectedId, databaseId: 1, catalogName: 'prod', schemaName: 'main' },
    ]);
  });
  expect(getSqlLabState(store).activeSemanticLayerEditorId).toEqual(
    expectedId,
  );
});
