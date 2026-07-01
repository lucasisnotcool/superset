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
import RecoverySuggestionsDialog from './RecoverySuggestionsDialog';

const base = 'http://agent.local/agent/semantic-layer/projects/p1';
const RECOVERY = `${base}/coverage/runs/run-1/recovery`;
const DISMISS = `${base}/coverage/runs/run-1/recovery/dismiss`;
const APPLY = `${base}/copilot/apply`;

const READY = {
  run_id: 'run-1',
  status: 'ready',
  conversation_id: 'conv-1',
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
        summary: 'Closes: "a drive unit is a patty"',
        validation: { valid: true },
      },
    ],
    warnings: [],
    steps: [],
    message: 'Proposed a fix',
  },
};

const originalAgentUrl = process.env.SUPERSET_AI_AGENT_URL;

beforeEach(() => {
  process.env.SUPERSET_AI_AGENT_URL = 'http://agent.local/';
});

afterEach(() => {
  process.env.SUPERSET_AI_AGENT_URL = originalAgentUrl;
  fetchMock.clearHistory().removeRoutes();
});

test('loads and renders recovery suggestions with the closed-claim rationale', async () => {
  fetchMock.get(RECOVERY, READY);

  render(
    <RecoverySuggestionsDialog
      projectId="p1"
      runId="run-1"
      open
      onClose={jest.fn()}
    />,
  );

  expect(await screen.findByTestId('changeset-review')).toBeInTheDocument();
  expect(
    screen.getByText('Closes: "a drive unit is a patty"'),
  ).toBeInTheDocument();
});

test('applying posts accepted items, dismisses, and confirms success', async () => {
  fetchMock.get(RECOVERY, READY);
  fetchMock.post(APPLY, [
    { id: 'f1', path: 'models/fix.json', status: 'draft' },
  ]);
  fetchMock.post(DISMISS, { dismissed: true });
  const onApplied = jest.fn();

  render(
    <RecoverySuggestionsDialog
      projectId="p1"
      runId="run-1"
      open
      onClose={jest.fn()}
      onApplied={onApplied}
    />,
  );

  await userEvent.click(await screen.findByTestId('changeset-apply'));

  // Visibility of system status: an explicit success confirmation, not a silent
  // close. The user is told the edits landed and what to do next.
  expect(await screen.findByTestId('recovery-applied')).toBeInTheDocument();
  expect(fetchMock.callHistory.calls(APPLY)).toHaveLength(1);
  // Applying resolves the notification for this run.
  expect(fetchMock.callHistory.calls(DISMISS)).toHaveLength(1);
  expect(onApplied).toHaveBeenCalled();
});

test('surfaces a friendly, non-technical error when apply fails', async () => {
  fetchMock.get(RECOVERY, READY);
  fetchMock.post(APPLY, { status: 500, body: { message: 'boom' } });

  render(
    <RecoverySuggestionsDialog
      projectId="p1"
      runId="run-1"
      open
      onClose={jest.fn()}
    />,
  );

  await userEvent.click(await screen.findByTestId('changeset-apply'));

  expect(await screen.findByTestId('recovery-error')).toHaveTextContent(
    'Could not apply the suggestions. Please try again.',
  );
});

test('shows a preparing state while the agent is still running', async () => {
  fetchMock.get(RECOVERY, {
    run_id: 'run-1',
    status: 'running',
    conversation_id: null,
    suggestion_count: 0,
    dismissed: false,
    stale: false,
    changeset: null,
  });

  render(
    <RecoverySuggestionsDialog
      projectId="p1"
      runId="run-1"
      open
      onClose={jest.fn()}
    />,
  );

  expect(await screen.findByTestId('recovery-preparing')).toBeInTheDocument();
});

test('flags stale suggestions when the MDL moved on', async () => {
  fetchMock.get(RECOVERY, { ...READY, stale: true });

  render(
    <RecoverySuggestionsDialog
      projectId="p1"
      runId="run-1"
      open
      onClose={jest.fn()}
    />,
  );

  expect(await screen.findByText(/MDL has changed/)).toBeInTheDocument();
});

test('offers only Close in the footer (no Dismiss)', async () => {
  fetchMock.get(RECOVERY, READY);

  render(
    <RecoverySuggestionsDialog
      projectId="p1"
      runId="run-1"
      open
      onClose={jest.fn()}
    />,
  );

  await screen.findByTestId('changeset-review');
  expect(screen.getByTestId('recovery-close')).toBeInTheDocument();
  expect(screen.queryByTestId('recovery-dismiss')).not.toBeInTheDocument();
});
