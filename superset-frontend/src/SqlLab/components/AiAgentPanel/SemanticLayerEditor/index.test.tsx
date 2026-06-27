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
  within,
} from 'spec/helpers/testing-library';
import SemanticLayerEditor from '.';

// Stub the shared ingestion hook so the Upload-document wiring can be asserted
// without real uploads; its own behavior is covered in useDocumentIngestion.test.
const mockIngest = jest.fn();
jest.mock('../useDocumentIngestion', () => ({
  __esModule: true,
  default: () => ({ ingest: mockIngest, isIngesting: false }),
}));

const originalAgentUrl = process.env.SUPERSET_AI_AGENT_URL;

beforeEach(() => {
  process.env.SUPERSET_AI_AGENT_URL = 'http://agent.local/';
  mockIngest.mockReset();
});

afterEach(() => {
  process.env.SUPERSET_AI_AGENT_URL = originalAgentUrl;
  fetchMock.clearHistory().removeRoutes();
});

const project = {
  id: 'project-1',
  name: 'Database 1.prod.main',
  owner_id: 'local',
  database_uri_fingerprint: 'fingerprint',
  catalog_name: 'prod',
  schema_name: 'main',
  default_database_id: 1,
  visibility: 'db_access',
  status: 'active',
  permission: 'admin',
  created_at: '2026-06-19T00:00:00Z',
  updated_at: '2026-06-19T00:00:00Z',
};

const mdlFile = (id: string, path: string) => ({
  id,
  project_id: 'project-1',
  path,
  filename: path.split('/').pop(),
  content: `{"models":[{"name":"${id}"}]}`,
  content_type: 'application/json',
  source_type: 'manual',
  status: 'active',
  validation: { valid: true, messages: [] },
  checksum: id,
  created_at: '2026-06-19T00:00:00Z',
  updated_at: '2026-06-19T00:00:00Z',
});

const readinessFor = (files: { status?: string }[]) => {
  const active = files.filter(file => file.status === 'active');
  if (active.length > 0) {
    return {
      status: 'ready',
      ready: true,
      has_active_models: true,
      active_model_count: active.length,
      detail: 'Semantic layer is ready.',
    };
  }
  return {
    status: 'empty',
    ready: false,
    has_active_models: false,
    active_model_count: 0,
    detail: 'Schema has not been onboarded yet.',
  };
};

const mockBaseRoutes = (files: { status?: string }[] = []) => {
  fetchMock.post(
    'http://agent.local/agent/semantic-layer/projects/resolve',
    project,
  );
  fetchMock.get(
    'http://agent.local/agent/semantic-layer/projects/project-1/mdl-files',
    files,
  );
  fetchMock.get(
    'http://agent.local/agent/semantic-layer/projects/project-1/readiness',
    readinessFor(files),
  );
  fetchMock.get(
    'http://agent.local/agent/semantic-layer/projects/project-1/state',
    {
      project_id: 'project-1',
      database_id: 1,
      catalog_name: 'prod',
      schema_name: 'main',
      dataset_ids: [],
      document_count: 0,
      last_error: null,
    },
  );
};

const onboardingJob = (warnings: string[] = []) => ({
  id: 'job-1',
  kind: 'onboarding',
  status: 'completed',
  project_id: 'project-1',
  created_at: '2026-06-19T00:00:00Z',
  updated_at: '2026-06-19T00:00:00Z',
  result: {
    project_id: 'project-1',
    model_count: 1,
    activated_count: 1,
    warnings,
    files: [mdlFile('moves', 'models/moves.json')],
  },
});

const mockOnboard = (warnings: string[] = []) => {
  fetchMock.post(
    'http://agent.local/agent/semantic-layer/projects/project-1/onboard',
    onboardingJob(warnings),
  );
};

const mockReset = (deleted = 1) => {
  fetchMock.post(
    'http://agent.local/agent/semantic-layer/projects/project-1/reset',
    { deleted },
  );
};

test('loads the project once per scope without re-fetch loops', async () => {
  mockBaseRoutes([
    mdlFile('a', 'models/a.json'),
    mdlFile('b', 'models/b.json'),
  ]);

  render(
    <SemanticLayerEditor databaseId={1} catalogName="prod" schemaName="main" />,
    { useRedux: true },
  );

  await waitFor(() => {
    expect(screen.getByText('Database 1.prod.main')).toBeInTheDocument();
  });

  // Selecting the first file must not re-trigger the load effect: the project
  // is resolved and listed exactly once for the scope.
  await new Promise(resolve => {
    setTimeout(resolve, 50);
  });
  expect(
    fetchMock.callHistory.calls(
      'http://agent.local/agent/semantic-layer/projects/resolve',
    ),
  ).toHaveLength(1);
  expect(
    fetchMock.callHistory.calls(
      'http://agent.local/agent/semantic-layer/projects/project-1/mdl-files',
    ),
  ).toHaveLength(1);
});

