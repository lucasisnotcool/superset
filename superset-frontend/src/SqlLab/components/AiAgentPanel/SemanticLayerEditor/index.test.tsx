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

const originalAgentUrl = process.env.SUPERSET_AI_AGENT_URL;

beforeEach(() => {
  process.env.SUPERSET_AI_AGENT_URL = 'http://agent.local/';
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

test('clicking Onboard from the empty state starts onboarding', async () => {
  mockBaseRoutes([]);
  mockOnboard();

  render(
    <SemanticLayerEditor databaseId={1} catalogName="prod" schemaName="main" />,
    { useRedux: true },
  );

  await screen.findByTestId('copilot-onboard');
  await userEvent.click(screen.getByTestId('copilot-onboard'));

  await waitFor(() => {
    expect(
      fetchMock.callHistory.calls(
        'http://agent.local/agent/semantic-layer/projects/project-1/onboard',
      ),
    ).toHaveLength(1);
  });
});

test('opens the Add dialog with a drop zone', async () => {
  mockBaseRoutes([mdlFile('a', 'models/a.json')]);

  render(
    <SemanticLayerEditor databaseId={1} catalogName="prod" schemaName="main" />,
    { useRedux: true },
  );

  await waitFor(() => {
    expect(screen.getByText('Database 1.prod.main')).toBeInTheDocument();
  });

  await userEvent.click(screen.getByRole('button', { name: /Add/i }));

  await waitFor(() => {
    expect(screen.getByTestId('semantic-import-dropzone')).toBeInTheDocument();
  });
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
