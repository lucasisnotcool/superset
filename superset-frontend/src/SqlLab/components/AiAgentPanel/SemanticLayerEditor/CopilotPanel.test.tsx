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
import {
  render,
  screen,
  userEvent,
  waitFor,
} from 'spec/helpers/testing-library';
import CopilotPanel from './CopilotPanel';

const originalAgentUrl = process.env.SUPERSET_AI_AGENT_URL;
const originalFetch = global.fetch;

const changeset = {
  items: [
    {
      op: 'create',
      path: 'models/orders.json',
      proposed_content: '{"models":[{"name":"orders"}]}',
      current_content: null,
      validation: { valid: true, messages: [] },
      summary: 'Add orders model',
    },
  ],
  manifest_validation: { valid: true, messages: [] },
  warnings: [],
  steps: [{ kind: 'copilot_tool', status: 'ok', summary: 'write_mdl_file' }],
  message: 'Created the orders model.',
};

const SSE = [
  'event: progress\n' +
    'data: {"type":"progress","agent_step":{"kind":"copilot_tool",' +
    '"status":"ok","summary":"write_mdl_file"}}\n\n',
  `event: complete\ndata: ${JSON.stringify({
    type: 'complete',
    changeset,
  })}\n\n`,
].join('');

const streamResponse = (text: string) => {
  const chunks = [new TextEncoder().encode(text)];
  let index = 0;
  return {
    ok: true,
    body: {
      getReader: () => ({
        read: () =>
          index < chunks.length
            ? Promise.resolve({ value: chunks[index++], done: false })
            : Promise.resolve({ value: undefined, done: true }),
      }),
    },
  } as unknown as Response;
};

const jsonResponse = (body: unknown) =>
  ({
    ok: true,
    json: () => Promise.resolve(body),
    text: () => Promise.resolve(JSON.stringify(body)),
  }) as unknown as Response;

const appliedFiles = [
  {
    id: 'file-1',
    project_id: 'project-1',
    path: 'models/orders.json',
    filename: 'orders.json',
    content: '{"models":[{"name":"orders"}]}',
    content_type: 'application/json',
    source_type: 'copilot',
    status: 'draft',
    checksum: 'c',
    created_at: '2026-06-19T00:00:00Z',
    updated_at: '2026-06-19T00:00:00Z',
  },
];

const inspector = {
  system_prompt: 'You are MDL Copilot.',
  skills: [{ name: 'generate-mdl', text: 'author MDL' }],
  tools: [{ name: 'write_mdl_file', description: 'write a file' }],
  instructions: [],
};

const conversation = (messages: unknown[] = []) => ({
  id: 'conv-1',
  title: messages.length ? 'model the orders table' : 'New chat',
  owner_id: 'local',
  kind: 'copilot',
  project_id: 'project-1',
  scope: { database_id: 1, dataset_ids: [] },
  messages,
  created_at: '2026-06-19T00:00:00Z',
  updated_at: '2026-06-19T00:00:00Z',
});

// The thread the backend returns after a turn: user + assistant carrying the
// changeset as a generic artifact (so a resumed thread re-renders the proposal).
const threadAfterTurn = conversation([
  {
    id: 'm-user',
    role: 'user',
    content: 'model the orders table',
    created_at: '2026-06-19T00:00:00Z',
    artifacts: [],
  },
  {
    id: 'm-assistant',
    role: 'assistant',
    content: 'Created the orders model.',
    created_at: '2026-06-19T00:00:01Z',
    artifacts: [
      { id: 'a-1', type: 'changeset', sql: null, payload: changeset },
    ],
  },
]);

const installFetch = (opts: { history?: unknown[]; thread?: unknown } = {}) => {
  const fetchMockFn = jest.fn(
    (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? 'GET';
      if (url.includes('/copilot/stream')) {
        return Promise.resolve(streamResponse(SSE));
      }
      if (url.includes('/copilot/apply')) {
        return Promise.resolve(jsonResponse(appliedFiles));
      }
      if (url.includes('/copilot/inspector')) {
        return Promise.resolve(jsonResponse(inspector));
      }
      // A single conversation: GET resume, PATCH rename, DELETE.
      if (/\/copilot\/conversations\/[^/]+$/.test(url)) {
        return Promise.resolve(
          jsonResponse(
            method === 'DELETE'
              ? { deleted: true }
              : (opts.thread ?? threadAfterTurn),
          ),
        );
      }
      if (url.endsWith('/copilot/conversations')) {
        return Promise.resolve(
          jsonResponse(
            method === 'POST' ? conversation() : (opts.history ?? []),
          ),
        );
      }
      return Promise.resolve(jsonResponse({}));
    },
  );
  global.fetch = fetchMockFn as unknown as typeof fetch;
  return fetchMockFn;
};

beforeEach(() => {
  process.env.SUPERSET_AI_AGENT_URL = 'http://agent.local/';
  localStorage.clear();
});

