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
import userEvent from '@testing-library/user-event';
import { render, screen } from 'spec/helpers/testing-library';
import CoverageBadge from './CoverageBadge';

const STATUS =
  'http://agent.local/agent/semantic-layer/projects/p1/coverage/status';
const REFRESH =
  'http://agent.local/agent/semantic-layer/projects/p1/coverage/refresh';
const LATEST =
  'http://agent.local/agent/semantic-layer/projects/p1/coverage/latest';

const REPORT = {
  document_filename: '',
  findings: [],
  total: 4,
  covered: 3,
  partial: 0,
  missing: 1,
  score: 0.82,
  overreach: [],
  unsupported: 0,
  warnings: [],
};

const completedRun = {
  id: 'r1',
  project_id: 'p1',
  owner_id: 'o1',
  mdl_checksum: 'm1',
  docs_checksum: 'd1',
  status: 'complete',
  score: 0.82,
  report: REPORT,
  created_at: '2026-06-29T10:00:00Z',
  updated_at: '2026-06-29T10:00:00Z',
};

const originalAgentUrl = process.env.SUPERSET_AI_AGENT_URL;

beforeEach(() => {
  process.env.SUPERSET_AI_AGENT_URL = 'http://agent.local/';
});

afterEach(() => {
  process.env.SUPERSET_AI_AGENT_URL = originalAgentUrl;
  fetchMock.clearHistory().removeRoutes();
});

test('opens the viewer on click and does NOT re-run', async () => {
  fetchMock.get(STATUS, {
    status: 'ready',
    running: false,
    stale: false,
    score: 0.82,
    run_id: 'r1',
  });
  fetchMock.get(LATEST, completedRun);
  fetchMock.post(REFRESH, { scheduled: true });

  render(<CoverageBadge projectId="p1" />);

  const badge = await screen.findByTestId('coverage-badge');
  expect(badge).toHaveTextContent('82%');

  await userEvent.click(badge);

  // Viewer opens; clicking the badge must NOT have scheduled a re-run.
  expect(await screen.findByTestId('coverage-panel')).toBeInTheDocument();
  expect(fetchMock.callHistory.calls(REFRESH)).toHaveLength(0);
  expect(await screen.findByTestId('coverage-report')).toBeInTheDocument();
});

test('re-runs only via the explicit button inside the viewer', async () => {
  fetchMock.get(STATUS, {
    status: 'ready',
    running: false,
    stale: false,
    score: 0.82,
    run_id: 'r1',
  });
  fetchMock.get(LATEST, completedRun);
  fetchMock.post(REFRESH, { scheduled: true });

  render(<CoverageBadge projectId="p1" />);

  await userEvent.click(await screen.findByTestId('coverage-badge'));
  await userEvent.click(await screen.findByTestId('coverage-rerun'));

  expect(fetchMock.callHistory.calls(REFRESH)).toHaveLength(1);
});

test('renders nothing when coverage has never run', async () => {
  fetchMock.get(STATUS, {
    status: 'none',
    running: false,
    stale: false,
    score: null,
    run_id: null,
  });

  render(<CoverageBadge projectId="p1" />);

  // Give the fetch a tick; the badge must stay absent for the "none" state.
  expect(screen.queryByTestId('coverage-badge')).not.toBeInTheDocument();
});

test('shows a loading placeholder until the first status resolves (G11)', async () => {
  let release: (body: unknown) => void = () => {};
  fetchMock.get(
    STATUS,
    new Promise(resolve => {
      release = resolve;
    }),
  );

  render(<CoverageBadge projectId="p1" />);

  // Before the first status resolves: a placeholder, not a blank that pops in.
  expect(screen.getByTestId('coverage-loading')).toBeInTheDocument();
  expect(screen.queryByTestId('coverage-badge')).not.toBeInTheDocument();

  release({
    status: 'ready',
    running: false,
    stale: false,
    score: 0.7,
    run_id: 'r1',
  });

  expect(await screen.findByTestId('coverage-badge')).toHaveTextContent('70%');
  expect(screen.queryByTestId('coverage-loading')).not.toBeInTheDocument();
});

test('flags a stale score after the MDL changes', async () => {
  fetchMock.get(STATUS, {
    status: 'stale',
    running: false,
    stale: true,
    score: 0.5,
    run_id: 'r1',
  });

  render(<CoverageBadge projectId="p1" />);

  expect(await screen.findByTestId('coverage-badge')).toHaveTextContent(
    'stale',
  );
});

test('mirrors live stage detail in the analysing label', async () => {
  fetchMock.get(STATUS, {
    status: 'analysing',
    running: true,
    stale: false,
    score: null,
    run_id: null,
    progress: { stage: 'judging', detail: '142 claims vs 38 facts' },
  });

  render(<CoverageBadge projectId="p1" />);

  expect(await screen.findByTestId('coverage-badge')).toHaveTextContent(
    '142 claims vs 38 facts',
  );
});

test('shows live progress in the viewer while a run is in flight', async () => {
  fetchMock.get(STATUS, {
    status: 'analysing',
    running: true,
    stale: false,
    score: null,
    run_id: null,
    progress: {
      stage: 'extracting',
      detail: 'orders.pdf',
      current: 1,
      total: 5,
    },
  });

  render(<CoverageBadge projectId="p1" />);

  await userEvent.click(await screen.findByTestId('coverage-badge'));
  expect(await screen.findByTestId('coverage-progress')).toBeInTheDocument();
  // Running state shows progress, not a stored report.
  expect(screen.queryByTestId('coverage-report')).not.toBeInTheDocument();
});

test('refetches status when a coverage_completed SSE event arrives', async () => {
  const listeners: Record<string, Array<() => void>> = {};
  const OriginalEventSource = globalThis.EventSource;
  class MockEventSource {
    addEventListener(type: string, handler: () => void) {
      (listeners[type] ??= []).push(handler);
    }

    removeEventListener() {}

    close() {}
  }
  // @ts-ignore - test double
  globalThis.EventSource = MockEventSource;

  // First status: analysing; after the event, a completed score.
  fetchMock.get(STATUS, {
    status: 'analysing',
    running: true,
    stale: false,
    score: null,
    run_id: null,
  });

  render(<CoverageBadge projectId="p1" />);
  expect(await screen.findByTestId('coverage-badge')).toHaveTextContent(
    'analysing',
  );

  fetchMock.removeRoutes().clearHistory();
  fetchMock.get(STATUS, {
    status: 'ready',
    running: false,
    stale: false,
    score: 0.9,
    run_id: 'r2',
  });
  // Simulate the server pushing a completed run.
  listeners.coverage_completed?.forEach(handler => handler());

  expect(await screen.findByText(/90%/)).toBeInTheDocument();
  globalThis.EventSource = OriginalEventSource;
});
