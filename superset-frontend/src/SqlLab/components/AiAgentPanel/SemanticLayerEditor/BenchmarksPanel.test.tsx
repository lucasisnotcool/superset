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
  within,
} from 'spec/helpers/testing-library';
import BenchmarksPanel from './BenchmarksPanel';

const originalAgentUrl = process.env.SUPERSET_AI_AGENT_URL;

const BASE =
  'http://agent.local/agent/semantic-layer/projects/proj-1/benchmarks';

const BENCHMARK = {
  id: 'bm-1',
  project_id: 'proj-1',
  name: 'Core',
  description: null,
  item_count: 1,
  created_at: '2026-07-01T00:00:00Z',
  updated_at: '2026-07-01T00:00:00Z',
};

const ITEM = {
  id: 'item-1',
  benchmark_id: 'bm-1',
  position: 0,
  question: 'Revenue by region?',
  answer_type: 'gold_sql',
  answer_spec: { sql: 'SELECT region, revenue FROM sales' },
  capability_tags: ['metric'],
  use_as_example: false,
  verified_by: 'kim',
  verified_at: '2026-07-01T00:00:00Z',
};

const COMPLETE_RUN = {
  id: 'run-1',
  benchmark_id: 'bm-1',
  project_id: 'proj-1',
  status: 'complete',
  trials: 1,
  benchmark_checksum: 'abc',
  score: 0.5,
  totals: {
    items: 2,
    trials: 1,
    passed: 1,
    failed: 1,
    needs_review: 0,
    errors: 0,
    pass_hat_k: null,
  },
  progress: null,
  error: null,
  created_at: '2026-07-02T00:00:00Z',
};

const FAILED_RESULT = {
  id: 'res-1',
  run_id: 'run-1',
  item_id: 'item-1',
  trial_index: 0,
  question: 'Revenue by region?',
  answer_type: 'gold_sql',
  agent_sql: 'SELECT region, revenue FROM salez',
  agent_rows_preview: [{ region: 'emea', revenue: 999 }],
  gold_rows_preview: [{ region: 'emea', revenue: 10 }],
  verdict: 'fail',
  reasons: ['1 gold cell(s) unmatched across aligned columns.'],
  matched_models: ['sales'],
  duration_ms: 1200,
  override_verdict: null,
  override_by: null,
  override_comment: null,
  scores: [{ name: 'ex', value: 0, label: 'fail', source: 'code' }],
};

let itemsResponse: unknown[];
let runsResponse: unknown[];

beforeEach(() => {
  process.env.SUPERSET_AI_AGENT_URL = 'http://agent.local/';
  itemsResponse = [ITEM];
  runsResponse = [COMPLETE_RUN];
  fetchMock.get(BASE, [BENCHMARK]);
  fetchMock.get(`${BASE}/bm-1/items`, () => itemsResponse);
  fetchMock.get(`${BASE}/bm-1/runs`, () => runsResponse);
});

afterEach(() => {
  process.env.SUPERSET_AI_AGENT_URL = originalAgentUrl;
  fetchMock.clearHistory().removeRoutes();
});

const renderPanel = (canWrite = true) =>
  render(<BenchmarksPanel projectId="proj-1" canWrite={canWrite} />, {
    useRedux: true,
  });

test('re-activating the pane refetches benchmarks and items', async () => {
  // Regression: Tabs keep hidden panes mounted, so items imported from the
  // Authoring tab never appeared when toggling back to Benchmarks.
  itemsResponse = [];
  const { rerender } = render(
    <BenchmarksPanel projectId="proj-1" canWrite active />,
    { useRedux: true },
  );
  expect(await screen.findByText(/No test questions yet/)).toBeInTheDocument();

  // A sibling tab imports an item while this pane is hidden.
  itemsResponse = [ITEM];
  rerender(<BenchmarksPanel projectId="proj-1" canWrite active={false} />);
  rerender(<BenchmarksPanel projectId="proj-1" canWrite active />);

  expect(await screen.findByText('Revenue by region?')).toBeInTheDocument();
});

test('lists benchmark questions with type and verified badges', async () => {
  renderPanel();
  expect(await screen.findByText('Revenue by region?')).toBeInTheDocument();
  expect(screen.getByText('gold_sql')).toBeInTheDocument();
  expect(screen.getByText('Verified')).toBeInTheDocument();
  expect(screen.getByText('metric')).toBeInTheDocument();
});

test('lists run history with totals', async () => {
  renderPanel();
  expect(
    await screen.findByText(/1 passed \/ 1 failed \/ 0 review \/ 0 errors/),
  ).toBeInTheDocument();
});

test('read-only users see no mutating controls', async () => {
  renderPanel(false);
  await screen.findByText('Revenue by region?');
  expect(screen.queryByTestId('add-benchmark-item')).not.toBeInTheDocument();
  expect(screen.queryByTestId('run-benchmark')).not.toBeInTheDocument();
  expect(screen.queryByTestId('import-golden')).not.toBeInTheDocument();
});

