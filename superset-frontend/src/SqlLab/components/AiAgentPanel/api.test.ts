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
import { SupersetClient } from '@superset-ui/core';
import {
  applyCopilotChangeset,
  createDataset,
  getDatasetWritePermission,
  listAllRegisteredTableNames,
  listPhysicalTables,
  onboardSemanticProject,
  getSemanticJob,
  getMdlProvenance,
  createCopilotConversation,
  deleteCopilotConversation,
  getCopilotConversation,
  getCopilotDeployPreview,
  getCopilotInspector,
  listCopilotConversations,
  updateCopilotConversationTitle,
  getProjectReadiness,
  getProjectWorkspace,
  runCopilot,
  runCoverage,
  createMdlFile,
  createConversation,
  createSemanticLayerEventSource,
  createProjectSemanticLayerEventSource,
  deleteMdlFile,
  enrichProjectDocument,
  getSemanticLayerState,
  deleteConversation,
  executeConversationSql,
  getAgentBaseUrl,
  getAgentHealth,
  getAgentHealthCached,
  resetAgentHealthCache,
  getConversation,
  listMdlFiles,
  listConversations,
  listSemanticDocuments,
  materializeSemanticProject,
  queryAgent,
  resolveSemanticProject,
  sendConversationMessage,
  updateMdlFile,
  uploadMdlFile,
  uploadProjectSourceDocument,
  uploadSemanticDocument,
  validateSql,
} from './api';

const originalAgentUrl = process.env.SUPERSET_AI_AGENT_URL;

beforeEach(() => {
  process.env.SUPERSET_AI_AGENT_URL = 'http://agent.local/';
});

afterEach(() => {
  process.env.SUPERSET_AI_AGENT_URL = originalAgentUrl;
  fetchMock.clearHistory().removeRoutes();
});

test('getAgentBaseUrl trims trailing slash', () => {
  expect(getAgentBaseUrl()).toBe('http://agent.local');
});

test('getAgentHealth requests health endpoint', async () => {
  fetchMock.get('http://agent.local/health', {
    status: 'ok',
    model_provider: 'ollama',
    base_url: 'http://localhost:11434',
    default_model: 'qwen2.5-coder:7b',
    reachable: true,
  });

  const response = await getAgentHealth();

  expect(response.reachable).toBe(true);
  expect(response.model_provider).toBe('ollama');
  expect(fetchMock.callHistory.calls('http://agent.local/health')).toHaveLength(
    1,
  );
});

test('getAgentHealthCached dedupes within the window and refetches after (RG3)', async () => {
  resetAgentHealthCache();
  fetchMock.get('http://agent.local/health', {
    status: 'ok',
    model_provider: 'ollama',
    base_url: 'http://localhost:11434',
    default_model: 'qwen2.5-coder:7b',
    reachable: true,
    max_document_bytes: 5_000_000,
  });

  const first = await getAgentHealthCached();
  const second = await getAgentHealthCached();
  expect(first?.max_document_bytes).toBe(5_000_000);
  expect(second?.max_document_bytes).toBe(5_000_000);
  // Two reads, one network call (cached within the window).
  expect(fetchMock.callHistory.calls('http://agent.local/health')).toHaveLength(
    1,
  );

  // maxAgeMs=0 forces the cache to be treated as stale -> a refetch.
  await getAgentHealthCached(0);
  expect(fetchMock.callHistory.calls('http://agent.local/health')).toHaveLength(
    2,
  );
});

test('getAgentHealthCached returns null on failure without throwing (RG4)', async () => {
  resetAgentHealthCache();
  fetchMock.get('http://agent.local/health', 500);

  const result = await getAgentHealthCached();
  // Degrade-closed: best-effort, never rejects; callers use their default.
  expect(result).toBeNull();
});

