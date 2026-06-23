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
  createStore,
  render,
  screen,
  userEvent,
  waitFor,
} from 'spec/helpers/testing-library';
import reducerIndex from 'spec/helpers/reducerIndex';
import SemanticLayerEditor from '.';

interface ToastState {
  messageToasts: { text: string }[];
}

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

const mockBaseRoutes = (files: unknown[] = []) => {
  fetchMock.post(
    'http://agent.local/agent/semantic-layer/projects/resolve',
    project,
  );
  fetchMock.get(
    'http://agent.local/agent/semantic-layer/projects/project-1/mdl-files',
    files,
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
      approved_document_count: 0,
      indexed_document_count: 0,
      semantic_layer_version: null,
      indexing_status: 'idle',
      last_error: null,
    },
  );
};

const mockOnboard = (warnings: string[] = []) => {
  fetchMock.post(
    'http://agent.local/agent/semantic-layer/projects/project-1/onboard',
    {
      id: 'job-1',
      kind: 'onboarding',
      status: 'completed',
      project_id: 'project-1',
      created_at: '2026-06-19T00:00:00Z',
      updated_at: '2026-06-19T00:00:00Z',
      result: {
        project_id: 'project-1',
        model_count: 1,
        warnings,
        files: [mdlFile('moves', 'models/moves.json')],
      },
    },
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

test('eagerly onboards an empty schema and surfaces warnings as a toast', async () => {
  mockBaseRoutes([]);
  mockOnboard(['models/moves.json cannot be activated until fixed: bad']);
  const store = createStore({}, reducerIndex);

  render(
    <SemanticLayerEditor databaseId={1} catalogName="prod" schemaName="main" />,
    { store },
  );

  await waitFor(() => {
    expect(
      fetchMock.callHistory.calls(
        'http://agent.local/agent/semantic-layer/projects/project-1/onboard',
      ),
    ).toHaveLength(1);
  });
  await waitFor(() => {
    const { messageToasts } = store.getState() as unknown as ToastState;
    expect(
      messageToasts.some(toast =>
        toast.text.includes('Onboarding completed with warnings'),
      ),
    ).toBe(true);
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

test('manual Onboard button triggers onboarding', async () => {
  mockBaseRoutes([mdlFile('a', 'models/a.json')]);
  mockOnboard();

  render(
    <SemanticLayerEditor databaseId={1} catalogName="prod" schemaName="main" />,
    { useRedux: true },
  );

  await waitFor(() => {
    expect(screen.getByText('Database 1.prod.main')).toBeInTheDocument();
  });

  await userEvent.click(screen.getByRole('button', { name: /Onboard/i }));

  await waitFor(() => {
    expect(
      fetchMock.callHistory.calls(
        'http://agent.local/agent/semantic-layer/projects/project-1/onboard',
      ),
    ).toHaveLength(1);
  });
});
