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

const installFetch = () => {
  const fetchMockFn = jest.fn((input: RequestInfo | URL) => {
    const url = String(input);
    if (url.endsWith('/copilot/stream')) {
      return Promise.resolve(streamResponse(SSE));
    }
    if (url.endsWith('/copilot/apply')) {
      return Promise.resolve(jsonResponse(appliedFiles));
    }
    if (url.endsWith('/copilot/inspector')) {
      return Promise.resolve(jsonResponse(inspector));
    }
    return Promise.resolve(jsonResponse({}));
  });
  global.fetch = fetchMockFn as unknown as typeof fetch;
  return fetchMockFn;
};

beforeEach(() => {
  process.env.SUPERSET_AI_AGENT_URL = 'http://agent.local/';
});

afterEach(() => {
  process.env.SUPERSET_AI_AGENT_URL = originalAgentUrl;
  global.fetch = originalFetch;
  jest.restoreAllMocks();
});

test('streams the copilot, shows a diff, and applies accepted changes', async () => {
  const fetchFn = installFetch();
  const onApplied = jest.fn();

  render(<CopilotPanel projectId="project-1" canWrite onApplied={onApplied} />);

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
});

test('rejecting a file excludes it from apply', async () => {
  installFetch();
  render(<CopilotPanel projectId="project-1" canWrite />);

  await userEvent.type(screen.getByTestId('copilot-input'), 'do it');
  await userEvent.click(screen.getByTestId('copilot-send'));
  await screen.findByTestId('copilot-changeset');

  await userEvent.click(screen.getByTestId('copilot-reject'));

  expect(screen.getByTestId('copilot-apply')).toBeDisabled();
});

test('disables the composer without write permission', () => {
  installFetch();
  render(<CopilotPanel projectId="project-1" canWrite={false} />);

  expect(screen.getByTestId('copilot-input')).toBeDisabled();
  expect(screen.getByTestId('copilot-send')).toBeDisabled();
});

test('opens the inspector drawer and loads agent context', async () => {
  installFetch();
  render(<CopilotPanel projectId="project-1" canWrite />);

  await userEvent.click(screen.getByTestId('copilot-inspector-toggle'));

  expect(await screen.findByText('You are MDL Copilot.')).toBeInTheDocument();
});
