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
import { CoverageStatusInfo } from '../api';
import CoveragePanel from './CoveragePanel';

const LATEST =
  'http://agent.local/agent/semantic-layer/projects/p1/coverage/latest';
const RECOVERY =
  'http://agent.local/agent/semantic-layer/projects/p1/coverage/runs/run-1/recovery';

const RUN = {
  id: 'run-1',
  project_id: 'p1',
  owner_id: 'o1',
  mdl_checksum: 'm1',
  docs_checksum: 'd1',
  status: 'complete',
  score: 0.6,
  report: {
    document_filename: '',
    findings: [],
    total: 5,
    covered: 3,
    partial: 0,
    missing: 2,
    score: 0.6,
    overreach: [],
    unsupported: 0,
    warnings: [],
  },
  created_at: '2026-06-30T10:00:00Z',
  updated_at: '2026-06-30T10:00:00Z',
};

const baseInfo: CoverageStatusInfo = {
  status: 'ready',
  running: false,
  stale: false,
  score: 0.6,
  run_id: 'run-1',
};

const originalAgentUrl = process.env.SUPERSET_AI_AGENT_URL;

beforeEach(() => {
  process.env.SUPERSET_AI_AGENT_URL = 'http://agent.local/';
  fetchMock.get(LATEST, RUN);
});

afterEach(() => {
  process.env.SUPERSET_AI_AGENT_URL = originalAgentUrl;
  fetchMock.clearHistory().removeRoutes();
});

test('surfaces the recovery agent preparing (in-flight) state in the report', async () => {
  render(
    <CoveragePanel
      projectId="p1"
      open
      onClose={jest.fn()}
      onRerun={jest.fn()}
      info={{
        ...baseInfo,
        recovery_status: 'running',
        recovery_run_id: 'run-1',
        recovery_dismissed: false,
      }}
    />,
  );

  expect(
    await screen.findByText('Preparing coverage suggestions…'),
  ).toBeInTheDocument();
});

test('surfaces the recovery agent failure state in the report', async () => {
  render(
    <CoveragePanel
      projectId="p1"
      open
      onClose={jest.fn()}
      onRerun={jest.fn()}
      info={{
        ...baseInfo,
        recovery_status: 'failed',
        recovery_run_id: 'run-1',
        recovery_dismissed: false,
      }}
    />,
  );

  expect(
    await screen.findByText('Coverage suggestions unavailable'),
  ).toBeInTheDocument();
});

test('offers the Review entrypoint when suggestions are ready', async () => {
  render(
    <CoveragePanel
      projectId="p1"
      open
      onClose={jest.fn()}
      onRerun={jest.fn()}
      info={{
        ...baseInfo,
        recovery_status: 'ready',
        recovery_run_id: 'run-1',
        recovery_dismissed: false,
      }}
    />,
  );

  expect(
    await screen.findByTestId('coverage-review-suggestions'),
  ).toBeInTheDocument();
});

test('reviewing suggestions extends the same dialog into a second pane', async () => {
  fetchMock.get(RECOVERY, {
    run_id: 'run-1',
    status: 'ready',
    conversation_id: 'c1',
    suggestion_count: 1,
    dismissed: false,
    stale: false,
    changeset: {
      items: [
        {
          op: 'create',
          path: 'models/fix.json',
          current_content: '',
          proposed_content: '{"a":1}',
          summary: 'Closes a gap',
          validation: { valid: true },
        },
      ],
      warnings: [],
      steps: [],
      message: 'fix',
    },
  });

  render(
    <CoveragePanel
      projectId="p1"
      open
      onClose={jest.fn()}
      onRerun={jest.fn()}
      info={{
        ...baseInfo,
        recovery_status: 'ready',
        recovery_run_id: 'run-1',
        recovery_dismissed: false,
      }}
    />,
  );

  await userEvent.click(
    await screen.findByTestId('coverage-review-suggestions'),
  );

  // The suggestions render INLINE in the same coverage dialog (second pane); the
  // report stays visible beside them and no second modal is opened.
  expect(
    await screen.findByTestId('coverage-suggestions-pane'),
  ).toBeInTheDocument();
  expect(screen.getByTestId('coverage-report')).toBeInTheDocument();
  expect(await screen.findByTestId('changeset-review')).toBeInTheDocument();
  // Exactly one dialog is present — no double-dialogging.
  expect(screen.getAllByTestId('coverage-panel')).toHaveLength(1);
});

test('shows no recovery callout when there is nothing to recover', async () => {
  render(
    <CoveragePanel
      projectId="p1"
      open
      onClose={jest.fn()}
      onRerun={jest.fn()}
      info={{ ...baseInfo, recovery_status: 'none', recovery_run_id: 'run-1' }}
    />,
  );

  // The report renders, but no recovery callouts appear.
  expect(await screen.findByTestId('coverage-report')).toBeInTheDocument();
  expect(
    screen.queryByText('Preparing coverage suggestions…'),
  ).not.toBeInTheDocument();
  expect(
    screen.queryByTestId('coverage-review-suggestions'),
  ).not.toBeInTheDocument();
});