test('queryAgent posts typed payload to agent backend', async () => {
  fetchMock.post('http://agent.local/agent/query', {
    status: 'needs_review',
    sql: 'select 1',
    explanation: 'Returns one row.',
    validation: {
      is_valid: true,
      is_read_only: true,
      normalized_sql: 'select 1',
      dialect: 'sqlite',
      errors: [],
    },
    trace: [],
  });

  const response = await queryAgent({
    question: 'show one',
    database_id: 1,
    catalog_name: 'prod',
    schema_name: null,
    dataset_ids: [16],
    execute: false,
  });

  const [call] = fetchMock.callHistory.calls('http://agent.local/agent/query');
  expect(response.sql).toBe('select 1');
  expect(call.options.credentials).toBe('include');
  expect(JSON.parse(String(call.options.body))).toEqual({
    question: 'show one',
    database_id: 1,
    catalog_name: 'prod',
    schema_name: null,
    dataset_ids: [16],
    execute: false,
  });
});

test('validateSql posts SQL validation payload', async () => {
  fetchMock.post('http://agent.local/agent/validate-sql', {
    is_valid: true,
    is_read_only: true,
    normalized_sql: 'select 1',
    dialect: 'sqlite',
    errors: [],
  });

  const response = await validateSql('select 1', 'sqlite');

  const [call] = fetchMock.callHistory.calls(
    'http://agent.local/agent/validate-sql',
  );
  expect(response.is_valid).toBe(true);
  expect(call.options.credentials).toBe('include');
  expect(JSON.parse(String(call.options.body))).toEqual({
    sql: 'select 1',
    dialect: 'sqlite',
  });
});

test('validateSql surfaces the policy classification and reason', async () => {
  fetchMock.post('http://agent.local/agent/validate-sql', {
    is_valid: false,
    is_read_only: false,
    classification: 'mutating',
    reason: 'Statement writes data or changes server state.',
    normalized_sql: null,
    dialect: 'sqlite',
    errors: ['Statement writes data or changes server state.'],
  });

  const response = await validateSql('delete from t', 'sqlite');

  expect(response.is_valid).toBe(false);
  expect(response.classification).toBe('mutating');
  expect(response.reason).toBe(
    'Statement writes data or changes server state.',
  );
});

test('conversation API helpers use typed conversation endpoints', async () => {
  const conversation = {
    id: 'conversation-1',
    title: 'Show top names',
    owner_id: 'local',
    scope: {
      database_id: 1,
      schema_name: null,
      dataset_ids: [16],
      query_editor_id: 'editor-1',
      current_sql: 'select 1',
      selected_text: null,
    },
    messages: [],
    created_at: '2026-06-19T00:00:00Z',
    updated_at: '2026-06-19T00:00:00Z',
  };
  fetchMock.post('http://agent.local/agent/conversations', conversation);
  fetchMock.get('http://agent.local/agent/conversations', [
    {
      id: 'conversation-1',
      title: 'Show top names',
      owner_id: 'local',
      database_id: 1,
      schema_name: null,
      updated_at: '2026-06-19T00:00:00Z',
      last_message: null,
    },
  ]);
  fetchMock.get(
    'http://agent.local/agent/conversations/conversation-1',
    conversation,
  );
  fetchMock.post(
    'http://agent.local/agent/conversations/conversation-1/messages',
    {
      status: 'ok',
      conversation_id: 'conversation-1',
      message: {
        id: 'message-2',
        role: 'assistant',
        content: 'Answer',
        created_at: '2026-06-19T00:00:00Z',
        artifacts: [],
      },
      artifacts: [],
      trace: [],
      conversation,
    },
  );
  fetchMock.delete('http://agent.local/agent/conversations/conversation-1', {
    deleted: true,
  });
  fetchMock.post(
    'http://agent.local/agent/conversations/conversation-1/execute-sql',
    {
      status: 'ok',
      conversation_id: 'conversation-1',
      message: {
        id: 'message-3',
        role: 'assistant',
        content: 'Executed',
        created_at: '2026-06-19T00:00:00Z',
        artifacts: [],
      },
      artifacts: [],
      trace: [],
      conversation,
    },
  );

  const scope = {
    database_id: 1,
    schema_name: null,
    dataset_ids: [16],
    query_editor_id: 'editor-1',
    current_sql: 'select 1',
    selected_text: null,
  };

  expect((await createConversation(scope)).id).toBe('conversation-1');
  expect(await listConversations()).toHaveLength(1);
  expect((await getConversation('conversation-1')).title).toBe(
    'Show top names',
  );
  expect(
    (
      await sendConversationMessage('conversation-1', {
        message: 'What columns?',
        scope,
        execution_mode: 'manual',
      })
    ).message.content,
  ).toBe('Answer');
  expect(
    (
      await executeConversationSql('conversation-1', {
        sql: 'select 1',
        scope,
        execution_mode: 'manual',
        artifact_id: 'artifact-1',
      })
    ).message.content,
  ).toBe('Executed');
  expect((await deleteConversation('conversation-1')).deleted).toBe(true);

  const [messageCall] = fetchMock.callHistory.calls(
    'http://agent.local/agent/conversations/conversation-1/messages',
  );
  expect(messageCall.options.credentials).toBe('include');
  expect(JSON.parse(String(messageCall.options.body))).toEqual({
    message: 'What columns?',
    scope,
    execution_mode: 'manual',
  });
  const [executeCall] = fetchMock.callHistory.calls(
    'http://agent.local/agent/conversations/conversation-1/execute-sql',
  );
  expect(executeCall.options.credentials).toBe('include');
  expect(JSON.parse(String(executeCall.options.body))).toEqual({
    sql: 'select 1',
    scope,
    execution_mode: 'manual',
    artifact_id: 'artifact-1',
  });
});

