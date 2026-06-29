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
import { render, screen, waitFor } from 'spec/helpers/testing-library';
import RecoveryBanner from './RecoveryBanner';

const base = 'http://agent.local/agent/semantic-layer/projects/p1';
const STATUS = `${base}/coverage/status`;
const RECOVERY = `${base}/coverage/runs/run-1/recovery`;
const DISMISS = `${base}/coverage/runs/run-1/recovery/dismiss`;

const READY_STATUS = {
  status: 'ready',
  running: false,
  stale: false,
  score: 0.6,
  run_id: 'run-1',
  recovery_status: 'ready',
  recovery_run_id: 'run-1',
  recovery_dismissed: false,
};

const originalAgentUrl = process.env.SUPERSET_AI_AGENT_URL;
let OriginalEventSource: typeof globalThis.EventSource;

beforeEach(() => {
  process.env.SUPERSET_AI_AGENT_URL = 'http://agent.local/';
  OriginalEventSource = globalThis.EventSource;
  class MockEventSource {
    addEventListener() {}

    removeEventListener() {}

    close() {}
  }
  // @ts-ignore - test double
  globalThis.EventSource = MockEventSource;
});

afterEach(() => {
  process.env.SUPERSET_AI_AGENT_URL = originalAgentUrl;
  globalThis.EventSource = OriginalEventSource;
  fetchMock.clearHistory().removeRoutes();
});

test('shows the banner when recovery suggestions are ready and undismissed', async () => {
  fetchMock.get(STATUS, READY_STATUS);

  render(<RecoveryBanner projectId="p1" />);

  expect(await screen.findByTestId('recovery-banner')).toBeInTheDocument();
});

test('Review opens the suggestions dialog', async () => {
  fetchMock.get(STATUS, READY_STATUS);
  fetchMock.get(RECOVERY, {
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
          summary: 'Closes a gap',
          validation: { valid: true },
        },
      ],
      warnings: [],
      steps: [],
      message: 'fix',
    },
  });

  render(<RecoveryBanner projectId="p1" />);

  await userEvent.click(await screen.findByTestId('recovery-banner-review'));
  expect(await screen.findByTestId('recovery-dialog')).toBeInTheDocument();
  expect(await screen.findByTestId('changeset-review')).toBeInTheDocument();
});

test('hides the banner once dismissed', async () => {
  fetchMock.get(STATUS, { ...READY_STATUS, recovery_dismissed: true });

  render(<RecoveryBanner projectId="p1" />);

  // Allow the status fetch to settle; the banner must stay hidden.
  await waitFor(() =>
    expect(fetchMock.callHistory.calls(STATUS).length).toBeGreaterThan(0),
  );
  expect(screen.queryByTestId('recovery-banner')).not.toBeInTheDocument();
});

test('hides the banner when there are no ready suggestions', async () => {
  fetchMock.get(STATUS, {
    ...READY_STATUS,
    recovery_status: 'none',
    recovery_run_id: null,
  });

  render(<RecoveryBanner projectId="p1" />);

  await waitFor(() =>
    expect(fetchMock.callHistory.calls(STATUS).length).toBeGreaterThan(0),
  );
  expect(screen.queryByTestId('recovery-banner')).not.toBeInTheDocument();
});

test('closing the banner durably dismisses it on the server', async () => {
  fetchMock.get(STATUS, READY_STATUS);
  fetchMock.post(DISMISS, { dismissed: true });

  render(<RecoveryBanner projectId="p1" />);
  await screen.findByTestId('recovery-banner');

  await userEvent.click(screen.getByTestId('recovery-banner-dismiss'));

  await waitFor(() =>
    expect(fetchMock.callHistory.calls(DISMISS)).toHaveLength(1),
  );
});
