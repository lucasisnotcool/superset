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
import userEvent from '@testing-library/user-event';
import { render, screen } from 'spec/helpers/testing-library';
import ExplainDialog from './ExplainDialog';
import type { AgentStep } from './api';

const steps: AgentStep[] = [
  {
    kind: 'load_wren_context',
    status: 'ok',
    summary: 'Loaded Wren semantic context.',
    started_at: '2026-06-24T12:00:00Z',
    duration_ms: 120,
    attempt_index: 0,
    detail: {
      kind: 'wren_context',
      available: true,
      matched_models: ['orders'],
      retrieval_mode: 'embedding',
      retrieved_item_count: 3,
      context_item_count: 5,
      recalled_example_count: 2,
    },
  },
  {
    kind: 'plan_semantic_sql',
    status: 'ok',
    summary: 'Rewrote semantic SQL to native SQL.',
    started_at: '2026-06-24T12:00:01Z',
    duration_ms: 2000,
    attempt_index: 0,
    detail: {
      kind: 'plan_semantic_sql',
      engine: 'wren',
      rewritten: true,
      semantic_sql: 'SELECT a FROM orders',
      native_sql: 'SELECT a FROM sales.orders',
      referenced_tables: ['orders'],
      warnings: [],
    },
  },
  {
    kind: 'execute_sql',
    status: 'ok',
    summary: 'Executed SQL and returned 5 row(s).',
    started_at: '2026-06-24T12:00:03Z',
    attempt_index: 1,
    detail: {
      kind: 'execute',
      row_count: 5,
      adapter: 'rest',
      query_id: 99,
      is_duplicate: false,
    },
  },
];

const noop = () => {};

test('renders nothing when closed', () => {
  render(
    <ExplainDialog
      open={false}
      onClose={noop}
      userMessage="hi"
      steps={steps}
    />,
  );
  expect(screen.queryByTestId('explain-dialog')).not.toBeInTheDocument();
});

test('renders the user message and one box per step with friendly labels', () => {
  render(
    <ExplainDialog
      open
      onClose={noop}
      userMessage="Show top orders"
      steps={steps}
    />,
  );
  expect(screen.getByText('Show top orders')).toBeInTheDocument();
  expect(screen.getByText('Retrieved semantic context')).toBeInTheDocument();
  expect(screen.getByText('Rewrote semantic SQL')).toBeInTheDocument();
  expect(screen.getByText('Executed SQL')).toBeInTheDocument();
  expect(screen.getAllByTestId('explain-step')).toHaveLength(3);
});

test('surfaces typed detail: the semantic->native rewrite toggles', async () => {
  render(<ExplainDialog open onClose={noop} steps={steps} />);
  // Defaults to the executed (native) form; the authored (semantic) form is
  // revealed by the toggle (B4).
  expect(screen.getByText('SELECT a FROM sales.orders')).toBeInTheDocument();
  expect(screen.queryByText('SELECT a FROM orders')).not.toBeInTheDocument();
  await userEvent.click(screen.getByText('Semantic (authored)'));
  expect(screen.getByText('SELECT a FROM orders')).toBeInTheDocument();
  // Retrieval provenance is legible (matched models, retriever mode). "orders"
  // appears as both a matched model and a referenced table, hence getAllByText.
  expect(screen.getAllByText('orders').length).toBeGreaterThan(0);
  expect(screen.getByText('embedding')).toBeInTheDocument();
});

// A single plan_semantic_sql step carrying only the supplied rewrite fields, so
// the SqlRewrite fallback branches can be exercised in isolation.
const rewriteStep = (
  detail: Partial<{ semantic_sql: string | null; native_sql: string | null }>,
): AgentStep[] => [
  {
    kind: 'plan_semantic_sql',
    status: 'ok',
    summary: 'Rewrote semantic SQL to native SQL.',
    started_at: '2026-06-24T12:00:01Z',
    duration_ms: 10,
    attempt_index: 0,
    detail: {
      kind: 'plan_semantic_sql',
      engine: 'wren',
      rewritten: true,
      semantic_sql: null,
      native_sql: null,
      referenced_tables: [],
      warnings: [],
      ...detail,
    },
  },
];

test('rewrite with only the native form shows it without a toggle', () => {
  render(
    <ExplainDialog
      open
      onClose={noop}
      steps={rewriteStep({ native_sql: 'SELECT a FROM sales.orders' })}
    />,
  );
  expect(screen.getByText('Native SQL')).toBeInTheDocument();
  expect(screen.getByText('SELECT a FROM sales.orders')).toBeInTheDocument();
  // No toggle is offered when there is nothing to toggle between.
  expect(screen.queryByText('Semantic (authored)')).not.toBeInTheDocument();
});

test('rewrite with only the semantic form falls back to the authored block', () => {
  render(
    <ExplainDialog
      open
      onClose={noop}
      steps={rewriteStep({ semantic_sql: 'SELECT a FROM orders' })}
    />,
  );
  expect(screen.getByText('Semantic SQL')).toBeInTheDocument();
  expect(screen.getByText('SELECT a FROM orders')).toBeInTheDocument();
  expect(screen.queryByText('Native (executed)')).not.toBeInTheDocument();
});