test('semantic-layer API helpers use typed document endpoints', async () => {
  const scope = {
    database_id: 1,
    schema_name: null,
    dataset_ids: [16],
  };
  const document = {
    id: 'document-1',
    filename: 'notes.md',
    content_type: 'text/markdown',
    size_bytes: 12,
    status: 'extracted',
    scope,
    checksum: 'abc',
    storage_uri: 'file:///tmp/notes.md',
    warnings: [],
    created_at: '2026-06-19T00:00:00Z',
    updated_at: '2026-06-19T00:00:00Z',
  };
  fetchMock.post('http://agent.local/agent/semantic-layer/documents', document);
  fetchMock.get(
    'http://agent.local/agent/semantic-layer/documents?database_id=1&dataset_ids=16',
    [document],
  );
  fetchMock.get(
    'http://agent.local/agent/semantic-layer/state?database_id=1&dataset_ids=16',
    {
      database_id: 1,
      schema_name: null,
      dataset_ids: [16],
      document_count: 1,
      last_error: null,
    },
  );

  expect(
    (
      await uploadSemanticDocument(
        scope,
        new File(['notes'], 'notes.md', { type: 'text/markdown' }),
      )
    ).id,
  ).toBe('document-1');
  expect(await listSemanticDocuments(scope)).toHaveLength(1);
  expect((await getSemanticLayerState(scope)).document_count).toBe(1);
  const [uploadCall] = fetchMock.callHistory.calls(
    'http://agent.local/agent/semantic-layer/documents',
  );
  expect(uploadCall.options.credentials).toBe('include');
});

