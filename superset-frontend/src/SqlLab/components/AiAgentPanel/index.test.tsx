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
  fireEvent,
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
  (store.getState() as unknown as { sqlLab: SqlLabRootState['sqlLab'] }).sqlLab;

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
  window.localStorage.clear();
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
  // The panel attempts SSE streaming first and falls back to the buffered
  // endpoint when streaming is unavailable; 404 keeps this test on the
  // buffered contract it asserts below.
  fetchMock.post(
    'http://agent.local/agent/conversations/conversation-1/messages/stream',
    404,
  );
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
  // The button signals execution via its label/icon but stays enabled rather
  // than being permanently disabled after a successful run.
  const executedButton = screen.getByRole('button', { name: 'Executed' });
  expect(executedButton).toBeInTheDocument();
  expect(executedButton).toBeEnabled();
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
      {
        id: expectedId,
        databaseId: 1,
        catalogName: 'prod',
        schemaName: 'main',
      },
    ]);
  });
  expect(getSqlLabState(store).activeSemanticLayerEditorId).toEqual(expectedId);
});

const deferred = <T,>() => {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>(innerResolve => {
    resolve = innerResolve;
  });
  return { promise, resolve };
};

test('shows the user message immediately and streams agent progress', async () => {
  const conversation = {
    id: 'conversation-1',
    title: 'Stream',
    owner_id: 'local',
    scope: {
      database_id: 1,
      schema_name: null,
      dataset_ids: [],
      query_editor_id: null,
      current_sql: null,
      selected_text: null,
    },
    messages: [],
    created_at: '2026-06-19T00:00:00Z',
    updated_at: '2026-06-19T00:00:00Z',
  };
  const assistantMessage = {
    id: 'message-2',
    role: 'assistant',
    // Assistant content is routed through SafeMarkdown for rendering; markdown
    // parsing itself is covered by the SafeMarkdown suite (react-markdown is
    // mocked here to render its source verbatim).
    content: 'Streamed answer.',
    created_at: '2026-06-19T00:00:00Z',
    artifacts: [],
  };
  const finalConversation = {
    ...conversation,
    messages: [
      {
        id: 'message-1',
        role: 'user',
        content: 'Stream question',
        created_at: '2026-06-19T00:00:00Z',
        artifacts: [],
      },
      assistantMessage,
    ],
  };
  const sse =
    'event: progress\n' +
    'data: {"type":"progress","step":"draft_response","status":"ok",' +
    '"summary":"Drafting SQL…"}\n\n' +
    'event: complete\n' +
    `data: ${JSON.stringify({
      type: 'complete',
      response: {
        status: 'ok',
        conversation_id: 'conversation-1',
        message: assistantMessage,
        artifacts: [],
        trace: [],
        conversation: finalConversation,
      },
    })}\n\n`;
  const gate = deferred<void>();

  fetchMock.post('http://agent.local/agent/conversations', conversation);
  fetchMock.post(
    'http://agent.local/agent/conversations/conversation-1/messages/stream',
    () =>
      gate.promise.then(() => ({
        status: 200,
        body: sse,
        headers: { 'Content-Type': 'text/event-stream' },
      })),
  );

  render(<AiAgentPanel />, { useRedux: true, initialState });

  await userEvent.type(
    screen.getByPlaceholderText('Ask about this database'),
    'Stream question',
  );
  await userEvent.click(screen.getByRole('button', { name: 'Send' }));

  // The user message is shown immediately, alongside a progress indicator,
  // while the stream is still in flight.
  expect(await screen.findByText('Stream question')).toBeInTheDocument();
  expect(screen.getByTestId('agent-progress')).toBeInTheDocument();

  gate.resolve();

  // Once the stream completes, the assistant turn renders and the progress
  // indicator is removed.
  expect(await screen.findByText('Streamed answer.')).toBeInTheDocument();
  await waitFor(() => {
    expect(screen.queryByTestId('agent-progress')).not.toBeInTheDocument();
  });
  // The execution mode is persisted onto the created conversation.
  expect(
    window.localStorage.getItem('sqllab:ai-agent:exec-mode:conversation-1'),
  ).toBe('manual');
});

