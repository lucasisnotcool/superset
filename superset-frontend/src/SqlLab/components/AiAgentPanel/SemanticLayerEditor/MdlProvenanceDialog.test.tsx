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

test("shows a teammate's captured name (not 'You') for another user's edit", async () => {
  // DP10/FP2: a non-self user edit renders the captured author name, never "You".
  fetchMock.get(PROVENANCE, [
    {
      id: 'u9',
      kind: 'mdl_updated',
      status: 'ok',
      summary: 'Edited models/orders.json',
      created_at: '2026-06-26T17:00:00Z',
      actor: 'superset:7',
      actor_name: 'alice',
      actor_type: 'user',
      is_self: false,
      detail: { paths: ['models/orders.json'] },
    },
  ]);

  render(
    <MdlProvenanceDialog open projectId="project-1" onClose={jest.fn()} />,
  );

  await screen.findByTestId('provenance-entry');
  const actor = screen.getByTestId('provenance-actor');
  expect(actor).toHaveTextContent('alice');
  expect(actor).not.toHaveTextContent('You');
});

const SCORES_BY_VERSION =
  'http://agent.local/agent/semantic-layer/projects/project-1/coverage/scores-by-version';

test('labels versions with coverage scores and a before/after delta', async () => {
  // Newest-first: a Copilot edit produced version c2 (60%), the prior activation
  // produced c1 (88%). Coverage is NOT a timeline entry — it annotates each
  // version-producing entry, with the delta on the newer one.
  fetchMock.get(PROVENANCE, [
    {
      id: 'edit1',
      kind: 'copilot_edit',
      status: 'ok',
      summary: 'Agent edit',
      created_at: '2026-06-26T07:00:00Z',
      actor_type: 'agent',
      detail: { mdl_checksum: 'c2' },
    },
    {
      id: 'act1',
      kind: 'mdl_activated',
      status: 'ok',
      summary: 'Activated models/orders.json',
      created_at: '2026-06-26T06:00:00Z',
      detail: { mdl_checksum: 'c1', status_from: 'draft', status_to: 'active' },
    },
  ]);
  fetchMock.get(SCORES_BY_VERSION, {
    c1: {
      score: 0.88,
      run_id: 'run-1',
      status: 'complete',
      computed_at: '2026-06-26T06:01:00Z',
      docs_checksum: 'd1',
    },
    c2: {
      score: 0.6,
      run_id: 'run-2',
      status: 'complete',
      computed_at: '2026-06-26T07:01:00Z',
      docs_checksum: 'd1',
    },
  });

  render(
    <MdlProvenanceDialog open projectId="project-1" onClose={jest.fn()} />,
  );

  const entries = await screen.findAllByTestId('provenance-entry');
  // No coverage row injected into the timeline.
  expect(entries).toHaveLength(2);
  const chips = await screen.findAllByTestId('provenance-coverage-chip');
  expect(chips[0]).toHaveTextContent('60%'); // newest version
  expect(chips[1]).toHaveTextContent('88%'); // prior version
  // The newer version shows the drop vs the prior scored version.
  expect(screen.getByTestId('provenance-coverage-delta')).toHaveTextContent(
    '↓28%',
  );
});

test('opens a stored coverage report by clicking a version chip', async () => {
  fetchMock.get(PROVENANCE, [
    {
      id: 'act1',
      kind: 'mdl_activated',
      status: 'ok',
      summary: 'Activated models/orders.json',
      created_at: '2026-06-26T06:00:00Z',
      detail: { mdl_checksum: 'c1', status_from: 'draft', status_to: 'active' },
    },
  ]);
  fetchMock.get(SCORES_BY_VERSION, {
    c1: {
      score: 0.8,
      run_id: 'run-7',
      status: 'complete',
      computed_at: '2026-06-26T06:01:00Z',
      docs_checksum: 'd1',
    },
  });
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

  await userEvent.click(await screen.findByTestId('provenance-coverage-chip'));
  // The report body replaces the timeline; a back link returns to history.
  expect(
    await screen.findByTestId('provenance-coverage-back'),
  ).toBeInTheDocument();
  await userEvent.click(screen.getByTestId('provenance-coverage-back'));
  expect(await screen.findByTestId('provenance-entry')).toBeInTheDocument();
});

