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

test('renders agent enrichment with document chips, actor tag and a conversation link', async () => {
  fetchMock.get(PROVENANCE, [
    {
      id: 'a1',
      kind: 'enrichment',
      status: 'ok',
      summary: 'Add synonyms from glossary',
      created_at: '2026-06-26T05:00:00Z',
      actor_type: 'agent',
      detail: {
        source_type: 'copilot',
        conversation_id: 'conv-9',
        documents: [{ id: 'd1', filename: 'glossary.md' }],
      },
    },
  ]);
  const onOpenConversation = jest.fn();

  render(
    <MdlProvenanceDialog
      open
      projectId="project-1"
      onClose={jest.fn()}
      onOpenConversation={onOpenConversation}
    />,
  );

  const entry = await screen.findByTestId('provenance-entry');
  expect(entry).toHaveTextContent('Enrichment');
  expect(screen.getByTestId('provenance-actor')).toHaveTextContent('Agent');
  expect(screen.getByTestId('provenance-documents')).toHaveTextContent(
    'glossary.md',
  );
  await userEvent.click(screen.getByTestId('provenance-open-conversation'));
  expect(onOpenConversation).toHaveBeenCalledWith('conv-9');
});

test('renders a coalesced user run as an edited-N-times range', async () => {
  fetchMock.get(PROVENANCE, [
    {
      id: 'u1',
      kind: 'mdl_updated',
      status: 'ok',
      summary: 'Edited 3 times',
      created_at: '2026-06-26T17:00:00Z',
      first_at: '2026-06-25T14:00:00Z',
      edit_count: 3,
      actor_type: 'user',
      detail: { paths: ['models/orders.json'] },
    },
  ]);

  render(
    <MdlProvenanceDialog open projectId="project-1" onClose={jest.fn()} />,
  );

  const entry = await screen.findByTestId('provenance-entry');
  expect(entry).toHaveTextContent('Edited 3 times');
  expect(entry).toHaveTextContent('3 edits');
  expect(screen.getByTestId('provenance-actor')).toHaveTextContent('You');
});

test('opens a stored coverage report from a coverage entry', async () => {
  fetchMock.get(PROVENANCE, [
    {
      id: 'cov1',
      kind: 'coverage',
      status: 'ok',
      summary: 'Coverage 80%',
      created_at: '2026-06-26T06:00:00Z',
      actor_type: 'system',
      detail: { run_id: 'run-7', score: 0.8 },
    },
  ]);
  fetchMock.get(
    'http://agent.local/agent/semantic-layer/projects/project-1/coverage/runs/run-7',
    {
      id: 'run-7',
      project_id: 'project-1',
      owner_id: 'u1',
      mdl_checksum: 'c1',
      docs_checksum: 'd1',
      status: 'complete',
      score: 0.8,
      report: {
        document_filename: '',
        findings: [],
        total: 5,
        covered: 4,
        partial: 0,
        missing: 1,
        score: 0.8,
        overreach: [],
        unsupported: 0,
        warnings: [],
      },
      created_at: '2026-06-26T06:00:00Z',
      updated_at: '2026-06-26T06:00:00Z',
    },
  );

  render(
    <MdlProvenanceDialog open projectId="project-1" onClose={jest.fn()} />,
  );

  await userEvent.click(await screen.findByTestId('provenance-open-coverage'));
  // The report body replaces the timeline; a back link returns to history.
  expect(
    await screen.findByTestId('provenance-coverage-back'),
  ).toBeInTheDocument();
  await userEvent.click(screen.getByTestId('provenance-coverage-back'));
  expect(await screen.findByTestId('provenance-entry')).toBeInTheDocument();
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
