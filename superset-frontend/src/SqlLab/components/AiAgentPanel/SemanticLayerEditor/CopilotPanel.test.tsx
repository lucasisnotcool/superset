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
  act,
  fireEvent,
  render,
  screen,
  userEvent,
  waitFor,
} from 'spec/helpers/testing-library';
import type { SemanticDocument } from '../api';
import CopilotPanel from './CopilotPanel';

// The shared ingestion hook is exercised in its own suite; here we stub it so the
// attach wiring (inline grounding + tree refresh) can be asserted without redux or
// real network. `mockIngest` resolves with the persisted documents.
const mockIngest = jest.fn();
jest.mock('../useDocumentIngestion', () => ({
  __esModule: true,
  default: () => ({ ingest: mockIngest, isIngesting: false }),
}));

// Override only the single-document getter the attach-status poll calls; the rest
// of `../api` (streaming, conversations) stays real and rides the fetch mock.
const mockGetSemanticDocument = jest.fn();
jest.mock('../api', () => ({
  ...jest.requireActual('../api'),
  getSemanticDocument: (...args: unknown[]) => mockGetSemanticDocument(...args),
}));

const ingestedDoc = (
  overrides: Partial<SemanticDocument> = {},
): SemanticDocument => ({
  id: 'doc-1',
  filename: 'spec.pdf',
  content_type: 'application/pdf',
  size_bytes: 10,
  status: 'extracted',
  scope: { database_id: 1, dataset_ids: [] },
  checksum: 'abc',
  storage_uri: 'mem://x',
  extracted_text: 'Quarterly revenue by region.',
  warnings: [],
  created_at: '',
  updated_at: '',
  ...overrides,
});

const attachFile = async (name = 'spec.pdf', type = 'application/pdf') => {
  const input = screen.getByTestId('copilot-attach-input') as HTMLInputElement;
  await userEvent.upload(input, new File(['bytes'], name, { type }));
};

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
  mockIngest.mockReset();
  mockGetSemanticDocument.mockReset();
});

afterEach(() => {
  jest.useRealTimers();
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

test('invalid changeset items are excluded from the default accepted set (P3)', async () => {
  // One valid create + one relationship-as-model that failed validation. The
  // invalid item must default to rejected so it is not applied; the Apply button
  // counts only the valid one.
  const mixed = {
    items: [
      {
        op: 'create',
        path: 'models/orders.json',
        proposed_content: '{"models":[{"name":"orders"}]}',
        current_content: null,
        validation: { valid: true, messages: [] },
        summary: 'Add orders model',
      },
      {
        op: 'create',
        path: 'models/orders_to_customers.json',
        proposed_content: '{"models":[{"name":"orders_to_customers"}]}',
        current_content: null,
        validation: {
          valid: false,
          messages: [
            {
              line: null,
              column: null,
              severity: 'error',
              message:
                'Model orders_to_customers has neither a physical mapping nor ' +
                'columns. If it represents a join, define it under ' +
                'relationships[] instead of models[].',
              code: 'model_missing_mapping_and_columns',
            },
          ],
        },
        summary: 'Add join',
      },
    ],
    manifest_validation: { valid: false, messages: [] },
    warnings: [],
    steps: [],
    message: 'Proposed 2 changes.',
  };
  const sse = `event: complete\ndata: ${JSON.stringify({
    type: 'complete',
    changeset: mixed,
  })}\n\n`;
  global.fetch = jest.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? 'GET';
    if (url.includes('/copilot/stream')) {
      return Promise.resolve(streamResponse(sse));
    }
    if (/\/copilot\/conversations\/[^/]+$/.test(url)) {
      return Promise.resolve(jsonResponse(threadAfterTurn));
    }
    if (url.endsWith('/copilot/conversations')) {
      return Promise.resolve(
        jsonResponse(method === 'POST' ? conversation() : []),
      );
    }
    return Promise.resolve(jsonResponse({}));
  }) as unknown as typeof fetch;

  render(
    <CopilotPanel
      projectId="project-1"
      canWrite
      readinessStatus="ready"
      onOnboard={jest.fn()}
    />,
  );
  await userEvent.type(
    screen.getByTestId('copilot-input'),
    'model orders and its join to customers',
  );
  await userEvent.click(screen.getByTestId('copilot-send'));

  // Only the valid item is accepted by default -> "Apply 1 accepted".
  expect(await screen.findByText('Apply 1 accepted')).toBeInTheDocument();
});