test('rolls up agent tool calls and expands the per-file list with source chips', async () => {
  fetchMock.get(PROVENANCE, [
    {
      id: 'agent1',
      kind: 'enrichment',
      status: 'ok',
      summary: 'Onboard + enrich from spec',
      created_at: '2026-06-26T05:00:00Z',
      actor_type: 'agent',
      detail: {
        source_type: 'copilot',
        action_summary: { onboard: 3, write: 1 },
        documents: [{ id: 'd1', filename: 'spec.md' }],
        tool_calls: [
          {
            tool: 'propose_onboard_tables',
            action: 'onboard',
            paths: ['models/a.json', 'models/b.json', 'models/c.json'],
            source_document_ids: [],
            args_summary: {},
            status: 'ok',
          },
          {
            tool: 'write_mdl_file',
            action: 'write',
            paths: ['models/a.json'],
            source_document_ids: ['d1'],
            args_summary: {},
            status: 'ok',
          },
        ],
      },
    },
  ]);

  render(
    <MdlProvenanceDialog open projectId="project-1" onClose={jest.fn()} />,
  );

  const entry = await screen.findByTestId('provenance-entry');
  // The per-verb rollup line aggregates the ledger summarily.
  expect(screen.getByTestId('provenance-rollup')).toHaveTextContent(
    'Onboarded 3 table(s) · Wrote 1 file(s)',
  );
  // Three unique files; preview shows the first 3 (a, b, c) with a doc chip on a.
  let files = screen.getAllByTestId('provenance-file');
  expect(files).toHaveLength(3);
  expect(entry).toHaveTextContent('models/a.json ← spec.md');
  // No "+N more" here (exactly 3 unique paths); add a 4th-file case below.
  expect(screen.queryByTestId('provenance-files-expand')).toBeNull();
  files = screen.getAllByTestId('provenance-file');
  expect(files[0]).toHaveTextContent('models/a.json');
});

test('renders the remove verb in the rollup line', async () => {
  fetchMock.get(PROVENANCE, [
    {
      id: 'agent-remove',
      kind: 'copilot_edit',
      status: 'ok',
      summary: 'Dropped a stale relationship and a calculated column',
      created_at: '2026-06-26T05:00:00Z',
      actor_type: 'agent',
      detail: {
        source_type: 'copilot',
        action_summary: { remove: 2 },
        tool_calls: [
          {
            tool: 'remove_mdl_entity',
            action: 'remove',
            paths: ['relationships.json'],
            source_document_ids: [],
            args_summary: { removed_count: 2 },
            status: 'ok',
          },
        ],
      },
    },
  ]);

  render(
    <MdlProvenanceDialog open projectId="project-1" onClose={jest.fn()} />,
  );

  await screen.findByTestId('provenance-entry');
  expect(screen.getByTestId('provenance-rollup')).toHaveTextContent(
    'Removed 2 entit(ies)',
  );
});

test('truncates a long file list behind a +N more toggle', async () => {
  fetchMock.get(PROVENANCE, [
    {
      id: 'agent2',
      kind: 'copilot_edit',
      status: 'ok',
      summary: 'Wrote many files',
      created_at: '2026-06-26T05:00:00Z',
      actor_type: 'agent',
      detail: {
        action_summary: { write: 5 },
        tool_calls: [
          {
            tool: 'write_mdl_file',
            action: 'write',
            paths: [
              'models/a.json',
              'models/b.json',
              'models/c.json',
              'models/d.json',
              'models/e.json',
            ],
            source_document_ids: [],
            args_summary: {},
            status: 'ok',
          },
        ],
      },
    },
  ]);

  render(
    <MdlProvenanceDialog open projectId="project-1" onClose={jest.fn()} />,
  );

  await screen.findByTestId('provenance-entry');
  expect(screen.getAllByTestId('provenance-file')).toHaveLength(3);
  await userEvent.click(screen.getByTestId('provenance-files-expand'));
  expect(screen.getAllByTestId('provenance-file')).toHaveLength(5);
  await userEvent.click(screen.getByTestId('provenance-files-collapse'));
  expect(screen.getAllByTestId('provenance-file')).toHaveLength(3);
});

test('legacy agent entry without tool_calls falls back to changeset paths', async () => {
  fetchMock.get(PROVENANCE, [
    {
      id: 'legacy1',
      kind: 'copilot_edit',
      status: 'ok',
      summary: 'Applied 2 changes',
      created_at: '2026-06-26T05:00:00Z',
      actor_type: 'agent',
      detail: { paths: ['models/x.json', 'models/y.json'] },
    },
  ]);

  render(
    <MdlProvenanceDialog open projectId="project-1" onClose={jest.fn()} />,
  );

  await screen.findByTestId('provenance-entry');
  // No rollup (no action_summary) but the file list still renders from paths.
  expect(screen.queryByTestId('provenance-rollup')).toBeNull();
  expect(screen.getAllByTestId('provenance-file')).toHaveLength(2);
});

const COVERAGE_STATUS =
  'http://agent.local/agent/semantic-layer/projects/project-1/coverage/status';

test('shows a coverage running indicator while a run is in flight', async () => {
  fetchMock.get(PROVENANCE, []);
  fetchMock.get(COVERAGE_STATUS, {
    status: 'analysing',
    running: true,
    stale: false,
    score: null,
    run_id: null,
  });

  render(
    <MdlProvenanceDialog open projectId="project-1" onClose={jest.fn()} />,
  );

  expect(
    await screen.findByTestId('provenance-coverage-running'),
  ).toBeInTheDocument();
});

test('shows no coverage running indicator when idle', async () => {
  fetchMock.get(PROVENANCE, []);
  fetchMock.get(COVERAGE_STATUS, {
    status: 'ready',
    running: false,
    stale: false,
    score: 0.9,
    run_id: 'r1',
  });

  render(
    <MdlProvenanceDialog open projectId="project-1" onClose={jest.fn()} />,
  );

  await screen.findByText(/No history yet/);
  expect(
    screen.queryByTestId('provenance-coverage-running'),
  ).not.toBeInTheDocument();
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