test('shows the Copilot rail by default and toggles it off', async () => {
  mockBaseRoutes([mdlFile('a', 'models/a.json')]);

  render(
    <SemanticLayerEditor databaseId={1} catalogName="prod" schemaName="main" />,
    { useRedux: true },
  );

  await waitFor(() => {
    expect(screen.getByText('Database 1.prod.main')).toBeInTheDocument();
  });
  await waitFor(() => {
    expect(screen.getByTestId('copilot-rail')).toBeInTheDocument();
  });

  await userEvent.click(screen.getByTestId('toggle-copilot'));

  expect(screen.queryByTestId('copilot-rail')).not.toBeInTheDocument();
});

test('blocks the Copilot with an onboarding prompt until a base model is active', async () => {
  // A project with only draft (non-active) MDL is not "ready": the Copilot rail
  // shows the onboarding bootstrap view rather than the chat.
  const draftFile = { ...mdlFile('a', 'models/a.json'), status: 'draft' };
  mockBaseRoutes([draftFile]);

  render(
    <SemanticLayerEditor databaseId={1} catalogName="prod" schemaName="main" />,
    { useRedux: true },
  );

  await waitFor(() => {
    expect(screen.getByText('Database 1.prod.main')).toBeInTheDocument();
  });
  await waitFor(() => {
    expect(screen.getByTestId('copilot-not-ready')).toBeInTheDocument();
  });
  // The chat surface is gated; the onboarding CTA is shown instead.
  expect(screen.queryByTestId('copilot-input')).not.toBeInTheDocument();
  expect(screen.getByTestId('copilot-onboard')).toBeInTheDocument();
});

test('mounts the Copilot once a base model is active (ready)', async () => {
  mockBaseRoutes([mdlFile('a', 'models/a.json')]); // active by default

  render(
    <SemanticLayerEditor databaseId={1} catalogName="prod" schemaName="main" />,
    { useRedux: true },
  );

  await waitFor(() => {
    expect(screen.getByTestId('copilot-input')).toBeInTheDocument();
  });
  expect(screen.queryByTestId('copilot-not-ready')).not.toBeInTheDocument();
});

test('does not auto-onboard an empty schema; shows the Onboard CTA instead', async () => {
  mockBaseRoutes([]);
  mockOnboard();

  render(
    <SemanticLayerEditor databaseId={1} catalogName="prod" schemaName="main" />,
    { useRedux: true },
  );

  await waitFor(() => {
    expect(screen.getByTestId('copilot-not-ready')).toBeInTheDocument();
  });
  // The explicit onboarding CTA is shown and no chat composer is mounted.
  expect(screen.getByTestId('copilot-onboard')).toBeInTheDocument();
  expect(screen.queryByTestId('copilot-input')).not.toBeInTheDocument();
  // Critically: onboarding is NOT fired automatically.
  expect(
    fetchMock.callHistory.calls(
      'http://agent.local/agent/semantic-layer/projects/project-1/onboard',
    ),
  ).toHaveLength(0);
});

test('the provenance button opens the MDL history dialog', async () => {
  mockBaseRoutes([mdlFile('a', 'models/a.json')]);
  fetchMock.get(
    'http://agent.local/agent/semantic-layer/projects/project-1/provenance',
    [
      {
        id: 'e1',
        kind: 'mdl_created',
        status: 'ok',
        summary: 'Created models/a.json',
        created_at: '2026-06-26T00:00:00Z',
        detail: { path: 'models/a.json' },
      },
    ],
  );

  render(
    <SemanticLayerEditor databaseId={1} catalogName="prod" schemaName="main" />,
    { useRedux: true },
  );

  await userEvent.click(await screen.findByTestId('open-provenance'));

  // The dialog loads and renders the directory's history.
  expect(await screen.findByText('Created models/a.json')).toBeInTheDocument();
});