test('the standalone Upload control is gone (attach is the single ingress)', () => {
  installFetch();
  render(
    <CopilotPanel
      projectId="project-1"
      canWrite
      readinessStatus="ready"
      onOnboard={jest.fn()}
    />,
  );

  expect(screen.queryByTestId('copilot-upload')).not.toBeInTheDocument();
  // Attach remains, and is now the persist+ground entry point.
  expect(screen.getByTestId('copilot-attach')).toBeInTheDocument();
});

test('attaching persists via the pipeline and refreshes the workspace', async () => {
  installFetch();
  mockIngest.mockResolvedValue([
    { document: ingestedDoc(), deduplicated: false },
  ]);
  const onDocumentsChanged = jest.fn();
  render(
    <CopilotPanel
      projectId="project-1"
      canWrite
      readinessStatus="ready"
      onOnboard={jest.fn()}
      onDocumentsChanged={onDocumentsChanged}
    />,
  );

  await attachFile();

  // Ingestion ran with the chosen file, the editor was asked to refresh, and a
  // chip for the persisted document is staged for the next turn.
  await waitFor(() => expect(mockIngest).toHaveBeenCalledTimes(1));
  expect(onDocumentsChanged).toHaveBeenCalledTimes(1);
  expect(await screen.findByText('spec.pdf')).toBeInTheDocument();
});

test('a still-extracting attachment shows a status hint on its chip', async () => {
  installFetch();
  mockIngest.mockResolvedValue([
    { document: ingestedDoc({ status: 'extracting' }), deduplicated: false },
  ]);
  render(
    <CopilotPanel
      projectId="project-1"
      canWrite
      readinessStatus="ready"
      onOnboard={jest.fn()}
    />,
  );

  await attachFile();

  expect(await screen.findByText(/spec\.pdf · Extracting/)).toBeInTheDocument();
});

test('attaching inlines the extracted text into the next turn', async () => {
  const fetchFn = installFetch();
  mockIngest.mockResolvedValue([
    { document: ingestedDoc(), deduplicated: false },
  ]);
  render(
    <CopilotPanel
      projectId="project-1"
      canWrite
      readinessStatus="ready"
      onOnboard={jest.fn()}
    />,
  );

  await attachFile();
  await screen.findByText('spec.pdf');
  await userEvent.type(screen.getByTestId('copilot-input'), 'summarize it');
  await userEvent.click(screen.getByTestId('copilot-send'));

  await waitFor(() => {
    const streamCall = fetchFn.mock.calls.find(([url]) =>
      String(url).includes('/copilot/stream'),
    );
    expect(streamCall).toBeDefined();
    const body = JSON.parse(String((streamCall![1] as RequestInit).body));
    expect(body.attachments).toEqual([
      expect.objectContaining({
        filename: 'spec.pdf',
        content_type: 'application/pdf',
        text: 'Quarterly revenue by region.',
        truncated: false,
      }),
    ]);
  });
});

// --- Live attach-status poll (gaps #2/#3) -----------------------------------
// These use fake timers + fireEvent for deterministic control of the 1500ms poll.

const attachViaInput = async (name = 'spec.pdf', type = 'application/pdf') => {
  await act(async () => {
    fireEvent.change(screen.getByTestId('copilot-attach-input'), {
      target: { files: [new File(['x'], name, { type })] },
    });
  });
};

const typeMessage = (value: string) =>
  act(() => {
    fireEvent.change(screen.getByTestId('copilot-input'), {
      target: { value },
    });
  });

test('a still-extracting attachment polls to extracted and ungated Send (R1/R2)', async () => {
  jest.useFakeTimers();
  installFetch();
  mockIngest.mockResolvedValue([
    {
      document: ingestedDoc({ status: 'extracting', extracted_text: null }),
      deduplicated: false,
    },
  ]);
  mockGetSemanticDocument.mockResolvedValue(
    ingestedDoc({ status: 'extracted', extracted_text: 'DONE' }),
  );
  render(
    <CopilotPanel
      projectId="project-1"
      canWrite
      readinessStatus="ready"
      onOnboard={jest.fn()}
    />,
  );

  await attachViaInput();
  await typeMessage('go');

  // Pending: chip shows the live status and Send is gated.
  expect(screen.getByText(/spec\.pdf · Extracting/)).toBeInTheDocument();
  expect(screen.getByTestId('copilot-send')).toBeDisabled();

  // One poll tick reconciles the doc to its terminal status.
  await act(async () => {
    await jest.advanceTimersByTimeAsync(1500);
  });

  expect(mockGetSemanticDocument).toHaveBeenCalledWith('doc-1');
  expect(screen.queryByText(/Extracting/)).not.toBeInTheDocument();
  expect(screen.getByTestId('copilot-send')).not.toBeDisabled();
});