test('semantic project API helpers use project and MDL endpoints', async () => {
  const project = {
    id: 'project-1',
    name: 'Sales.prod.pipeline',
    owner_id: 'owner',
    database_uri_fingerprint: 'fingerprint',
    database_label: 'Sales',
    catalog_name: 'prod',
    schema_name: 'pipeline',
    default_database_id: 1,
    visibility: 'db_access',
    status: 'active',
    permission: 'admin',
    created_at: '2026-06-19T00:00:00Z',
    updated_at: '2026-06-19T00:00:00Z',
  };
  const mdlFile = {
    id: 'file-1',
    project_id: 'project-1',
    path: 'models/gross_moves.json',
    filename: 'gross_moves.json',
    content: '{"models":[{"name":"gross_moves"}]}',
    content_type: 'application/json',
    source_type: 'manual',
    status: 'draft',
    validation: { valid: true, messages: [] },
    checksum: 'checksum',
    created_at: '2026-06-19T00:00:00Z',
    updated_at: '2026-06-19T00:00:00Z',
  };
  const document = {
    id: 'document-1',
    project_id: 'project-1',
    filename: 'notes.md',
    content_type: 'text/markdown',
    size_bytes: 12,
    status: 'extracted',
    scope: {
      database_id: 1,
      catalog_name: 'prod',
      schema_name: 'pipeline',
      dataset_ids: [],
    },
    checksum: 'abc',
    storage_uri: 'file:///tmp/notes.md',
    warnings: [],
    created_at: '2026-06-19T00:00:00Z',
    updated_at: '2026-06-19T00:00:00Z',
  };
  fetchMock.post(
    'http://agent.local/agent/semantic-layer/projects/resolve',
    project,
  );
  fetchMock.get(
    'http://agent.local/agent/semantic-layer/projects/project-1/mdl-files',
    [mdlFile],
  );
  fetchMock.post(
    'http://agent.local/agent/semantic-layer/projects/project-1/mdl-files',
    mdlFile,
  );
  fetchMock.patch(
    'http://agent.local/agent/semantic-layer/projects/project-1/mdl-files/file-1',
    { ...mdlFile, status: 'active' },
  );
  fetchMock.delete(
    'http://agent.local/agent/semantic-layer/projects/project-1/mdl-files/file-1',
    { deleted: true },
  );
  fetchMock.post(
    'http://agent.local/agent/semantic-layer/projects/project-1/mdl-files/upload',
    mdlFile,
  );
  fetchMock.post(
    'http://agent.local/agent/semantic-layer/projects/project-1/documents',
    document,
  );
  fetchMock.post(
    'http://agent.local/agent/semantic-layer/projects/project-1/documents/document-1/enrich',
    {
      source_document_id: 'document-1',
      proposed_path: 'models/gross_moves.json',
      proposed_content: '{"models":[{"name":"gross_moves"}]}',
      validation: { valid: true, messages: [] },
      warnings: [],
    },
  );
  fetchMock.post(
    'http://agent.local/agent/semantic-layer/projects/project-1/materialize',
    {
      project_id: 'project-1',
      path: '/tmp/wren/project-1/mdl.json',
      file_count: 1,
      checksum: 'checksum',
    },
  );

  expect(
    (
      await resolveSemanticProject({
        database_id: 1,
        catalog_name: 'prod',
        schema_name: 'pipeline',
      })
    ).id,
  ).toBe('project-1');
  expect(await listMdlFiles('project-1')).toHaveLength(1);
  expect(
    (
      await createMdlFile('project-1', {
        path: 'models/gross_moves.json',
        content: '{"models":[{"name":"gross_moves"}]}',
      })
    ).id,
  ).toBe('file-1');
  expect(
    (
      await updateMdlFile('project-1', 'file-1', {
        status: 'active',
      })
    ).status,
  ).toBe('active');
  expect((await deleteMdlFile('project-1', 'file-1')).deleted).toBe(true);
  expect(
    (
      await uploadMdlFile(
        'project-1',
        new File(['{"models":[]}'], 'mdl.json', { type: 'application/json' }),
      )
    ).id,
  ).toBe('file-1');
  expect(
    (
      await uploadProjectSourceDocument(
        'project-1',
        new File(['notes'], 'notes.md', { type: 'text/markdown' }),
      )
    ).project_id,
  ).toBe('project-1');
  expect(
    (await enrichProjectDocument('project-1', 'document-1')).proposed_path,
  ).toBe('models/gross_moves.json');
  expect((await materializeSemanticProject('project-1')).file_count).toBe(1);

  const [resolveCall] = fetchMock.callHistory.calls(
    'http://agent.local/agent/semantic-layer/projects/resolve',
  );
  expect(resolveCall.options.credentials).toBe('include');
  expect(JSON.parse(String(resolveCall.options.body))).toEqual({
    database_id: 1,
    catalog_name: 'prod',
    schema_name: 'pipeline',
  });
});