test('clicking Onboard opens the table picker, then onboards the selection', async () => {
  mockBaseRoutes([]);
  mockOnboard();
  // The picker lists registered datasets from Superset's dataset API.
  fetchMock.get('glob:*/api/v1/dataset/?q=*', {
    result: [
      { id: 1, table_name: 'orders' },
      { id: 2, table_name: 'customers' },
    ],
    count: 2,
  });

  render(
    <SemanticLayerEditor databaseId={1} catalogName="prod" schemaName="main" />,
    { useRedux: true },
  );

  await screen.findByTestId('copilot-onboard');
  await userEvent.click(screen.getByTestId('copilot-onboard'));

  // Picker opens — no onboarding has fired yet (selection is required first).
  await screen.findByText('orders');
  expect(
    fetchMock.callHistory.calls(
      'http://agent.local/agent/semantic-layer/projects/project-1/onboard',
    ),
  ).toHaveLength(0);

  // Select one table and confirm → onboarding runs with that selection.
  const checkboxes = await screen.findAllByTestId('picker-checkbox');
  await userEvent.click(checkboxes[0]);
  await userEvent.click(screen.getByTestId('picker-confirm'));

  await waitFor(() => {
    const calls = fetchMock.callHistory.calls(
      'http://agent.local/agent/semantic-layer/projects/project-1/onboard',
    );
    expect(calls).toHaveLength(1);
    expect(JSON.parse(String(calls[0].options.body))).toEqual({
      mode: 'include',
      dataset_ids: [1],
      exclude_dataset_ids: [],
      search: null,
    });
  });
});

test('registers an unregistered physical table from the picker, then onboards it', async () => {
  mockBaseRoutes([]);
  mockOnboard();
  // One registered dataset…
  fetchMock.get('glob:*/api/v1/dataset/?q=*', {
    result: [{ id: 1, table_name: 'orders' }],
    count: 1,
  });
  // …but the schema physically has a second table that isn't a dataset yet.
  fetchMock.get('glob:*/api/v1/database/*/tables/?q=*', {
    count: 2,
    result: [
      { value: 'orders', type: 'table' },
      { value: 'shipments', type: 'table' },
    ],
  });
  // Registering it returns a new dataset id.
  fetchMock.post('glob:*/api/v1/dataset/', { id: 99 });

  render(
    <SemanticLayerEditor databaseId={1} catalogName="prod" schemaName="main" />,
    { useRedux: true },
  );

  await screen.findByTestId('copilot-onboard');
  await userEvent.click(screen.getByTestId('copilot-onboard'));

  // The unregistered table appears in its own section and is checkable.
  const unreg = await screen.findAllByTestId('picker-unregistered-checkbox');
  expect(unreg).toHaveLength(1);
  await userEvent.click(unreg[0]); // shipments
  await userEvent.click(screen.getByTestId('picker-confirm'));

  // It is registered as a dataset…
  await waitFor(() =>
    expect(fetchMock.callHistory.calls('glob:*/api/v1/dataset/')).toHaveLength(
      1,
    ),
  );
  // …then onboarded by the new dataset id.
  await waitFor(() => {
    const calls = fetchMock.callHistory.calls(
      'http://agent.local/agent/semantic-layer/projects/project-1/onboard',
    );
    expect(calls).toHaveLength(1);
    expect(JSON.parse(String(calls[0].options.body))).toEqual({
      mode: 'include',
      dataset_ids: [99],
      exclude_dataset_ids: [],
      search: null,
    });
  });
});

test('Upload document ingests files through the shared pipeline (no dialog)', async () => {
  mockBaseRoutes([mdlFile('a', 'models/a.json')]);
  mockIngest.mockResolvedValue([
    {
      document: {
        id: 'doc-1',
        filename: 'glossary.csv',
        content_type: 'text/csv',
        size_bytes: 10,
        status: 'extracted',
        scope: { database_id: 1, dataset_ids: [] },
        checksum: 'abc',
        storage_uri: 'mem://x',
        warnings: [],
        created_at: '',
        updated_at: '',
      },
      deduplicated: false,
    },
  ]);

  render(
    <SemanticLayerEditor databaseId={1} catalogName="prod" schemaName="main" />,
    { useRedux: true },
  );

  await waitFor(() => {
    expect(screen.getByText('Database 1.prod.main')).toBeInTheDocument();
  });

  // The button stays, but the legacy staging dialog is gone — Upload now runs the
  // same persist+vectorize pipeline as Copilot Attach, just without a chat.
  expect(
    screen.getByRole('button', { name: /Upload document/i }),
  ).toBeInTheDocument();
  expect(
    screen.queryByTestId('semantic-import-dropzone'),
  ).not.toBeInTheDocument();

  // Choosing a file routes it through the shared ingestion hook.
  await userEvent.upload(
    screen.getByTestId('semantic-upload-input'),
    new File(['revenue'], 'glossary.csv', { type: 'text/csv' }),
  );
  await waitFor(() => expect(mockIngest).toHaveBeenCalledTimes(1));
  expect(mockIngest).toHaveBeenCalledWith([expect.any(File)]);
});

