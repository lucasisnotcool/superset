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
import GoldenQueriesPanel from './GoldenQueriesPanel';

const originalAgentUrl = process.env.SUPERSET_AI_AGENT_URL;

const LIST_URL =
  'http://agent.local/agent/semantic-layer/projects/proj-1/mdl-files';
const UPDATE_URL =
  'http://agent.local/agent/semantic-layer/projects/proj-1/mdl-files/qf-1';

const queriesFile = (entries: unknown[]) => ({
  id: 'qf-1',
  project_id: 'proj-1',
  path: 'queries.json',
  filename: 'queries.json',
  content: JSON.stringify({ queries: entries }),
  content_type: 'application/json',
  source_type: 'manual',
  status: 'draft',
  checksum: 'x',
  created_at: '2026-06-30T00:00:00Z',
  updated_at: '2026-06-30T00:00:00Z',
});

const ENTRY = {
  name: 'top customers',
  question: 'who are the top customers?',
  semantic_sql: 'SELECT * FROM customers',
  verified_at: 123,
};

beforeEach(() => {
  process.env.SUPERSET_AI_AGENT_URL = 'http://agent.local/';
});

afterEach(() => {
  process.env.SUPERSET_AI_AGENT_URL = originalAgentUrl;
  fetchMock.clearHistory().removeRoutes();
});

const renderPanel = (canWrite = true) =>
  render(<GoldenQueriesPanel projectId="proj-1" canWrite={canWrite} />, {
    useRedux: true,
  });

test('lists the project golden queries with a verified badge', async () => {
  fetchMock.get(LIST_URL, [queriesFile([ENTRY])]);
  renderPanel();
  expect(
    await screen.findByText('who are the top customers?'),
  ).toBeInTheDocument();
  expect(screen.getByText('Verified')).toBeInTheDocument();
});

test('shows an empty state when there are no golden queries', async () => {
  fetchMock.get(LIST_URL, [queriesFile([])]);
  renderPanel();
  expect(await screen.findByText('No golden queries yet.')).toBeInTheDocument();
});

test('removes a golden query by rewriting queries.json', async () => {
  let rows = [queriesFile([ENTRY])];
  fetchMock.get(LIST_URL, () => rows);
  fetchMock.patch(UPDATE_URL, () => {
    rows = [queriesFile([])];
    return rows[0];
  });

  renderPanel();
  await screen.findByText('who are the top customers?');
  await userEvent.click(
    screen.getByRole('button', { name: /Remove golden query/ }),
  );
  await userEvent.click(screen.getByRole('button', { name: 'Remove' }));

  await waitFor(() =>
    expect(screen.getByText('No golden queries yet.')).toBeInTheDocument(),
  );
});