test('MDL Copilot helpers call workspace, run, apply, and inspector', async () => {
  const base = 'http://agent.local/agent/semantic-layer/projects/project-1';
  fetchMock.get(`${base}/workspace`, {
    path: '',
    name: 'workspace',
    kind: 'folder',
    editable: false,
    children: [
      {
        path: 'models',
        name: 'models',
        kind: 'folder',
        editable: false,
        children: [],
      },
    ],
  });
  fetchMock.post(`${base}/copilot`, {
    items: [
      {
        op: 'create',
        path: 'models/orders.json',
        proposed_content: '{"models":[]}',
        summary: 'Add orders',
      },
    ],
    manifest_validation: { valid: true, messages: [] },
    warnings: [],
    steps: [],
    message: 'Created orders.',
  });
  fetchMock.post(`${base}/copilot/apply`, [
    {
      id: 'file-1',
      project_id: 'project-1',
      path: 'models/orders.json',
      filename: 'orders.json',
      content: '{"models":[]}',
      content_type: 'application/json',
      source_type: 'copilot',
      status: 'draft',
      checksum: 'c',
      created_at: '2026-06-19T00:00:00Z',
      updated_at: '2026-06-19T00:00:00Z',
    },
  ]);
  fetchMock.get(`${base}/copilot/inspector`, {
    system_prompt: 'You are MDL Copilot.',
    skills: [{ name: 'generate-mdl', text: '...' }],
    tools: [{ name: 'write_mdl_file', description: '...' }],
    instructions: [],
  });

  const workspace = await getProjectWorkspace('project-1');
  expect(workspace.children[0].name).toBe('models');

  const changeset = await runCopilot('project-1', { message: 'model orders' });
  expect(changeset.items[0].op).toBe('create');
  expect(changeset.message).toBe('Created orders.');

  const applied = await applyCopilotChangeset('project-1', changeset.items);
  expect(applied[0].source_type).toBe('copilot');

  const inspector = await getCopilotInspector('project-1');
  expect(inspector.tools[0].name).toBe('write_mdl_file');

  fetchMock.get(`${base}/copilot/deploy-preview`, {
    items: [{ op: 'create', path: 'models/orders.json', summary: 'Activate' }],
    manifest_validation: { valid: true, messages: [] },
    warnings: [],
    steps: [],
    message: '1 draft(s) would be activated.',
  });
  const preview = await getCopilotDeployPreview('project-1');
  expect(preview.items[0].path).toBe('models/orders.json');

  fetchMock.post(`${base}/copilot/coverage`, {
    document_id: 'doc-1',
    document_filename: 'glossary.md',
    findings: [],
    total: 3,
    covered: 2,
    partial: 0,
    missing: 1,
    score: 0.667,
    warnings: [],
  });
  const coverage = await runCoverage('project-1', 'doc-1');
  expect(coverage.missing).toBe(1);
  const coverageCall = fetchMock.callHistory.calls(
    `${base}/copilot/coverage`,
  )[0];
  expect(JSON.parse(String(coverageCall.options.body))).toEqual({
    document_id: 'doc-1',
    include_overreach: false,
  });

  const [runCall] = fetchMock.callHistory.calls(`${base}/copilot`);
  expect(JSON.parse(String(runCall.options.body))).toEqual({
    message: 'model orders',
  });
  const [applyCall] = fetchMock.callHistory.calls(`${base}/copilot/apply`);
  expect(JSON.parse(String(applyCall.options.body)).items).toHaveLength(1);
});