test('adds a question through the dialog', async () => {
  fetchMock.post(`${BASE}/bm-1/items`, { ...ITEM, id: 'item-2' });
  renderPanel();
  await screen.findByText('Revenue by region?');

  await userEvent.click(screen.getByTestId('add-benchmark-item'));
  await userEvent.type(
    screen.getByTestId('item-question'),
    'How many drives shipped?',
  );
  await userEvent.type(
    screen.getByTestId('item-answer-spec'),
    'SELECT count(*) FROM shipments',
  );
  await userEvent.click(await screen.findByRole('button', { name: 'Add' }));

  await waitFor(() =>
    expect(
      fetchMock.callHistory.calls(`${BASE}/bm-1/items`, { method: 'POST' }),
    ).toHaveLength(1),
  );
  const [call] = fetchMock.callHistory.calls(`${BASE}/bm-1/items`, {
    method: 'POST',
  });
  expect(JSON.parse(String(call.options.body))).toMatchObject({
    question: 'How many drives shipped?',
    answer_type: 'gold_sql',
    answer_spec: { sql: 'SELECT count(*) FROM shipments' },
  });
});

test('starts a run and shows submitted toast flow', async () => {
  fetchMock.post(`${BASE}/bm-1/runs`, {
    run_id: 'run-2',
    status: 'pending',
    total_items: 1,
  });
  renderPanel();
  await screen.findByText('Revenue by region?');

  await userEvent.click(screen.getByTestId('run-benchmark'));
  await waitFor(() =>
    expect(
      fetchMock.callHistory.calls(`${BASE}/bm-1/runs`, { method: 'POST' }),
    ).toHaveLength(1),
  );
});

test('dry run shows the gold preview inline', async () => {
  fetchMock.post(`${BASE}/bm-1/items/item-1/dry-run`, {
    answer_type: 'gold_sql',
    columns: ['region', 'revenue'],
    rows: [{ region: 'emea', revenue: 10 }],
    row_count: 1,
    problems: [],
  });
  renderPanel();
  await screen.findByText('Revenue by region?');

  await userEvent.click(screen.getByRole('button', { name: 'Dry run' }));
  expect(await screen.findByTestId('dry-run-preview')).toHaveTextContent(
    /emea/,
  );
});

test('run details render verdicts, previews and reasons', async () => {
  fetchMock.get(`${BASE}/bm-1/runs/run-1/results`, [FAILED_RESULT]);
  renderPanel();
  await screen.findByText('Revenue by region?');

  await userEvent.click(screen.getByRole('button', { name: 'Details' }));
  expect(await screen.findByText('fail')).toBeInTheDocument();
  expect(
    screen.getByText(/1 gold cell\(s\) unmatched across aligned columns/),
  ).toBeInTheDocument();
  expect(screen.getByText(/999/)).toBeInTheDocument();
});

test('comparison banner marks within-noise deltas as not actionable', async () => {
  const otherRun = { ...COMPLETE_RUN, id: 'run-0', score: 0.45 };
  runsResponse = [COMPLETE_RUN, otherRun];
  fetchMock.get(`${BASE}/bm-1/runs/run-1/results`, [FAILED_RESULT]);
  fetchMock.get(`${BASE}/bm-1/runs/run-1/compare/run-0`, {
    run_id: 'run-1',
    other_run_id: 'run-0',
    delta: 0.05,
    ci_low: -0.02,
    ci_high: 0.12,
    significant: false,
    n_items: 20,
    improved: ['a'],
    regressed: [],
    unchanged: [],
    benchmark_changed: false,
  });
  renderPanel();
  await screen.findByText('Revenue by region?');

  await userEvent.click(
    (await screen.findAllByRole('button', { name: 'Details' }))[0],
  );
  const compare = await screen.findByTestId('compare-select');
  await userEvent.click(within(compare).getByRole('combobox'));
  const list = await waitFor(() => {
    const dropdown = document.querySelector('.rc-virtual-list');
    if (!dropdown) {
      throw new Error('dropdown not open');
    }
    return dropdown as HTMLElement;
  });
  await userEvent.click(within(list).getByText(/45%/));

  expect(await screen.findByTestId('comparison-banner')).toHaveTextContent(
    /within noise/,
  );
});

