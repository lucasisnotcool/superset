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
import AuthoringPanel from './AuthoringPanel';

const originalAgentUrl = process.env.SUPERSET_AI_AGENT_URL;

const BASE =
  'http://agent.local/agent/semantic-layer/projects/proj-1/benchmarks';

const BENCHMARK = {
  id: 'bm-1',
  project_id: 'proj-1',
  name: 'Core',
  description: null,
  item_count: 0,
  created_at: '2026-07-01T00:00:00Z',
  updated_at: '2026-07-01T00:00:00Z',
};

const STEP = {
  kind: 'authoring_sql_probe',
  status: 'ok',
  summary: 'gold SQL verified (3 rows)',
  started_at: '2026-07-01T00:00:00Z',
  attempt_index: 0,
};

const DRAFT = {
  items: [
    {
      question: 'Who buys most?',
      answer_type: 'gold_sql',
      answer_spec: { sql: 'SELECT buyer FROM orders' },
      capability_tags: ['aggregation'],
      origin: 'extracted',
      validation: 'verified',
      problems: [],
    },
    {
      question: 'Broken one?',
      answer_type: 'gold_sql',
      answer_spec: { sql: 'SELECT nope' },
      capability_tags: [],
      origin: 'extracted',
      validation: 'needs_review',
      problems: ['gold SQL failed: ORA-00942'],
    },
  ],
  context_doc: '# Business context\n\nBuyers are companies.',
  warnings: [],
  steps_taken: 2,
  model_failed: false,
};

const sseBody = [
  `data: ${JSON.stringify({ type: 'progress', agent_step: STEP })}`,
  '',
  `data: ${JSON.stringify({ type: 'complete', draft: DRAFT })}`,
  '',
  '',
].join('\n');

beforeEach(() => {
  process.env.SUPERSET_AI_AGENT_URL = 'http://agent.local/';
  fetchMock.get(BASE, [BENCHMARK]);
});

afterEach(() => {
  process.env.SUPERSET_AI_AGENT_URL = originalAgentUrl;
  fetchMock.clearHistory().removeRoutes();
});

const renderPanel = (canWrite = true) =>
  render(<AuthoringPanel projectId="proj-1" canWrite={canWrite} />, {
    useRedux: true,
  });

const uploadCsv = async () => {
  const file = new File(
    ['question,gold_sql\nWho buys most?,\n'],
    'questions.csv',
    { type: 'text/csv' },
  );
  const input = screen.getByTestId('authoring-csv-input');
  await userEvent.upload(input, file);
};

test('read-only users see the write-access notice', async () => {
  renderPanel(false);
  expect(
    await screen.findByText(/write access to author/i),
  ).toBeInTheDocument();
});

test('the Choose buttons open their file inputs (ref-click wiring)', async () => {
  // Regression: a hidden input nested under a <label> with an antd <Button>
  // never opened the picker (the button swallowed the label activation).
  // Assert the visible button programmatically clicks its sibling input.
  renderPanel();
  await screen.findByTestId('authoring-run');
  const csvInput = screen.getByTestId('authoring-csv-input') as HTMLInputElement;
  const contextInput = screen.getByTestId(
    'authoring-context-input',
  ) as HTMLInputElement;
  const csvClick = jest.spyOn(csvInput, 'click');
  const contextClick = jest.spyOn(contextInput, 'click');

  await userEvent.click(screen.getByTestId('authoring-csv-choose'));
  await userEvent.click(screen.getByTestId('authoring-context-choose'));

  expect(csvClick).toHaveBeenCalledTimes(1);
  expect(contextClick).toHaveBeenCalledTimes(1);
});

test('author flow: upload, stream steps, review rows, import approved', async () => {
  fetchMock.post(`${BASE}/bm-1/author/stream`, {
    body: sseBody,
    headers: { 'Content-Type': 'text/event-stream' },
  });
  fetchMock.post(`${BASE}/bm-1/items/import`, {
    created: 1,
    skipped_duplicates: 0,
    errors: [],
  });

  renderPanel();
  await uploadCsv();
  const run = await screen.findByTestId('authoring-run');
  await waitFor(() => expect(run).toBeEnabled());
  await userEvent.click(run);

  // Streamed progress surfaced live (P6.1).
  expect(
    await screen.findByText('gold SQL verified (3 rows)'),
  ).toBeInTheDocument();

  // Draft rows render with validation/origin tags; verified pre-approved,
  // flagged one needs an explicit human tick (review gate).
  expect(await screen.findByText('Who buys most?')).toBeInTheDocument();
  expect(screen.getByText('needs_review')).toBeInTheDocument();
  expect(screen.getByText('gold SQL failed: ORA-00942')).toBeInTheDocument();
  const approveBoxes = screen.getAllByRole('checkbox');
  expect(approveBoxes).toHaveLength(2);
  expect(approveBoxes[0]).toBeChecked();
  expect(approveBoxes[1]).not.toBeChecked();

  await userEvent.click(screen.getByTestId('authoring-import'));
  await waitFor(() =>
    expect(
      fetchMock.callHistory.calls(`${BASE}/bm-1/items/import`),
    ).toHaveLength(1),
  );
  const [call] = fetchMock.callHistory.calls(`${BASE}/bm-1/items/import`);
  const payload = JSON.parse(String(call.options?.body));
  // Only the approved row went, stamped verified (import IS the approval).
  expect(payload.items).toHaveLength(1);
  expect(payload.items[0].question).toBe('Who buys most?');
  expect(payload.items[0].verified).toBe(true);
});

test('a streamed error event surfaces as a toast, not a crash', async () => {
  fetchMock.post(`${BASE}/bm-1/author/stream`, {
    body: `data: ${JSON.stringify({ type: 'error', detail: 'model down' })}\n\n`,
    headers: { 'Content-Type': 'text/event-stream' },
  });
  renderPanel();
  await uploadCsv();
  const run = await screen.findByTestId('authoring-run');
  await waitFor(() => expect(run).toBeEnabled());
  await userEvent.click(run);
  await waitFor(() =>
    expect(screen.queryByTestId('authoring-draft-rows')).not.toBeInTheDocument(),
  );
});