test('Copilot conversation helpers use project-scoped thread endpoints', async () => {
  const base =
    'http://agent.local/agent/semantic-layer/projects/project-1/copilot/conversations';
  const thread = {
    id: 'conv-1',
    title: 'New chat',
    owner_id: 'local',
    kind: 'copilot',
    project_id: 'project-1',
    scope: { database_id: 1, dataset_ids: [] },
    messages: [],
    created_at: '2026-06-19T00:00:00Z',
    updated_at: '2026-06-19T00:00:00Z',
  };
  fetchMock.post(base, thread);
  fetchMock.get(base, [
    {
      id: 'conv-1',
      title: 'New chat',
      owner_id: 'local',
      kind: 'copilot',
      project_id: 'project-1',
      database_id: 1,
      updated_at: '2026-06-19T00:00:00Z',
      last_message: null,
    },
  ]);
  fetchMock.get(`${base}/conv-1`, thread);
  fetchMock.patch(`${base}/conv-1`, { ...thread, title: 'Orders' });
  fetchMock.delete(`${base}/conv-1`, { deleted: true });

  const created = await createCopilotConversation('project-1');
  expect(created.kind).toBe('copilot');

  const list = await listCopilotConversations('project-1');
  expect(list[0].id).toBe('conv-1');

  const got = await getCopilotConversation('project-1', 'conv-1');
  expect(got.project_id).toBe('project-1');

  const renamed = await updateCopilotConversationTitle(
    'project-1',
    'conv-1',
    'Orders',
  );
  expect(renamed.title).toBe('Orders');

  const deleted = await deleteCopilotConversation('project-1', 'conv-1');
  expect(deleted.deleted).toBe(true);

  const [patchCall] = fetchMock.callHistory.calls(`${base}/conv-1`, {
    method: 'PATCH',
  });
  expect(JSON.parse(String(patchCall.options.body))).toEqual({
    title: 'Orders',
  });
});

test('getProjectReadiness requests the readiness endpoint', async () => {
  const base = 'http://agent.local/agent/semantic-layer/projects/project-1';
  fetchMock.get(`${base}/readiness`, {
    status: 'ready',
    ready: true,
    has_active_models: true,
    active_model_count: 3,
    running_job_id: null,
    detail: 'Semantic layer is ready.',
  });

  const readiness = await getProjectReadiness('project-1');
  expect(readiness.status).toBe('ready');
  expect(readiness.ready).toBe(true);
  expect(readiness.active_model_count).toBe(3);
  expect(fetchMock.callHistory.calls(`${base}/readiness`)).toHaveLength(1);
});

test('onboardSemanticProject sends the table selection body', async () => {
  const base = 'http://agent.local/agent/semantic-layer/projects/project-1';
  fetchMock.post(`${base}/onboard`, {
    id: 'job-1',
    kind: 'onboarding',
    status: 'running',
  });

  await onboardSemanticProject('project-1', {
    mode: 'include',
    datasetIds: [1, 2],
  });

  const [call] = fetchMock.callHistory.calls(`${base}/onboard`);
  expect(JSON.parse(String(call.options.body))).toEqual({
    mode: 'include',
    dataset_ids: [1, 2],
    exclude_dataset_ids: [],
    search: null,
  });
});

test('onboardSemanticProject with no selection posts the whole-schema body', async () => {
  const base = 'http://agent.local/agent/semantic-layer/projects/project-1';
  fetchMock.post(`${base}/onboard`, {
    id: 'job-1',
    kind: 'onboarding',
    status: 'running',
  });

  await onboardSemanticProject('project-1');

  const [call] = fetchMock.callHistory.calls(`${base}/onboard`);
  expect(JSON.parse(String(call.options.body))).toEqual({});
});

test('getSemanticJob fetches a job by id for polling', async () => {
  const base = 'http://agent.local/agent/semantic-layer/projects/project-1';
  fetchMock.get(`${base}/jobs/job-1`, {
    id: 'job-1',
    kind: 'onboarding',
    status: 'completed',
    project_id: 'project-1',
    result: { project_id: 'project-1', model_count: 2, warnings: [] },
  });

  const job = await getSemanticJob('project-1', 'job-1');
  expect(job.status).toBe('completed');
  expect(job.result?.model_count).toBe(2);
  expect(fetchMock.callHistory.calls(`${base}/jobs/job-1`)).toHaveLength(1);
});