test('override buttons post the human verdict', async () => {
  fetchMock.get(`${BASE}/bm-1/runs/run-1/results`, [FAILED_RESULT]);
  fetchMock.get(`${BASE}/bm-1/runs/run-1`, COMPLETE_RUN);
  fetchMock.post(`${BASE}/bm-1/runs/run-1/results/res-1/override`, {
    ...FAILED_RESULT,
    override_verdict: 'pass',
    override_by: 'kim',
  });
  renderPanel();
  await screen.findByText('Revenue by region?');

  await userEvent.click(screen.getByRole('button', { name: 'Details' }));
  await userEvent.click(
    await screen.findByRole('button', { name: 'Mark pass' }),
  );
  await waitFor(() =>
    expect(
      fetchMock.callHistory.calls(
        `${BASE}/bm-1/runs/run-1/results/res-1/override`,
        { method: 'POST' },
      ),
    ).toHaveLength(1),
  );
});

test('imports golden queries into the benchmark', async () => {
  fetchMock.post(`${BASE}/bm-1/import-golden`, {
    created: 2,
    skipped_duplicates: 1,
  });
  renderPanel();
  await screen.findByText('Revenue by region?');

  await userEvent.click(screen.getByTestId('import-golden'));
  await waitFor(() =>
    expect(
      fetchMock.callHistory.calls(`${BASE}/bm-1/import-golden`, {
        method: 'POST',
      }),
    ).toHaveLength(1),
  );
});

test('shows empty state without questions', async () => {
  itemsResponse = [];
  runsResponse = [];
  renderPanel();
  expect(
    await screen.findByText(
      'No test questions yet. Add one or import golden queries.',
    ),
  ).toBeInTheDocument();
});

test('run rows show the capability breakdown tags', async () => {
  runsResponse = [
    {
      ...COMPLETE_RUN,
      totals: {
        ...COMPLETE_RUN.totals,
        by_capability: { metric: { items: 3, passed: 1 } },
      },
    },
  ];
  renderPanel();
  expect(await screen.findByText('metric 1/3')).toBeInTheDocument();
});

test('analyze failures renders the scientist report', async () => {
  fetchMock.get(`${BASE}/bm-1/runs/run-1/results`, [FAILED_RESULT]);
  fetchMock.post(`${BASE}/bm-1/runs/run-1/analyze`, {
    report: {
      summary: 'The join to regions is missing.',
      stats_note: 'No previous completed run to compare against.',
      within_noise: null,
      parse_degraded: false,
      findings: [
        {
          item_id: 'item-1',
          question: 'Revenue by region?',
          diagnosis: 'join_path',
          suggested_fix_type: 'Add or correct relationships.',
          suggested_action: 'Relate sales to regions on region_id.',
          test_suspect: false,
        },
      ],
    },
    conversation_id: 'conv-1',
  });
  renderPanel();
  await screen.findByText('Revenue by region?');

  await userEvent.click(screen.getByRole('button', { name: 'Details' }));
  await userEvent.click(await screen.findByTestId('analyze-run'));

  const report = await screen.findByTestId('analysis-report');
  expect(report).toHaveTextContent('The join to regions is missing.');
  expect(report).toHaveTextContent('join_path');
  expect(report).toHaveTextContent('Relate sales to regions on region_id.');
});

test('a run submits the single as-is config (no override knobs)', async () => {
  fetchMock.post(`${BASE}/bm-1/runs`, {
    run_id: 'run-2',
    status: 'pending',
    total_items: 1,
  });
  renderPanel();
  await screen.findByText('Revenue by region?');
  // The legacy sweep knobs are gone from the surface entirely.
  expect(screen.queryByTestId('model-override')).not.toBeInTheDocument();
  expect(screen.queryByTestId('exclude-exemplars')).not.toBeInTheDocument();
  await userEvent.click(screen.getByTestId('run-benchmark'));

  await waitFor(() =>
    expect(
      fetchMock.callHistory.calls(`${BASE}/bm-1/runs`, { method: 'POST' }),
    ).toHaveLength(1),
  );
  const [call] = fetchMock.callHistory.calls(`${BASE}/bm-1/runs`, {
    method: 'POST',
  });
  const body = JSON.parse(String(call.options.body));
  expect(body).toEqual({ trials: 1 });
});

test('propose MDL fixes renders the staged changeset', async () => {
  fetchMock.get(`${BASE}/bm-1/runs/run-1/results`, [FAILED_RESULT]);
  fetchMock.post(`${BASE}/bm-1/runs/run-1/handoff-copilot`, {
    changeset: {
      message: 'Added regions model.',
      items: [{ op: 'create', path: 'models/regions.json' }],
    },
    report: {
      summary: 'Join missing.',
      stats_note: null,
      within_noise: null,
      parse_degraded: false,
      findings: [],
    },
    conversation_id: 'conv-9',
    verification_hint: 'Re-run to verify.',
  });
  renderPanel();
  await screen.findByText('Revenue by region?');

  await userEvent.click(screen.getByRole('button', { name: 'Details' }));
  await userEvent.click(await screen.findByTestId('handoff-run'));

  const result = await screen.findByTestId('handoff-result');
  expect(result).toHaveTextContent(/1 staged MDL edit/);
  expect(result).toHaveTextContent('create models/regions.json');
});
