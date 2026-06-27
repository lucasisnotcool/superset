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
  fireEvent,
  render,
  screen,
  userEvent,
  waitFor,
} from 'spec/helpers/testing-library';
import { resetAgentHealthCache } from '../api';
import SemanticLayerImportDialog from './SemanticLayerImportDialog';

const XLSX_TYPE =
  'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet';

const originalAgentUrl = process.env.SUPERSET_AI_AGENT_URL;

beforeEach(() => {
  process.env.SUPERSET_AI_AGENT_URL = 'http://agent.local/';
  // The health memo is module-level; clear it so each test sees its own /health
  // mock rather than a value cached by a previous test (RG3).
  resetAgentHealthCache();
  // The dialog reads the upload cap from /health on open; default it so the
  // best-effort fetch resolves cleanly. Individual tests can override.
  fetchMock.get('http://agent.local/health', {
    max_document_bytes: 10_000_000,
  });
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

const makeMdlFile = (overrides: Record<string, unknown> = {}) => ({
  id: 'file-existing',
  project_id: 'project-1',
  path: 'models/model.json',
  filename: 'model.json',
  content: '{"models":[{"name":"model"}]}',
  content_type: 'application/json',
  source_type: 'uploaded_mdl',
  status: 'draft',
  checksum: 'x',
  created_at: '2026-06-19T00:00:00Z',
  updated_at: '2026-06-19T00:00:00Z',
  ...overrides,
});

const renderDialog = (
  props: { existingFiles?: ReturnType<typeof makeMdlFile>[] } = {},
) => {
  const onApplied = jest.fn();
  const onHide = jest.fn();
  render(
    <SemanticLayerImportDialog
      show
      onHide={onHide}
      projectId="project-1"
      existingFiles={(props.existingFiles ?? []) as never}
      canWrite
      onApplied={onApplied}
    />,
  );
  // The modal renders into a portal on document.body, not the render container.
  const fileInput = () =>
    document.querySelector('input[type="file"]') as HTMLInputElement;
  return { onApplied, onHide, fileInput };
};

test('shows the enrichment deprecation notice only once a Markdown file is staged', async () => {
  const { fileInput } = renderDialog();
  // Contextual (G1a): no blanket banner before anything is staged.
  expect(
    screen.queryByTestId('enrichment-deprecation-notice'),
  ).not.toBeInTheDocument();

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
      warnings: [],
      created_at: '2026-06-19T00:00:00Z',
      updated_at: '2026-06-19T00:00:00Z',
    },
  );
  fetchMock.post(
    'http://agent.local/agent/semantic-layer/projects/project-1/documents/document-9/enrich',
    {
      source_document_id: 'document-9',
      proposed_path: 'models/enriched.json',
      proposed_content: '{"models":[{"name":"enriched"}]}',
      validation: { valid: true, messages: [] },
      warnings: [],
    },
  );

  await userEvent.upload(
    fileInput(),
    makeFile('Gross moves glossary', 'glossary.md', 'text/markdown'),
  );

  expect(
    await screen.findByTestId('enrichment-deprecation-notice'),
  ).toBeInTheDocument();
});

test('stages a dropped JSON file as a new MDL draft with a diff', async () => {
  const { fileInput } = renderDialog();
  fetchMock.post(
    'http://agent.local/agent/semantic-layer/projects/project-1/mdl-files',
    {
      id: 'file-new',
      project_id: 'project-1',
      path: 'models/model.json',
      filename: 'model.json',
      content: '{"models":[{"name":"model"}]}',
      content_type: 'application/json',
      source_type: 'uploaded_mdl',
      status: 'draft',
      checksum: 'x',
      created_at: '2026-06-19T00:00:00Z',
      updated_at: '2026-06-19T00:00:00Z',
    },
  );

  await userEvent.upload(
    fileInput(),
    makeFile('{"models":[{"name":"model"}]}', 'model.json', 'application/json'),
  );

  await waitFor(() => {
    expect(screen.getByTestId('semantic-import-item')).toBeInTheDocument();
  });
  expect(screen.getByTestId('semantic-import-diff')).toBeInTheDocument();

  await userEvent.click(screen.getByRole('button', { name: 'Save' }));

  await waitFor(() => {
    const calls = fetchMock.callHistory.calls(
      'http://agent.local/agent/semantic-layer/projects/project-1/mdl-files',
    );
    expect(calls).toHaveLength(1);
    expect(JSON.parse(String(calls[0].options.body))).toMatchObject({
      path: 'models/model.json',
      source_type: 'uploaded_mdl',
    });
  });
});

test('the dialog has no activate controls and uses Save / Save all labels', async () => {
  const { fileInput } = renderDialog();
  fetchMock.post(
    'http://agent.local/agent/semantic-layer/projects/project-1/mdl-files',
    makeMdlFile({ id: 'file-new' }),
  );
  await userEvent.upload(
    fileInput(),
    makeFile('{"models":[{"name":"model"}]}', 'model.json', 'application/json'),
  );
  await screen.findByTestId('semantic-import-item');

  expect(screen.getByRole('button', { name: 'Save' })).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Save all' })).toBeInTheDocument();
  expect(
    screen.queryByRole('button', { name: /Activate/i }),
  ).not.toBeInTheDocument();
  expect(
    screen.queryByRole('button', { name: /Save draft/i }),
  ).not.toBeInTheDocument();
});

