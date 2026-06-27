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
import { render, screen } from 'spec/helpers/testing-library';
import MdlProvenanceDialog from './MdlProvenanceDialog';

const originalAgentUrl = process.env.SUPERSET_AI_AGENT_URL;
const PROVENANCE =
  'http://agent.local/agent/semantic-layer/projects/project-1/provenance';

beforeEach(() => {
  process.env.SUPERSET_AI_AGENT_URL = 'http://agent.local/';
});

afterEach(() => {
  process.env.SUPERSET_AI_AGENT_URL = originalAgentUrl;
  fetchMock.clearHistory().removeRoutes();
});

test('renders the provenance timeline newest-first with per-kind detail', async () => {
  fetchMock.get(PROVENANCE, [
    {
      id: 'e3',
      kind: 'mdl_activated',
      status: 'ok',
      summary: 'Activated models/orders.json',
      created_at: '2026-06-26T03:00:00Z',
      actor: 'user-1',
      detail: { status_from: 'draft', status_to: 'active' },
    },
    {
      id: 'e1',
      kind: 'onboarding',
      status: 'ok',
      summary: 'Onboarded 2 model(s); 2 activated.',
      created_at: '2026-06-26T01:00:00Z',
      detail: { mode: 'selected', model_count: 2, dataset_ids: [1, 2] },
    },
  ]);

  render(
    <MdlProvenanceDialog open projectId="project-1" onClose={jest.fn()} />,
  );

  const entries = await screen.findAllByTestId('provenance-entry');
  expect(entries).toHaveLength(2);
  // First row is the activation (server returns newest-first).
  expect(entries[0]).toHaveTextContent('Activated model');
  expect(entries[0]).toHaveTextContent('draft → active');
  expect(entries[1]).toHaveTextContent('Onboarding');
  expect(entries[1]).toHaveTextContent('2 model(s)');
});

test('shows an empty state when there is no history', async () => {
  fetchMock.get(PROVENANCE, []);

  render(
    <MdlProvenanceDialog open projectId="project-1" onClose={jest.fn()} />,
  );

  expect(await screen.findByText(/No history yet/)).toBeInTheDocument();
});

test('does not fetch when closed', () => {
  fetchMock.get(PROVENANCE, []);

  render(
    <MdlProvenanceDialog
      open={false}
      projectId="project-1"
      onClose={jest.fn()}
    />,
  );

  expect(fetchMock.callHistory.calls(PROVENANCE)).toHaveLength(0);
});