test('getMdlProvenance requests the provenance endpoint', async () => {
  const base = 'http://agent.local/agent/semantic-layer/projects/project-1';
  fetchMock.get(`${base}/provenance`, [
    {
      id: 'e1',
      kind: 'mdl_created',
      status: 'ok',
      summary: 'Created models/orders.json',
      created_at: '2026-06-26T00:00:00Z',
      detail: { path: 'models/orders.json' },
    },
  ]);

  const entries = await getMdlProvenance('project-1');
  expect(entries[0].kind).toBe('mdl_created');
  expect(fetchMock.callHistory.calls(`${base}/provenance`)).toHaveLength(1);
});

test('listPhysicalTables queries the database tables endpoint via SupersetClient', async () => {
  const get = jest.spyOn(SupersetClient, 'get').mockResolvedValue({
    json: {
      count: 2,
      result: [
        { value: 'orders', type: 'table' },
        { value: 'orders_view', type: 'view' },
      ],
    },
  } as any);

  const result = await listPhysicalTables(1, 'public', 'prod');

  expect(result).toEqual({ count: 2, names: ['orders', 'orders_view'] });
  const endpoint = get.mock.calls[0][0].endpoint as string;
  expect(endpoint).toContain('/api/v1/database/1/tables/?q=');
  // The rison query carries the schema and catalog filters.
  expect(decodeURIComponent(endpoint)).toContain('schema_name:public');
  expect(decodeURIComponent(endpoint)).toContain('catalog_name:prod');
  get.mockRestore();
});

test('listPhysicalTables omits the catalog filter when none is given', async () => {
  const get = jest.spyOn(SupersetClient, 'get').mockResolvedValue({
    json: { count: 0, result: [] },
  } as any);

  const result = await listPhysicalTables(1, 'public');

  expect(result).toEqual({ count: 0, names: [] });
  expect(
    decodeURIComponent(get.mock.calls[0][0].endpoint as string),
  ).not.toContain('catalog_name');
  get.mockRestore();
});

test('createDataset posts the Add-Dataset payload and returns the new id', async () => {
  const post = jest.spyOn(SupersetClient, 'post').mockResolvedValue({
    json: { id: 42 },
  } as any);

  const id = await createDataset({
    databaseId: 1,
    schema: 'public',
    tableName: 'orders',
    catalog: 'prod',
  });

  expect(id).toBe(42);
  expect(post).toHaveBeenCalledWith({
    endpoint: '/api/v1/dataset/',
    jsonPayload: {
      database: 1,
      catalog: 'prod',
      schema: 'public',
      table_name: 'orders',
    },
  });
  post.mockRestore();
});

test('listAllRegisteredTableNames returns every name from a single page', async () => {
  const get = jest.spyOn(SupersetClient, 'get').mockResolvedValue({
    json: {
      count: 2,
      result: [{ table_name: 'orders' }, { table_name: 'customers' }],
    },
  } as any);

  const { names, truncated } = await listAllRegisteredTableNames(1, 'public');

  expect(names).toEqual(['orders', 'customers']);
  expect(truncated).toBe(false);
  // Projects to id+table_name only, keyed by schema.
  const endpoint = decodeURIComponent(get.mock.calls[0][0].endpoint as string);
  expect(endpoint).toContain('table_name');
  expect(endpoint).toContain('schema');
  get.mockRestore();
});

test('listAllRegisteredTableNames pages until the full count is read', async () => {
  const page0 = Array.from({ length: 1000 }, (_, i) => ({
    table_name: `t${i}`,
  }));
  const page1 = [{ table_name: 't1000' }];
  const get = jest
    .spyOn(SupersetClient, 'get')
    .mockResolvedValueOnce({ json: { count: 1001, result: page0 } } as any)
    .mockResolvedValueOnce({ json: { count: 1001, result: page1 } } as any);

  const { names, truncated } = await listAllRegisteredTableNames(1, 'public');

  expect(names).toHaveLength(1001);
  expect(names[1000]).toBe('t1000');
  expect(truncated).toBe(false);
  expect(get).toHaveBeenCalledTimes(2);
  get.mockRestore();
});