afterEach(() => {
  process.env.SUPERSET_AI_AGENT_URL = originalAgentUrl;
  global.fetch = originalFetch;
  localStorage.clear();
  jest.restoreAllMocks();
});

test('streams the copilot, shows a diff, and applies accepted changes', async () => {
  const fetchFn = installFetch();
  const onApplied = jest.fn();

  render(
    <CopilotPanel
      projectId="project-1"
      canWrite
      onApplied={onApplied}
      readinessStatus="ready"
      onOnboard={jest.fn()}
    />,
  );

  await userEvent.type(
    screen.getByTestId('copilot-input'),
    'model the orders table',
  );
  await userEvent.click(screen.getByTestId('copilot-send'));

  expect(
    await screen.findByText('Created the orders model.'),
  ).toBeInTheDocument();
  expect(screen.getByTestId('copilot-changeset')).toBeInTheDocument();
  expect(screen.getByText('models/orders.json')).toBeInTheDocument();

  await userEvent.click(screen.getByTestId('copilot-apply'));

  await waitFor(() => expect(onApplied).toHaveBeenCalledTimes(1));
  const applyCall = fetchFn.mock.calls.find(([url]) =>
    String(url).endsWith('/copilot/apply'),
  );
  expect(applyCall).toBeDefined();
  // The apply carries the active thread id so the backend records an apply turn.
  expect(JSON.parse(String((applyCall![1] as RequestInit).body))).toEqual({
    items: expect.any(Array),
    conversation_id: 'conv-1',
  });
});

test('rejecting a file excludes it from apply', async () => {
  installFetch();
  render(
    <CopilotPanel
      projectId="project-1"
      canWrite
      readinessStatus="ready"
      onOnboard={jest.fn()}
    />,
  );

  await userEvent.type(screen.getByTestId('copilot-input'), 'do it');
  await userEvent.click(screen.getByTestId('copilot-send'));
  await screen.findByTestId('copilot-changeset');

  await userEvent.click(screen.getByTestId('copilot-reject'));

  expect(screen.getByTestId('copilot-apply')).toBeDisabled();
});

test('disables the composer without write permission', async () => {
  installFetch();
  render(
    <CopilotPanel
      projectId="project-1"
      canWrite={false}
      readinessStatus="ready"
      onOnboard={jest.fn()}
    />,
  );

  // Await the mount-time history fetch so its state update is wrapped in act().
  expect(await screen.findByTestId('copilot-input')).toBeDisabled();
  expect(screen.getByTestId('copilot-send')).toBeDisabled();
});

test('opens the inspector drawer and loads agent context', async () => {
  installFetch();
  render(
    <CopilotPanel
      projectId="project-1"
      canWrite
      readinessStatus="ready"
      onOnboard={jest.fn()}
    />,
  );

  await userEvent.click(screen.getByTestId('copilot-inspector-toggle'));

  expect(await screen.findByText('You are MDL Copilot.')).toBeInTheDocument();
});

test('empty layer shows an Onboard call-to-action instead of the chat', async () => {
  installFetch();
  const onOnboard = jest.fn();
  render(
    <CopilotPanel
      projectId="project-1"
      canWrite
      readinessStatus="empty"
      onOnboard={onOnboard}
    />,
  );

  // Bootstrap view, not chat: no composer, no coverage/inspector affordances.
  expect(screen.getByTestId('copilot-not-ready')).toBeInTheDocument();
  expect(screen.queryByTestId('copilot-input')).not.toBeInTheDocument();
  expect(
    screen.queryByTestId('copilot-coverage-toggle'),
  ).not.toBeInTheDocument();
  expect(
    screen.queryByTestId('copilot-inspector-toggle'),
  ).not.toBeInTheDocument();

  await userEvent.click(screen.getByTestId('copilot-onboard'));
  expect(onOnboard).toHaveBeenCalledTimes(1);
});

test('indexing shows a progress view and no Onboard button', () => {
  installFetch();
  render(
    <CopilotPanel
      projectId="project-1"
      canWrite
      readinessStatus="indexing"
      onOnboard={jest.fn()}
    />,
  );

  expect(screen.getByTestId('copilot-not-ready')).toBeInTheDocument();
  expect(screen.queryByTestId('copilot-onboard')).not.toBeInTheDocument();
  expect(screen.queryByTestId('copilot-input')).not.toBeInTheDocument();
});

test('failed onboarding shows the error and a Retry button', async () => {
  installFetch();
  const onOnboard = jest.fn();
  render(
    <CopilotPanel
      projectId="project-1"
      canWrite
      readinessStatus="failed"
      readinessDetail="Access denied to schema sales."
      onOnboard={onOnboard}
    />,
  );

  expect(
    screen.getByText(/Access denied to schema sales\./),
  ).toBeInTheDocument();
  await userEvent.click(screen.getByTestId('copilot-onboard'));
  expect(onOnboard).toHaveBeenCalledTimes(1);
});

