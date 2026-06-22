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
import { render, screen, userEvent, waitFor } from 'spec/helpers/testing-library';
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

test('resolves the project for the given scope and uploads a document', async () => {
  fetchMock.post(
    'http://agent.local/agent/semantic-layer/projects/resolve',
    project,
  );
  fetchMock.get(
    'http://agent.local/agent/semantic-layer/projects/project-1/mdl-files',
    [],
  );
  fetchMock.get(
    'http://agent.local/agent/semantic-layer/documents?database_id=1&schema_name=main&catalog_name=prod',
    [],
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
  fetchMock.post(
    'http://agent.local/agent/semantic-layer/projects/project-1/documents',
    {
      id: 'document-1',
      project_id: 'project-1',
      filename: 'notes.md',
      content_type: 'text/markdown',
      size_bytes: 12,
      status: 'needs_review',
      scope: {
        database_id: 1,
        catalog_name: 'prod',
        schema_name: 'main',
        dataset_ids: [],
      },
      checksum: 'abc',
      storage_uri: 'file:///tmp/notes.md',
      proposed_updates: [],
      warnings: [],
      created_at: '2026-06-19T00:00:00Z',
      updated_at: '2026-06-19T00:00:00Z',
    },
  );
  fetchMock.post(
    'http://agent.local/agent/semantic-layer/projects/project-1/documents/document-1/enrich',
    {
      source_document_id: 'document-1',
      proposed_path: 'models/notes.yaml',
      proposed_yaml: 'models:\n  - name: notes\n',
      validation: { valid: true, messages: [] },
      warnings: [],
    },
  );

  const { container } = render(
    <SemanticLayerEditor databaseId={1} catalogName="prod" schemaName="main" />,
  );

  await waitFor(() => {
    expect(screen.getByText('Database 1.prod.main')).toBeInTheDocument();
  });

  const [resolveCall] = fetchMock.callHistory.calls(
    'http://agent.local/agent/semantic-layer/projects/resolve',
  );
  expect(JSON.parse(String(resolveCall.options.body))).toMatchObject({
    database_id: 1,
    catalog_name: 'prod',
    schema_name: 'main',
    create_if_missing: true,
  });

  const inputs =
    container.querySelectorAll<HTMLInputElement>('input[type="file"]');
  expect(inputs).toHaveLength(2);
  await userEvent.upload(
    inputs[1],
    new File(['Metric gross_moves = count moves'], 'notes.md', {
      type: 'text/markdown',
    }),
  );

  await waitFor(() => {
    expect(
      fetchMock.callHistory.calls(
        'http://agent.local/agent/semantic-layer/projects/project-1/documents',
      ),
    ).toHaveLength(1);
  });
});