test('Reset confirms, deletes all MDL, and does NOT re-onboard', async () => {
  mockBaseRoutes([mdlFile('a', 'models/a.json')]);
  mockReset();
  mockOnboard();

  render(
    <SemanticLayerEditor databaseId={1} catalogName="prod" schemaName="main" />,
    { useRedux: true },
  );

  await waitFor(() => {
    expect(screen.getByText('Database 1.prod.main')).toBeInTheDocument();
  });

  // Clicking Reset opens a confirmation dialog and does NOT call the endpoint yet.
  await userEvent.click(screen.getByRole('button', { name: /Reset/i }));
  await screen.findByText('Reset semantic layer?');
  expect(
    fetchMock.callHistory.calls(
      'http://agent.local/agent/semantic-layer/projects/project-1/reset',
    ),
  ).toHaveLength(0);

  // Confirming in the dialog fires the reset.
  const dialog = await screen.findByRole('dialog');
  await userEvent.click(
    within(dialog).getByRole('button', { name: /^Reset$/i }),
  );

  await waitFor(() => {
    expect(
      fetchMock.callHistory.calls(
        'http://agent.local/agent/semantic-layer/projects/project-1/reset',
      ),
    ).toHaveLength(1);
  });
  // Reset is delete-only: onboarding is never triggered as a side effect.
  expect(
    fetchMock.callHistory.calls(
      'http://agent.local/agent/semantic-layer/projects/project-1/onboard',
    ),
  ).toHaveLength(0);
});

test(
  'async onboarding stays indexing while the job runs, then unblocks the rail ' +
    'and shows the new models once it completes — no premature success',
  async () => {
    // Reproduces the threaded-backend timeline: the start call returns a still
    // `running` job, the backend finishes later, and the editor must poll the
    // job to completion (not report success early and stop). `onboarded` flips
    // the schema's state once the job reports `completed`, mirroring the backend
    // activating the base models.
    let onboarded = false;
    const activeFile = mdlFile('moves', 'models/moves.json');
    const emptyReadiness = {
      status: 'empty',
      ready: false,
      has_active_models: false,
      active_model_count: 0,
      detail: 'Schema has not been onboarded yet.',
    };

    fetchMock.post(
      'http://agent.local/agent/semantic-layer/projects/resolve',
      project,
    );
    fetchMock.get(
      'http://agent.local/agent/semantic-layer/projects/project-1/mdl-files',
      () => (onboarded ? [activeFile] : []),
    );
    fetchMock.get(
      'http://agent.local/agent/semantic-layer/projects/project-1/readiness',
      () => (onboarded ? readinessFor([activeFile]) : emptyReadiness),
    );
    fetchMock.get(
      'http://agent.local/agent/semantic-layer/projects/project-1/state',
      {
        project_id: 'project-1',
        database_id: 1,
        catalog_name: 'prod',
        schema_name: 'main',
        dataset_ids: [],
        document_count: 0,
        last_error: null,
      },
    );
    // The start call returns the job in `running` (threaded backend), NOT done.
    fetchMock.post(
      'http://agent.local/agent/semantic-layer/projects/project-1/onboard',
      {
        id: 'job-1',
        kind: 'onboarding',
        status: 'running',
        project_id: 'project-1',
        created_at: '2026-06-19T00:00:00Z',
        updated_at: '2026-06-19T00:00:00Z',
      },
    );
    // The background poll: still running on the first tick, completed on the
    // second — and the completion is what makes the schema `ready`.
    let jobPolls = 0;
    fetchMock.get(
      'http://agent.local/agent/semantic-layer/projects/project-1/jobs/job-1',
      () => {
        jobPolls += 1;
        if (jobPolls >= 2) {
          onboarded = true;
          return {
            id: 'job-1',
            kind: 'onboarding',
            status: 'completed',
            project_id: 'project-1',
            created_at: '2026-06-19T00:00:00Z',
            updated_at: '2026-06-19T00:00:00Z',
            result: {
              project_id: 'project-1',
              model_count: 1,
              activated_count: 1,
              warnings: [],
              files: [activeFile],
            },
          };
        }
        return {
          id: 'job-1',
          kind: 'onboarding',
          status: 'running',
          project_id: 'project-1',
          created_at: '2026-06-19T00:00:00Z',
          updated_at: '2026-06-19T00:00:00Z',
        };
      },
    );
    fetchMock.get('glob:*/api/v1/dataset/?q=*', {
      result: [{ id: 1, table_name: 'orders' }],
      count: 1,
    });

    render(
      <SemanticLayerEditor
        databaseId={1}
        catalogName="prod"
        schemaName="main"
      />,
      { useRedux: true },
    );

    // Kick off onboarding through the table picker.
    await userEvent.click(await screen.findByTestId('copilot-onboard'));
    const checkboxes = await screen.findAllByTestId('picker-checkbox');
    await userEvent.click(checkboxes[0]);
    await userEvent.click(screen.getByTestId('picker-confirm'));

    // The job is polled in the background while it runs…
    await waitFor(
      () =>
        expect(
          fetchMock.callHistory.calls(
            'http://agent.local/agent/semantic-layer/projects/project-1/jobs/job-1',
          ).length,
        ).toBeGreaterThanOrEqual(1),
      { timeout: 6000 },
    );
    // …and the rail must remain in the bootstrap (indexing) state — it must NOT
    // flip to the chat composer while the job is still running.
    expect(screen.getByTestId('copilot-not-ready')).toBeInTheDocument();
    expect(screen.queryByTestId('copilot-input')).not.toBeInTheDocument();

    // Once the job completes, the rail self-heals: the Copilot chat mounts and the
    // freshly onboarded model appears in the browser — without a manual reload.
    expect(
      await screen.findByTestId('copilot-input', undefined, { timeout: 6000 }),
    ).toBeInTheDocument();
    expect(screen.queryByTestId('copilot-not-ready')).not.toBeInTheDocument();
    expect(
      await screen.findByText('moves.json', undefined, { timeout: 6000 }),
    ).toBeInTheDocument();
  },
  20000,
);