test('re-saving the same item updates instead of creating a second file', async () => {
  // Issue 1: after a create, a repeat save routes to an update (PATCH) via the
  // optimistic session map, so no second create (and no "already exists" 409).
  const { fileInput } = renderDialog();
  fetchMock.post(
    'http://agent.local/agent/semantic-layer/projects/project-1/mdl-files',
    makeMdlFile({ id: 'file-new', path: 'models/model.json' }),
  );
  fetchMock.patch(
    'http://agent.local/agent/semantic-layer/projects/project-1/mdl-files/file-new',
    makeMdlFile({ id: 'file-new', path: 'models/model.json' }),
  );

  await userEvent.upload(
    fileInput(),
    makeFile('{"models":[{"name":"model"}]}', 'model.json', 'application/json'),
  );
  await screen.findByTestId('semantic-import-item');

  await userEvent.click(screen.getByRole('button', { name: 'Save' }));
  await waitFor(() =>
    expect(
      fetchMock.callHistory.calls(
        'http://agent.local/agent/semantic-layer/projects/project-1/mdl-files',
      ),
    ).toHaveLength(1),
  );

  await userEvent.click(screen.getByRole('button', { name: 'Save' }));
  await waitFor(() =>
    expect(
      fetchMock.callHistory.calls(
        'http://agent.local/agent/semantic-layer/projects/project-1/mdl-files/file-new',
      ),
    ).toHaveLength(1),
  );
  // Still exactly one create — the second save updated.
  expect(
    fetchMock.callHistory.calls(
      'http://agent.local/agent/semantic-layer/projects/project-1/mdl-files',
    ),
  ).toHaveLength(1);
});

