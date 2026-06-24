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

test('surfaces typed detail: the semantic->native rewrite is shown', () => {
  render(<ExplainDialog open onClose={noop} steps={steps} />);
  expect(screen.getByText('Semantic SQL')).toBeInTheDocument();
  expect(screen.getByText('SELECT a FROM orders')).toBeInTheDocument();
  expect(screen.getByText('Native SQL')).toBeInTheDocument();
  expect(screen.getByText('SELECT a FROM sales.orders')).toBeInTheDocument();
  // Retrieval provenance is legible (matched models, retriever mode). "orders"
  // appears as both a matched model and a referenced table, hence getAllByText.
  expect(screen.getAllByText('orders').length).toBeGreaterThan(0);
  expect(screen.getByText('embedding')).toBeInTheDocument();
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