test('restores the persisted default execution mode on mount', async () => {
  window.localStorage.setItem('sqllab:ai-agent:exec-mode:default', 'read_only');
  render(<AiAgentPanel />, { useRedux: true, initialState });
  // The composer reflects the stored default rather than the hard-coded one.
  expect(await screen.findByText('Read-only queries')).toBeInTheDocument();
});

test('shows a jump-to-latest control when scrolled up', async () => {
  render(<AiAgentPanel />, { useRedux: true, initialState });
  const transcript = await screen.findByTestId('agent-transcript');
  Object.defineProperty(transcript, 'scrollHeight', {
    value: 1000,
    configurable: true,
  });
  Object.defineProperty(transcript, 'clientHeight', {
    value: 200,
    configurable: true,
  });
  transcript.scrollTop = 0;

  expect(screen.queryByTestId('jump-to-latest')).not.toBeInTheDocument();
  fireEvent.scroll(transcript);
  expect(screen.getByTestId('jump-to-latest')).toBeInTheDocument();

  fireEvent.click(screen.getByTestId('jump-to-latest'));
  await waitFor(() => {
    expect(screen.queryByTestId('jump-to-latest')).not.toBeInTheDocument();
  });
});

test('stops generation and reconciles without surfacing an error', async () => {
  const conversation = {
    id: 'conversation-1',
    title: 'Cancel',
    owner_id: 'local',
    scope: {
      database_id: 1,
      schema_name: null,
      dataset_ids: [],
      query_editor_id: null,
      current_sql: null,
      selected_text: null,
    },
    messages: [],
    created_at: '2026-06-19T00:00:00Z',
    updated_at: '2026-06-19T00:00:00Z',
  };
  const cancelledConversation = {
    ...conversation,
    messages: [
      {
        id: 'message-1',
        role: 'user',
        content: 'Long running question',
        created_at: '2026-06-19T00:00:00Z',
        artifacts: [],
      },
      {
        id: 'message-2',
        role: 'assistant',
        content: 'Generation cancelled.',
        created_at: '2026-06-19T00:00:00Z',
        artifacts: [],
      },
    ],
  };
  fetchMock.post('http://agent.local/agent/conversations', conversation);
  // The stream stays open until the request is aborted, then rejects with an
  // AbortError exactly like a real fetch would.
  fetchMock.post(
    'http://agent.local/agent/conversations/conversation-1/messages/stream',
    (_url: string, options: RequestInit) =>
      new Promise((_resolve, reject) => {
        const signal = options?.signal as AbortSignal | undefined;
        signal?.addEventListener('abort', () =>
          reject(new DOMException('Aborted', 'AbortError')),
        );
      }),
  );
  fetchMock.get(
    'http://agent.local/agent/conversations/conversation-1',
    cancelledConversation,
  );

  render(<AiAgentPanel />, { useRedux: true, initialState });
  await userEvent.type(
    screen.getByPlaceholderText('Ask about this database'),
    'Long running question',
  );
  await userEvent.click(screen.getByRole('button', { name: 'Send' }));

  // While in flight the Stop control replaces Send.
  const stopButton = await screen.findByRole('button', { name: 'Stop' });
  await userEvent.click(stopButton);

  // The transcript reconciles to the server state and no error is shown.
  await waitFor(() => {
    expect(screen.getByText('Generation cancelled.')).toBeInTheDocument();
  });
  expect(screen.getByRole('button', { name: 'Send' })).toBeInTheDocument();
  expect(screen.queryByTestId('agent-progress')).not.toBeInTheDocument();
});

