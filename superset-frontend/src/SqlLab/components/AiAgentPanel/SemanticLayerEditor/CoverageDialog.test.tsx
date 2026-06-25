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
import CoverageDialog from './CoverageDialog';

const originalAgentUrl = process.env.SUPERSET_AI_AGENT_URL;
const BASE = 'http://agent.local/agent/semantic-layer/projects/project-1';

beforeEach(() => {
  process.env.SUPERSET_AI_AGENT_URL = 'http://agent.local/';
});

afterEach(() => {
  process.env.SUPERSET_AI_AGENT_URL = originalAgentUrl;
  fetchMock.clearHistory().removeRoutes();
});

test('lists documents and runs a coverage audit on the selected one', async () => {
  fetchMock.get(`${BASE}/documents`, [
    {
      id: 'doc-1',
      project_id: 'project-1',
      filename: 'glossary.md',
      content_type: 'text/markdown',
      size_bytes: 10,
      status: 'extracted',
      scope: { database_id: 1, dataset_ids: [] },
      checksum: 'c',
      storage_uri: 'mem://d',
      created_at: '2026-06-19T00:00:00Z',
      updated_at: '2026-06-19T00:00:00Z',
    },
  ]);
  fetchMock.post(`${BASE}/copilot/coverage`, {
    document_id: 'doc-1',
    document_filename: 'glossary.md',
    findings: [],
    total: 4,
    covered: 3,
    partial: 0,
    missing: 1,
    score: 0.75,
    warnings: [],
  });

  render(<CoverageDialog projectId="project-1" open onClose={jest.fn()} />);

  // document auto-selected → run is enabled
  const run = await screen.findByTestId('coverage-run');
  await userEvent.click(run);

  expect(await screen.findByText('75% covered')).toBeInTheDocument();
  const call = fetchMock.callHistory.calls(`${BASE}/copilot/coverage`)[0];
  expect(JSON.parse(String(call.options.body))).toEqual({
    document_id: 'doc-1',
    include_overreach: false,
  });
});

test('shows an empty state when there are no documents', async () => {
  fetchMock.get(`${BASE}/documents`, []);

  render(<CoverageDialog projectId="project-1" open onClose={jest.fn()} />);

  await waitFor(() =>
    expect(
      screen.getByText('Upload a document to audit coverage against the MDL.'),
    ).toBeInTheDocument(),
  );
});
