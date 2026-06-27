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

const originalAgentUrl = process.env.SUPERSET_AI_AGENT_URL;

beforeEach(() => {
  process.env.SUPERSET_AI_AGENT_URL = 'http://agent.local/';
});

afterEach(() => {
  process.env.SUPERSET_AI_AGENT_URL = originalAgentUrl;
  fetchMock.clearHistory().removeRoutes();
});

test('shows the latest score and re-runs on click', async () => {
  fetchMock.get(STATUS, {
    status: 'ready',
    running: false,
    stale: false,
    score: 0.82,
    run_id: 'r1',
  });
  fetchMock.post(REFRESH, { scheduled: true });

  render(<CoverageBadge projectId="p1" />);

  const badge = await screen.findByTestId('coverage-badge');
  expect(badge).toHaveTextContent('82%');

  await userEvent.click(badge);
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