test('persists the turn to a thread and survives reload via the API', async () => {
  // First mount: send a turn (creates a thread, persisted server-side).
  const fetchFn = installFetch();
  const { unmount } = render(
    <CopilotPanel
      projectId="project-1"
      canWrite
      readinessStatus="ready"
      onOnboard={jest.fn()}
    />,
  );
  await userEvent.type(
    screen.getByTestId('copilot-input'),
    'model the orders table',
  );
  await userEvent.click(screen.getByTestId('copilot-send'));
  expect(
    await screen.findByText('Created the orders model.'),
  ).toBeInTheDocument();
  // The active thread id was stored so it can be resumed after a reload.
  expect(
    localStorage.getItem('sqllab:mdl-copilot:conversation:project-1'),
  ).toBe('conv-1');
  // A thread was created on the backend.
  expect(
    fetchFn.mock.calls.some(
      ([url, init]) =>
        String(url).endsWith('/copilot/conversations') &&
        (init as RequestInit | undefined)?.method === 'POST',
    ),
  ).toBe(true);
  unmount();

  // Simulated reload: a fresh mount resumes the stored thread from the API.
  installFetch();
  render(
    <CopilotPanel
      projectId="project-1"
      canWrite
      readinessStatus="ready"
      onOnboard={jest.fn()}
    />,
  );
  // The transcript is restored from the backend (not from local state).
  expect(
    await screen.findByText('Created the orders model.'),
  ).toBeInTheDocument();
});

test('"New chat" clears the transcript and forgets the active thread', async () => {
  installFetch();
  render(
    <CopilotPanel
      projectId="project-1"
      canWrite
      readinessStatus="ready"
      onOnboard={jest.fn()}
    />,
  );
  await userEvent.type(screen.getByTestId('copilot-input'), 'model orders');
  await userEvent.click(screen.getByTestId('copilot-send'));
  await screen.findByText('Created the orders model.');

  await userEvent.click(screen.getByTestId('copilot-new-chat'));

  expect(
    screen.queryByText('Created the orders model.'),
  ).not.toBeInTheDocument();
  expect(
    localStorage.getItem('sqllab:mdl-copilot:conversation:project-1'),
  ).toBeNull();
});

test('resumes a past thread and renders its changeset read-only', async () => {
  // History lists one prior thread; resuming it returns the persisted turn.
  installFetch({
    history: [
      {
        id: 'conv-1',
        title: 'model the orders table',
        owner_id: 'local',
        kind: 'copilot',
        project_id: 'project-1',
        database_id: 1,
        updated_at: '2026-06-19T00:00:00Z',
        last_message: 'Created the orders model.',
      },
    ],
  });
  render(
    <CopilotPanel
      projectId="project-1"
      canWrite
      readinessStatus="ready"
      onOnboard={jest.fn()}
    />,
  );

  await userEvent.click(screen.getByTestId('copilot-history-toggle'));
  await userEvent.click(await screen.findByTestId('copilot-history-item'));

  // The prior proposal re-renders, but read-only: no Apply/Accept affordances.
  expect(
    await screen.findByText('Created the orders model.'),
  ).toBeInTheDocument();
  expect(screen.getByTestId('copilot-changeset')).toBeInTheDocument();
  expect(screen.queryByTestId('copilot-apply')).not.toBeInTheDocument();
  expect(screen.queryByTestId('copilot-accept')).not.toBeInTheDocument();
});

test('a stale stored thread that 404s is forgotten, not surfaced as an error', async () => {
  // The active thread was deleted elsewhere: resume returns 404.
  const fetchMockFn = jest.fn(
    (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? 'GET';
      if (/\/copilot\/conversations\/[^/]+$/.test(url)) {
        return Promise.resolve({
          ok: false,
          status: 404,
          text: () => Promise.resolve(JSON.stringify({ detail: 'not found' })),
        } as unknown as Response);
      }
      if (url.endsWith('/copilot/conversations')) {
        return Promise.resolve(
          jsonResponse(method === 'POST' ? conversation() : []),
        );
      }
      return Promise.resolve(jsonResponse({}));
    },
  );
  global.fetch = fetchMockFn as unknown as typeof fetch;
  localStorage.setItem('sqllab:mdl-copilot:conversation:project-1', 'gone-1');

  render(
    <CopilotPanel
      projectId="project-1"
      canWrite
      readinessStatus="ready"
      onOnboard={jest.fn()}
    />,
  );

  // The empty-state prompt shows (fresh chat), and no error banner is rendered.
  expect(
    await screen.findByText(
      /Ask the agent to model a table, add a metric, or fix validation\./,
    ),
  ).toBeInTheDocument();
  await waitFor(() =>
    expect(
      localStorage.getItem('sqllab:mdl-copilot:conversation:project-1'),
    ).toBeNull(),
  );
  expect(screen.getByTestId('copilot-input')).toBeInTheDocument();
});