test('keeps the retry SQL execute button enabled after an earlier SQL failed', async () => {
  // Regression for the cumulative-trace bug: a retry artifact inherits the
  // failed execute_sql event from the earlier attempt. The error must be
  // attributed by SQL so the never-executed retry stays runnable.
  const conversation = {
    id: 'conversation-1',
    title: 'Retry',
    owner_id: 'local',
    scope: {
      database_id: 1,
      schema_name: null,
      dataset_ids: [],
      query_editor_id: null,
      current_sql: null,
      selected_text: null,
    },
    messages: [],
    created_at: '2026-06-19T00:00:00Z',
    updated_at: '2026-06-19T00:00:00Z',
  };
  const failedSql = 'SELECT * FROM missing_table';
  const retrySql = 'SELECT name FROM birth_names LIMIT 10';
  const sharedErrorEvent = {
    step: 'execute_sql',
    status: 'error',
    summary: 'SQL execution failed.',
    details: { error: 'no such table: missing_table', sql: failedSql },
  };
  const makeArtifact = (id: string, sql: string) => ({
    id,
    type: 'sql',
    sql,
    explanation: null,
    validation: {
      is_valid: true,
      is_read_only: true,
      normalized_sql: sql,
      dialect: 'sqlite',
      errors: [],
    },
    execution_result: null,
    trace: [sharedErrorEvent],
  });
  const turnConversation = {
    ...conversation,
    messages: [
      {
        id: 'message-1',
        role: 'user',
        content: 'q',
        created_at: '2026-06-19T00:00:00Z',
        artifacts: [],
      },
      {
        id: 'message-2',
        role: 'assistant',
        content: 'I retried with a different query.',
        created_at: '2026-06-19T00:00:00Z',
        artifacts: [
          makeArtifact('art-failed', failedSql),
          makeArtifact('art-retry', retrySql),
        ],
      },
    ],
  };

  fetchMock.post('http://agent.local/agent/conversations', conversation);
  fetchMock.post(
    'http://agent.local/agent/conversations/conversation-1/messages/stream',
    404,
  );
  fetchMock.post(
    'http://agent.local/agent/conversations/conversation-1/messages',
    {
      status: 'needs_review',
      conversation_id: 'conversation-1',
      message: turnConversation.messages[1],
      artifacts: turnConversation.messages[1].artifacts,
      trace: [],
      conversation: turnConversation,
    },
  );

  render(<AiAgentPanel />, { useRedux: true, initialState });

  await userEvent.type(
    screen.getByPlaceholderText('Ask about this database'),
    'q',
  );
  await userEvent.click(screen.getByRole('button', { name: 'Send' }));

  await screen.findByText('I retried with a different query.');
  const executeButtons = screen.getAllByRole('button', { name: 'Execute' });
  expect(executeButtons).toHaveLength(2);
  // The failed artifact owns the error and stays disabled; the retry artifact
  // was never executed and remains runnable.
  expect(executeButtons[0]).toBeDisabled();
  expect(executeButtons[1]).toBeEnabled();
});

const buildTurn = () => {
  const conversation = {
    id: 'conversation-1',
    title: 'Top names',
    owner_id: 'local',
    scope: {
      database_id: 1,
      schema_name: null,
      dataset_ids: [],
      query_editor_id: null,
      current_sql: null,
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
        content: 'Here are the top names.',
        created_at: '2026-06-19T00:00:00Z',
        artifacts: [],
      },
    ],
  };
  return { conversation, completedConversation };
};

test('renders per-message affordances and regenerates the last user prompt', async () => {
  const { conversation, completedConversation } = buildTurn();
  fetchMock.post('http://agent.local/agent/conversations', conversation);
  fetchMock.post(
    'http://agent.local/agent/conversations/conversation-1/messages/stream',
    404,
  );
  fetchMock.post(
    'http://agent.local/agent/conversations/conversation-1/messages',
    {
      status: 'ok',
      conversation_id: 'conversation-1',
      message: completedConversation.messages[1],
      artifacts: [],
      trace: [],
      conversation: completedConversation,
    },
  );

  render(<AiAgentPanel />, { useRedux: true, initialState });
  await userEvent.type(
    screen.getByPlaceholderText('Ask about this database'),
    'Show top names',
  );
  await userEvent.click(screen.getByRole('button', { name: 'Send' }));

  await screen.findByText('Here are the top names.');
  // The active conversation title is shown as the panel heading.
  expect(screen.getByRole('heading', { level: 5 }).textContent).toContain(
    'Top names',
  );
  // Relative timestamp is rendered for the message.
  expect(screen.getAllByText(/ago/).length).toBeGreaterThan(0);
  // Assistant message exposes copy, feedback, and (as the last turn) regenerate.
  expect(screen.getByRole('button', { name: 'Good response' })).toBeTruthy();
  expect(screen.getByRole('button', { name: 'Bad response' })).toBeTruthy();
  expect(
    screen.getAllByRole('button', { name: 'Copy message' }).length,
  ).toBeGreaterThan(0);

  await userEvent.click(screen.getByRole('button', { name: 'Regenerate' }));
  await waitFor(() => {
    expect(
      fetchMock.callHistory.calls(
        'http://agent.local/agent/conversations/conversation-1/messages',
      ),
    ).toHaveLength(2);
  });
  const [, regenerateCall] = fetchMock.callHistory.calls(
    'http://agent.local/agent/conversations/conversation-1/messages',
  );
  expect(JSON.parse(String(regenerateCall.options.body))).toMatchObject({
    message: 'Show top names',
  });
});