test('a settled (extracted) attachment is never polled (loop guard)', async () => {
  jest.useFakeTimers();
  installFetch();
  mockIngest.mockResolvedValue([
    { document: ingestedDoc({ status: 'extracted' }), deduplicated: false },
  ]);
  render(
    <CopilotPanel
      projectId="project-1"
      canWrite
      readinessStatus="ready"
      onOnboard={jest.fn()}
    />,
  );

  await attachViaInput();
  await act(async () => {
    await jest.advanceTimersByTimeAsync(5000);
  });

  expect(mockGetSemanticDocument).not.toHaveBeenCalled();
  // A ready attachment never gates Send (needs a message though).
  await typeMessage('go');
  expect(screen.getByTestId('copilot-send')).not.toBeDisabled();
});

test('Send re-enables after the poll budget is exhausted while still extracting (R2 give-up)', async () => {
  jest.useFakeTimers();
  installFetch();
  mockIngest.mockResolvedValue([
    {
      document: ingestedDoc({ status: 'extracting', extracted_text: null }),
      deduplicated: false,
    },
  ]);
  // Never reaches a terminal status → the poll must give up and ungate Send.
  mockGetSemanticDocument.mockResolvedValue(
    ingestedDoc({ status: 'extracting', extracted_text: null }),
  );
  render(
    <CopilotPanel
      projectId="project-1"
      canWrite
      readinessStatus="ready"
      onOnboard={jest.fn()}
    />,
  );

  await attachViaInput();
  await typeMessage('go');
  expect(screen.getByTestId('copilot-send')).toBeDisabled();

  // Advance through the whole attempt budget (120 ticks @ 1500ms + margin).
  await act(async () => {
    await jest.advanceTimersByTimeAsync(1500 * 121);
  });

  // The gate has given up so the turn can proceed (R2)...
  expect(screen.getByTestId('copilot-send')).not.toBeDisabled();
  // ...and the give-up cue replaces the misleading perpetual "Extracting…" (G3):
  // the chip now reads "Still processing in the background" and a note explains
  // the file is still extracting and will be available to later turns.
  expect(screen.queryByText(/· Extracting/)).not.toBeInTheDocument();
  expect(
    screen.getByText(/spec\.pdf · Still processing in the background/),
  ).toBeInTheDocument();
  expect(screen.getByTestId('copilot-attach-giveup-note')).toBeInTheDocument();
});

test('after async extraction completes, Send inlines the fresh text (R3)', async () => {
  jest.useFakeTimers();
  const fetchFn = installFetch();
  mockIngest.mockResolvedValue([
    {
      document: ingestedDoc({ status: 'extracting', extracted_text: null }),
      deduplicated: false,
    },
  ]);
  mockGetSemanticDocument.mockResolvedValue(
    ingestedDoc({ status: 'extracted', extracted_text: 'FINAL TEXT' }),
  );
  render(
    <CopilotPanel
      projectId="project-1"
      canWrite
      readinessStatus="ready"
      onOnboard={jest.fn()}
    />,
  );

  await attachViaInput();
  await typeMessage('summarize');
  await act(async () => {
    await jest.advanceTimersByTimeAsync(1500);
  });
  expect(screen.getByTestId('copilot-send')).not.toBeDisabled();

  // Send under real timers so the streaming read + thread reload settle normally.
  jest.useRealTimers();
  fireEvent.click(screen.getByTestId('copilot-send'));

  await waitFor(() => {
    const streamCall = fetchFn.mock.calls.find(([url]) =>
      String(url).includes('/copilot/stream'),
    );
    expect(streamCall).toBeDefined();
    const body = JSON.parse(String((streamCall![1] as RequestInit).body));
    expect(body.attachments[0].text).toBe('FINAL TEXT');
  });
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
