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
import SemanticLayerImportDialog from './SemanticLayerImportDialog';

const originalAgentUrl = process.env.SUPERSET_AI_AGENT_URL;

beforeEach(() => {
  process.env.SUPERSET_AI_AGENT_URL = 'http://agent.local/';
});

afterEach(() => {
  process.env.SUPERSET_AI_AGENT_URL = originalAgentUrl;
  fetchMock.clearHistory().removeRoutes();
});

// jsdom does not implement Blob/File.prototype.text(); provide it per file.
const makeFile = (content: string, name: string, type: string) => {
  const file = new File([content], name, { type });
  Object.defineProperty(file, 'text', {
    value: () => Promise.resolve(content),
  });
  return file;
};

const renderDialog = () => {
  const onApplied = jest.fn();
  render(
    <SemanticLayerImportDialog
      show
      onHide={jest.fn()}
      projectId="project-1"
      existingFiles={[]}
      canWrite
      onApplied={onApplied}
    />,
  );
  // The modal renders into a portal on document.body, not the render container.
  const fileInput = () =>
    document.querySelector('input[type="file"]') as HTMLInputElement;
  return { onApplied, fileInput };
};

test('stages a dropped YAML file as a new MDL draft with a diff', async () => {
  const { fileInput } = renderDialog();
  fetchMock.post('http://agent.local/agent/semantic-layer/projects/project-1/mdl-files', {
    id: 'file-new',
    project_id: 'project-1',
    path: 'models/model.yaml',
    filename: 'model.yaml',
    content: 'models:\n  - name: model\n',
    content_type: 'application/x-yaml',
    source_type: 'uploaded_mdl',
    status: 'draft',
    checksum: 'x',
    created_at: '2026-06-19T00:00:00Z',
    updated_at: '2026-06-19T00:00:00Z',
  });

  await userEvent.upload(
    fileInput(),
    makeFile('models:\n  - name: model\n', 'model.yaml', 'text/yaml'),
  );

  await waitFor(() => {
    expect(screen.getByTestId('semantic-import-item')).toBeInTheDocument();
  });
  expect(screen.getByTestId('semantic-import-diff')).toBeInTheDocument();

  await userEvent.click(screen.getByRole('button', { name: /Save draft/i }));

  await waitFor(() => {
    const calls = fetchMock.callHistory.calls(
      'http://agent.local/agent/semantic-layer/projects/project-1/mdl-files',
    );
    expect(calls).toHaveLength(1);
    expect(JSON.parse(String(calls[0].options.body))).toMatchObject({
      path: 'models/model.yaml',
      source_type: 'uploaded_mdl',
    });
  });
});

test('routes a dropped Markdown file through the enrichment pipeline', async () => {
  const { fileInput } = renderDialog();
  fetchMock.post(
    'http://agent.local/agent/semantic-layer/projects/project-1/documents/text',
    {
      id: 'document-9',
      project_id: 'project-1',
      filename: 'glossary.md',
      content_type: 'text/markdown',
      size_bytes: 5,
      status: 'needs_review',
      scope: { database_id: 1, schema_name: 'main', dataset_ids: [] },
      checksum: 'c',
      storage_uri: 'mem://glossary.md',
      proposed_updates: [],
      warnings: [],
      created_at: '2026-06-19T00:00:00Z',
      updated_at: '2026-06-19T00:00:00Z',
    },
  );
  fetchMock.post(
    'http://agent.local/agent/semantic-layer/projects/project-1/documents/document-9/enrich',
    {
      source_document_id: 'document-9',
      proposed_path: 'models/enriched.yaml',
      proposed_yaml: 'models:\n  - name: enriched\n',
      validation: { valid: true, messages: [] },
      warnings: [],
    },
  );

  await userEvent.upload(
    fileInput(),
    makeFile('Gross moves glossary', 'glossary.md', 'text/markdown'),
  );

  await waitFor(() => {
    expect(
      fetchMock.callHistory.calls(
        'http://agent.local/agent/semantic-layer/projects/project-1/documents/text',
      ),
    ).toHaveLength(1);
  });
  await waitFor(() => {
    expect(
      fetchMock.callHistory.calls(
        'http://agent.local/agent/semantic-layer/projects/project-1/documents/document-9/enrich',
      ),
    ).toHaveLength(1);
  });
});