test('reloads the conversation when the stream drops mid-turn', async () => {
  const { conversation, completedConversation } = buildTurn();
  fetchMock.post('http://agent.local/agent/conversations', conversation);
  // The stream emits a progress event but never a terminal `complete` event,
  // simulating a dropped connection.
  fetchMock.post(
    'http://agent.local/agent/conversations/conversation-1/messages/stream',
    {
      status: 200,
      body:
        'event: progress\n' +
        'data: {"type":"progress","step":"draft_response","status":"ok",' +
        '"summary":"Drafting…"}\n\n',
      headers: { 'Content-Type': 'text/event-stream' },
    },
  );
  // The buffered fallback must not be used for a mid-stream drop; the panel
  // resyncs via GET instead.
  fetchMock.get(
    'http://agent.local/agent/conversations/conversation-1',
    completedConversation,
  );

  render(<AiAgentPanel />, { useRedux: true, initialState });
  await userEvent.type(
    screen.getByPlaceholderText('Ask about this database'),
    'Show top names',
  );
  await userEvent.click(screen.getByRole('button', { name: 'Send' }));

  // The reloaded transcript appears and no error alert is shown.
  await waitFor(() => {
    expect(screen.getByText('Here are the top names.')).toBeInTheDocument();
  });
  expect(
    fetchMock.callHistory.calls(
      'http://agent.local/agent/conversations/conversation-1/messages',
    ),
  ).toHaveLength(0);
  expect(screen.getByRole('button', { name: 'Send' })).toBeInTheDocument();
});

test('renames the active conversation via the header title', async () => {
  const { conversation, completedConversation } = buildTurn();
  fetchMock.post('http://agent.local/agent/conversations', conversation);
  fetchMock.post(
    'http://agent.local/agent/conversations/conversation-1/messages/stream',
    404,
  );
  fetchMock.post(
    'http://agent.local/agent/conversations/conversation-1/messages',
    {
      status: 'ok',
      conversation_id: 'conversation-1',
      message: completedConversation.messages[1],
      artifacts: [],
      trace: [],
      conversation: completedConversation,
    },
  );
  fetchMock.patch('http://agent.local/agent/conversations/conversation-1', {
    ...completedConversation,
    title: 'Renamed chat',
  });

  render(<AiAgentPanel />, { useRedux: true, initialState });
  await userEvent.type(
    screen.getByPlaceholderText('Ask about this database'),
    'Show top names',
  );
  await userEvent.click(screen.getByRole('button', { name: 'Send' }));
  await screen.findByText('Here are the top names.');

  // antd Typography renders an inline "Edit" affordance for editable titles.
  await userEvent.click(screen.getByRole('button', { name: /edit/i }));
  // The edit field is seeded with the current title; pick the visible textbox
  // holding it (role queries exclude the aria-hidden autosize mirror, and the
  // composer textarea is empty after sending).
  const titleInput = screen
    .getAllByRole('textbox')
    .find(
      el => (el as HTMLTextAreaElement).value === 'Top names',
    ) as HTMLElement;
  await userEvent.clear(titleInput);
  await userEvent.type(titleInput, 'Renamed chat{enter}');

  await waitFor(() => {
    const [patchCall] = fetchMock.callHistory.calls(
      'http://agent.local/agent/conversations/conversation-1',
    );
    expect(patchCall).toBeTruthy();
    expect(JSON.parse(String(patchCall.options.body))).toMatchObject({
      title: 'Renamed chat',
    });
  });
});