test(
  'resumes polling a job already in flight on mount (remount/reload) and ' +
    'unblocks the rail when it finishes — no user action needed',
  async () => {
    // Simulates re-opening the editor while a previous onboarding is still
    // running: there is no `pendingJobId` in component state, so the rail must
    // recover from the backend-reported in-flight job (readiness.running_job_id).
    let onboarded = false;
    const activeFile = mdlFile('moves', 'models/moves.json');
    const indexingReadiness = {
      status: 'indexing',
      ready: false,
      has_active_models: false,
      active_model_count: 0,
      running_job_id: 'job-9',
      detail: 'Onboarding in progress; the semantic layer is initializing.',
    };

    fetchMock.post(
      'http://agent.local/agent/semantic-layer/projects/resolve',
      project,
    );
    fetchMock.get(
      'http://agent.local/agent/semantic-layer/projects/project-1/mdl-files',
      () => (onboarded ? [activeFile] : []),
    );
    fetchMock.get(
      'http://agent.local/agent/semantic-layer/projects/project-1/readiness',
      () => (onboarded ? readinessFor([activeFile]) : indexingReadiness),
    );
    fetchMock.get(
      'http://agent.local/agent/semantic-layer/projects/project-1/state',
      {
        project_id: 'project-1',
        database_id: 1,
        catalog_name: 'prod',
        schema_name: 'main',
        dataset_ids: [],
        document_count: 0,
        last_error: null,
      },
    );
    // Observing the job complete flips the schema to onboarded, mirroring the
    // backend having activated the models by the time the job reports `completed`.
    fetchMock.get(
      'http://agent.local/agent/semantic-layer/projects/project-1/jobs/job-9',
      () => {
        onboarded = true;
        return {
          id: 'job-9',
          kind: 'onboarding',
          status: 'completed',
          project_id: 'project-1',
          created_at: '2026-06-19T00:00:00Z',
          updated_at: '2026-06-19T00:00:00Z',
          result: {
            project_id: 'project-1',
            model_count: 1,
            activated_count: 1,
            warnings: [],
            files: [activeFile],
          },
        };
      },
    );

    render(
      <SemanticLayerEditor
        databaseId={1}
        catalogName="prod"
        schemaName="main"
      />,
      { useRedux: true },
    );

    // On mount the rail shows the indexing bootstrap (no chat) — onboarding is
    // still running per the backend.
    expect(await screen.findByTestId('copilot-not-ready')).toBeInTheDocument();
    expect(screen.queryByTestId('copilot-input')).not.toBeInTheDocument();

    // The editor resumes polling the reported job; once it reports complete the
    // schema is onboarded and the rail unblocks without any user interaction.
    await waitFor(
      () =>
        expect(
          fetchMock.callHistory.calls(
            'http://agent.local/agent/semantic-layer/projects/project-1/jobs/job-9',
          ).length,
        ).toBeGreaterThanOrEqual(1),
      { timeout: 6000 },
    );

    expect(
      await screen.findByTestId('copilot-input', undefined, { timeout: 6000 }),
    ).toBeInTheDocument();
    expect(screen.queryByTestId('copilot-not-ready')).not.toBeInTheDocument();
  },
  20000,
);