test('a new JSON upload that collides with an existing file is auto-suffixed', async () => {
  // Issue 4: dropping model.json when models/model.json already exists stages a
  // distinct models/model_1.json rather than clobbering or 409-ing.
  const { fileInput } = renderDialog({
    existingFiles: [makeMdlFile({ path: 'models/model.json' })],
  });

  await userEvent.upload(
    fileInput(),
    makeFile('{"models":[{"name":"model"}]}', 'model.json', 'application/json'),
  );

  const item = await screen.findByTestId('semantic-import-item');
  expect(item).toHaveTextContent('models/model_1.json');
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
      warnings: [],
      created_at: '2026-06-19T00:00:00Z',
      updated_at: '2026-06-19T00:00:00Z',
    },
  );
  fetchMock.post(
    'http://agent.local/agent/semantic-layer/projects/project-1/documents/document-9/enrich',
    {
      source_document_id: 'document-9',
      proposed_path: 'models/enriched.json',
      proposed_content: '{"models":[{"name":"enriched"}]}',
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

const DOCUMENTS_URL =
  'http://agent.local/agent/semantic-layer/projects/project-1/documents';

const makeDocResponse = (overrides: Record<string, unknown> = {}) => ({
  id: 'document-x',
  project_id: 'project-1',
  filename: 'metrics.xlsx',
  content_type:
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  size_bytes: 10,
  status: 'extracted',
  scope: { database_id: 1, schema_name: 'main', dataset_ids: [] },
  checksum: 'c',
  storage_uri: 'mem://metrics.xlsx',
  warnings: [],
  created_at: '2026-06-19T00:00:00Z',
  updated_at: '2026-06-19T00:00:00Z',
  ...overrides,
});

test('uploads a dropped Excel file as a source document and refreshes', async () => {
  const { fileInput, onApplied } = renderDialog();
  fetchMock.post(DOCUMENTS_URL, makeDocResponse());

  await userEvent.upload(
    fileInput(),
    makeFile(
      'binary',
      'metrics.xlsx',
      'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    ),
  );

  const item = await screen.findByTestId('semantic-import-item');
  // Routed to the multipart source-document endpoint, not MDL / enrichment.
  await waitFor(() =>
    expect(fetchMock.callHistory.calls(DOCUMENTS_URL)).toHaveLength(1),
  );
  expect(item).toHaveTextContent('(document)');
  expect(screen.getByText('Extracted')).toBeInTheDocument();
  // The new doc is surfaced in the workspace (raw/) via a refresh.
  await waitFor(() => expect(onApplied).toHaveBeenCalled());
  // Already persisted — no per-item Save / no diff.
  expect(
    screen.queryByRole('button', { name: 'Save' }),
  ).not.toBeInTheDocument();
  expect(screen.queryByTestId('semantic-import-diff')).not.toBeInTheDocument();
});

test('flags an uploaded image-only PDF as needs_ocr', async () => {
  const { fileInput } = renderDialog();
  fetchMock.post(
    DOCUMENTS_URL,
    makeDocResponse({
      id: 'doc-scan',
      filename: 'scan.pdf',
      content_type: 'application/pdf',
      status: 'needs_ocr',
    }),
  );

  await userEvent.upload(
    fileInput(),
    makeFile('%PDF-1.4 scan', 'scan.pdf', 'application/pdf'),
  );

  expect(await screen.findByText('Needs OCR')).toBeInTheDocument();
});

test('rejects an oversized file before any upload (G2)', async () => {
  const { fileInput } = renderDialog();
  fetchMock.post(DOCUMENTS_URL, makeDocResponse());

  const big = makeFile('x', 'big.pdf', 'application/pdf');
  // Fake a size over the 10 MB default cap without allocating 10 MB.
  Object.defineProperty(big, 'size', { value: 10_000_001 });
  await userEvent.upload(fileInput(), big);

  const item = await screen.findByTestId('semantic-import-item');
  expect(item).toHaveTextContent(/too large/i);
  // No upload round-trip for a file rejected client-side.
  expect(fetchMock.callHistory.calls(DOCUMENTS_URL)).toHaveLength(0);
});

test('honors the server-reported cap from /health, not just the default (G2)', async () => {
  // Operator-tuned cap smaller than the FE default proves the guard tracks the
  // backend rather than a hard-coded constant.
  fetchMock.removeRoutes();
  fetchMock.get('http://agent.local/health', { max_document_bytes: 100 });
  fetchMock.post(DOCUMENTS_URL, makeDocResponse());
  const { fileInput } = renderDialog();
  await waitFor(() =>
    expect(
      fetchMock.callHistory.calls('http://agent.local/health'),
    ).toHaveLength(1),
  );

  const file = makeFile('x', 'metrics.xlsx', XLSX_TYPE);
  // 200 bytes: under the 10 MB default but over the live 100-byte cap.
  Object.defineProperty(file, 'size', { value: 200 });
  await userEvent.upload(fileInput(), file);

  const item = await screen.findByTestId('semantic-import-item');
  // The message quotes the live cap (100 B), not the default (9.5 MB).
  expect(item).toHaveTextContent('100 B');
  expect(fetchMock.callHistory.calls(DOCUMENTS_URL)).toHaveLength(0);
});

test('drag-drop rejects unsupported files like the picker (G3)', async () => {
  renderDialog();
  fetchMock.post(DOCUMENTS_URL, makeDocResponse());

  const dropzone = screen.getByTestId('semantic-import-dropzone');
  fireEvent.drop(dropzone, {
    dataTransfer: {
      files: [
        makeFile('binary', 'metrics.xlsx', XLSX_TYPE),
        makeFile('img', 'logo.png', 'image/png'),
      ],
    },
  });

  // The unsupported .png is skipped with a single message...
  expect(
    await screen.findByText(/Skipped 1 unsupported file/i),
  ).toBeInTheDocument();
  // ...and only the accepted .xlsx is staged (no per-file error row for the png).
  const items = await screen.findAllByTestId('semantic-import-item');
  expect(items).toHaveLength(1);
  expect(items[0]).toHaveTextContent('metrics.xlsx');
});

test('a documents-only batch shows Done (not Save all) and just closes (G4)', async () => {
  const { fileInput, onHide } = renderDialog();
  fetchMock.post(DOCUMENTS_URL, makeDocResponse());

  await userEvent.upload(
    fileInput(),
    makeFile('binary', 'metrics.xlsx', XLSX_TYPE),
  );
  await screen.findByTestId('semantic-import-item');

  // Documents are already persisted -> nothing to "Save".
  expect(
    screen.queryByRole('button', { name: 'Save all' }),
  ).not.toBeInTheDocument();
  const done = screen.getByRole('button', { name: 'Done' });
  await userEvent.click(done);

  expect(onHide).toHaveBeenCalled();
  // "Done" must not POST any MDL file.
  expect(
    fetchMock.callHistory.calls(
      'http://agent.local/agent/semantic-layer/projects/project-1/mdl-files',
    ),
  ).toHaveLength(0);
});

test('surfaces enrichment proposal warnings (provider fallback)', async () => {
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
      warnings: [],
      created_at: '2026-06-19T00:00:00Z',
      updated_at: '2026-06-19T00:00:00Z',
    },
  );
  fetchMock.post(
    'http://agent.local/agent/semantic-layer/projects/project-1/documents/document-9/enrich',
    {
      source_document_id: 'document-9',
      proposed_path: 'models/enriched.json',
      proposed_content: '{"models":[{"name":"enriched"}]}',
      validation: { valid: true, messages: [] },
      warnings: [
        'The model did not return a valid structured MDL proposal, so a ' +
          'deterministic draft is shown.',
      ],
    },
  );

  await userEvent.upload(
    fileInput(),
    makeFile('Gross moves glossary', 'glossary.md', 'text/markdown'),
  );

  // F5: the degradation note reaches the user instead of being dropped.
  const warning = await screen.findByTestId('semantic-import-warnings');
  expect(warning).toHaveTextContent('did not return a valid structured');
});