test('groups retries into labeled attempts', () => {
  render(<ExplainDialog open onClose={noop} steps={steps} />);
  expect(screen.getByText('Attempt 1')).toBeInTheDocument();
  expect(screen.getByText('Attempt 2')).toBeInTheDocument();
});

test('an unknown step kind renders its summary without throwing', () => {
  const unknown: AgentStep[] = [
    {
      kind: 'some_future_node',
      status: 'ok',
      summary: 'A brand new step.',
      started_at: '2026-06-24T12:00:00Z',
      attempt_index: 0,
      detail: null,
    },
  ];
  render(<ExplainDialog open onClose={noop} steps={unknown} />);
  // Falls back to the raw kind label and shows the summary.
  expect(screen.getByText('some_future_node')).toBeInTheDocument();
  expect(screen.getByText('A brand new step.')).toBeInTheDocument();
});

test('shows an empty-state message when there is no timeline', () => {
  render(<ExplainDialog open onClose={noop} steps={[]} />);
  expect(
    screen.getByText('No timeline is available for this message yet.'),
  ).toBeInTheDocument();
});

test('invokes onClose from the modal close control', async () => {
  const onClose = jest.fn();
  render(<ExplainDialog open onClose={onClose} steps={steps} />);
  await userEvent.click(screen.getAllByLabelText('Close')[0]);
  expect(onClose).toHaveBeenCalled();
});

test('shows a turn summary and a copy-trace control', () => {
  render(<ExplainDialog open onClose={noop} steps={steps} />);
  // 3 steps across 2 attempts; total duration 120ms + 2000ms = 2.1s.
  expect(
    screen.getByText('3 steps · 2 attempt(s) · 2.1 s'),
  ).toBeInTheDocument();
  expect(
    screen.getByRole('button', { name: 'Copy trace as JSON' }),
  ).toBeInTheDocument();
});

test('a failed attempt leads with why it failed', () => {
  const retried: AgentStep[] = [
    {
      kind: 'draft_sql',
      status: 'ok',
      summary: 'Generated an initial SQL draft.',
      started_at: '2026-06-24T12:00:00Z',
      attempt_index: 0,
      detail: { kind: 'draft', recalled_example_count: 0 },
    },
    {
      kind: 'reflect_sql_outcome',
      status: 'warning',
      summary: 'Reflected on result.',
      started_at: '2026-06-24T12:00:01Z',
      attempt_index: 0,
      detail: {
        kind: 'reflect',
        outcome: 'retry',
        remaining_sql_iterations: 1,
        retry_feedback: 'Column patty_count does not exist',
      },
    },
    {
      kind: 'draft_sql',
      status: 'ok',
      summary: 'Re-drafted SQL.',
      started_at: '2026-06-24T12:00:02Z',
      attempt_index: 1,
      detail: { kind: 'draft', recalled_example_count: 0 },
    },
  ];
  render(<ExplainDialog open onClose={noop} steps={retried} />);
  // The failure reason leads the attempt (it also appears in the reflect detail).
  expect(screen.getByTestId('explain-attempt-outcome')).toHaveTextContent(
    'Column patty_count does not exist',
  );
});

test('long SQL collapses behind a disclosure', async () => {
  const longSql = `SELECT ${'col, '.repeat(60)}1 FROM grill_moves`;
  const execStep: AgentStep[] = [
    {
      kind: 'execute_sql',
      status: 'ok',
      summary: 'Executed SQL and returned 5 row(s).',
      started_at: '2026-06-24T12:00:00Z',
      attempt_index: 0,
      detail: {
        kind: 'execute',
        row_count: 5,
        executed_sql: longSql,
        is_duplicate: false,
      },
    },
  ];
  render(<ExplainDialog open onClose={noop} steps={execStep} />);
  // The SQL is hidden until the disclosure is opened.
  expect(screen.queryByText(longSql)).not.toBeInTheDocument();
  await userEvent.click(screen.getByText('Show SQL'));
  expect(screen.getByText(longSql)).toBeInTheDocument();
});

test('explains why no semantic layer was used', () => {
  const unavailable: AgentStep[] = [
    {
      kind: 'load_wren_context',
      status: 'warning',
      summary: 'Wren semantic context is unavailable.',
      started_at: '2026-06-24T12:00:00Z',
      attempt_index: 0,
      detail: {
        kind: 'wren_context',
        available: false,
        matched_models: [],
        retrieved_item_count: 0,
        context_item_count: 0,
        recalled_example_count: 0,
      },
    },
  ];
  render(<ExplainDialog open onClose={noop} steps={unavailable} />);
  expect(
    screen.getByText(
      'No semantic layer is active for this scope — answered from raw schema only.',
    ),
  ).toBeInTheDocument();
});