test('listAllRegisteredTableNames halts at the cap and reports truncation', async () => {
  const fullPage = Array.from({ length: 1000 }, (_, i) => ({
    table_name: `t${i}`,
  }));
  const get = jest
    .spyOn(SupersetClient, 'get')
    .mockResolvedValue({ json: { count: 9999, result: fullPage } } as any);

  const { names, truncated } = await listAllRegisteredTableNames(
    1,
    'public',
    2500,
  );

  // Stops once accumulated >= cap (3 pages of 1000 → 3000 ≥ 2500).
  expect(names.length).toBeGreaterThanOrEqual(2500);
  expect(truncated).toBe(true);
  get.mockRestore();
});

test('getDatasetWritePermission reads can_write from the dataset _info endpoint', async () => {
  const get = jest.spyOn(SupersetClient, 'get').mockResolvedValue({
    json: { permissions: ['can_read', 'can_write', 'can_export'] },
  } as any);

  expect(await getDatasetWritePermission()).toBe(true);
  const endpoint = get.mock.calls[0][0].endpoint as string;
  expect(endpoint).toContain('/api/v1/dataset/_info?q=');
  expect(decodeURIComponent(endpoint)).toContain('permissions');
  get.mockRestore();
});

test('getDatasetWritePermission is false when can_write is absent', async () => {
  const get = jest.spyOn(SupersetClient, 'get').mockResolvedValue({
    json: { permissions: ['can_read'] },
  } as any);

  expect(await getDatasetWritePermission()).toBe(false);
  get.mockRestore();
});

test('createDataset propagates a rejected request (e.g. 403 / duplicate)', async () => {
  const post = jest
    .spyOn(SupersetClient, 'post')
    .mockRejectedValue(new Error('Forbidden'));

  await expect(
    createDataset({ databaseId: 1, schema: 'public', tableName: 'orders' }),
  ).rejects.toThrow('Forbidden');
  post.mockRestore();
});

test('API helpers surface FastAPI detail errors', async () => {
  fetchMock.get('http://agent.local/agent/conversations', {
    status: 401,
    body: { detail: 'Superset session expired.' },
  });

  await expect(listConversations()).rejects.toThrow(
    'Superset session expired.',
  );
});

test('semantic-layer event source includes Superset credentials', () => {
  const calls: Array<{ url: string; init?: EventSourceInit }> = [];
  const OriginalEventSource = globalThis.EventSource;

  class MockEventSource {
    static CONNECTING = 0;

    static OPEN = 1;

    static CLOSED = 2;

    onerror: ((this: EventSource, ev: Event) => unknown) | null = null;

    onmessage: ((this: EventSource, ev: MessageEvent) => unknown) | null = null;

    onopen: ((this: EventSource, ev: Event) => unknown) | null = null;

    readyState = MockEventSource.CONNECTING;

    url: string;

    withCredentials: boolean;

    constructor(url: string | URL, init?: EventSourceInit) {
      this.url = String(url);
      this.withCredentials = init?.withCredentials ?? false;
      calls.push({ url: this.url, init });
    }

    addEventListener() {}

    close() {}

    dispatchEvent() {
      return true;
    }

    removeEventListener() {}
  }

  Object.defineProperty(globalThis, 'EventSource', {
    configurable: true,
    value: MockEventSource as unknown as typeof EventSource,
  });
  try {
    createSemanticLayerEventSource({
      database_id: 1,
      catalog_name: 'prod',
      schema_name: null,
      dataset_ids: [16],
    });
    createProjectSemanticLayerEventSource('project-1');
  } finally {
    Object.defineProperty(globalThis, 'EventSource', {
      configurable: true,
      value: OriginalEventSource,
    });
  }

  expect(calls).toEqual([
    {
      url: 'http://agent.local/agent/semantic-layer/events?database_id=1&catalog_name=prod&dataset_ids=16',
      init: { withCredentials: true },
    },
    {
      url: 'http://agent.local/agent/semantic-layer/projects/project-1/events',
      init: { withCredentials: true },
    },
  ]);
});