const chunkStep: AgentStep[] = [
  {
    kind: 'load_wren_context',
    status: 'ok',
    summary: 'Loaded Wren semantic context.',
    started_at: '2026-06-24T12:00:00Z',
    attempt_index: 0,
    detail: {
      kind: 'wren_context',
      available: true,
      matched_models: ['grill_moves'],
      retrieval_mode: 'embedding',
      retrieved_item_count: 1,
      context_item_count: 1,
      recalled_example_count: 0,
      retrieved_chunks: [
        {
          kind: 'column',
          name: 'patty_count',
          model: 'grill_moves',
          text: 'grill_moves.patty_count int — number of patties cooked',
          retriever: 'embedding',
          score: 0.87,
        },
      ],
      warnings: ['Semantic project has no active MDL files.'],
    },
  },
];

test('lists retrieved chunks under a collapsible header and surfaces warnings', async () => {
  render(<ExplainDialog open onClose={noop} steps={chunkStep} />);
  // Header reads even while collapsed; chunk text is revealed on expand.
  const header = screen.getByText('Retrieved chunks (1 · embedding)');
  expect(header).toBeInTheDocument();
  await userEvent.click(header);
  expect(
    screen.getByText('grill_moves.patty_count int — number of patties cooked'),
  ).toBeInTheDocument();
  expect(screen.getByText(/score 0\.87/)).toBeInTheDocument();
  // The unavailability/degradation reason is shown verbatim (B6).
  expect(
    screen.getByText('Semantic project has no active MDL files.'),
  ).toBeInTheDocument();
});

test('lists recalled examples under a collapsible header', async () => {
  const draft: AgentStep[] = [
    {
      kind: 'draft_sql',
      status: 'ok',
      summary: 'Generated an initial SQL draft.',
      started_at: '2026-06-24T12:00:00Z',
      attempt_index: 0,
      detail: {
        kind: 'draft',
        response_type: 'sql',
        model: 'gpt',
        recalled_example_count: 1,
        recalled_examples: [
          {
            question: "How many patties got 86'd?",
            native_sql: 'SELECT count(*) FROM grill_moves',
          },
        ],
      },
    },
  ];
  render(<ExplainDialog open onClose={noop} steps={draft} />);
  const header = screen.getByText('Recalled examples (1)');
  await userEvent.click(header);
  expect(screen.getByText("How many patties got 86'd?")).toBeInTheDocument();
  expect(
    screen.getByText('SELECT count(*) FROM grill_moves'),
  ).toBeInTheDocument();
});

test('groups chunks by model and badges the matched model', async () => {
  const grouped: AgentStep[] = [
    {
      kind: 'load_wren_context',
      status: 'ok',
      summary: 'Loaded Wren semantic context.',
      started_at: '2026-06-24T12:00:00Z',
      attempt_index: 0,
      detail: {
        kind: 'wren_context',
        available: true,
        matched_models: ['grill_moves'],
        retrieval_mode: 'embedding',
        retrieved_item_count: 2,
        context_item_count: 2,
        recalled_example_count: 0,
        retrieved_chunks: [
          {
            kind: 'column',
            name: 'patty_count',
            model: 'grill_moves',
            text: 'grill_moves.patty_count int',
          },
          {
            kind: 'column',
            name: 'region',
            model: 'stores',
            text: 'stores.region text',
          },
        ],
      },
    },
  ];
  render(<ExplainDialog open onClose={noop} steps={grouped} />);
  await userEvent.click(screen.getByText('Retrieved chunks (2 · embedding)'));
  // Both model group headers render; only the matched one gets a badge.
  // ("grill_moves" also appears in the Matched models row, hence getAllByText.)
  expect(screen.getAllByText('grill_moves').length).toBeGreaterThan(0);
  expect(screen.getByText('stores')).toBeInTheDocument();
  expect(screen.getByText('matched')).toBeInTheDocument();
});

test('shows ranked dataset candidates as tags with scan counts', () => {
  const loadContext: AgentStep[] = [
    {
      kind: 'load_context',
      status: 'ok',
      summary: 'Loaded 3 dataset(s) from database analytics.',
      started_at: '2026-06-24T12:00:00Z',
      attempt_index: 0,
      detail: {
        kind: 'load_context',
        dataset_count: 3,
        database_name: 'analytics',
        retrieval: {
          candidate_table_names: ['orders', 'customers'],
          candidate_metric_names: [],
          candidate_example_ids: [],
          candidate_document_ids: [],
          scanned_table_count: 240,
          omitted_table_count: 228,
          context_truncated: true,
        },
      },
    },
  ];
  render(<ExplainDialog open onClose={noop} steps={loadContext} />);
  expect(screen.getByText('orders')).toBeInTheDocument();
  expect(screen.getByText('customers')).toBeInTheDocument();
  expect(screen.getByText('2 of 240 (228 omitted)')).toBeInTheDocument();
  expect(screen.getByText('schema scan truncated')).toBeInTheDocument();
});
