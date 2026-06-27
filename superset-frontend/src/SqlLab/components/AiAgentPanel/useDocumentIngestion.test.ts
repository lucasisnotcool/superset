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
import { renderHook, act } from 'spec/helpers/testing-library';
import { createWrapper } from 'spec/helpers/testing-library';
import type { SemanticDocument } from './api';
import useDocumentIngestion from './useDocumentIngestion';

const mockUpload = jest.fn();
const mockSuccessToast = jest.fn();
const mockDangerToast = jest.fn();

jest.mock('./api', () => ({
  ...jest.requireActual('./api'),
  uploadProjectSourceDocument: (...args: unknown[]) => mockUpload(...args),
}));

jest.mock('src/components/MessageToasts/actions', () => ({
  ...jest.requireActual('src/components/MessageToasts/actions'),
  addSuccessToast: (...args: unknown[]) => {
    mockSuccessToast(...args);
    return { type: 'ADD_TOAST', payload: {} };
  },
  addDangerToast: (...args: unknown[]) => {
    mockDangerToast(...args);
    return { type: 'ADD_TOAST', payload: {} };
  },
}));

const doc = (overrides: Partial<SemanticDocument> = {}): SemanticDocument => ({
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
  ...overrides,
});

const file = (name = 'glossary.csv') =>
  new File(['revenue by region'], name, { type: 'text/csv' });

const setup = (projectId: string | null = 'project-1') =>
  renderHook(() => useDocumentIngestion(projectId), {
    wrapper: createWrapper({ useRedux: true }),
  });

beforeEach(() => jest.clearAllMocks());

test('uploads each file and returns the persisted documents', async () => {
  mockUpload.mockResolvedValue(doc({ deduplicated: false }));
  const { result } = setup();

  let ingested: Awaited<ReturnType<typeof result.current.ingest>> = [];
  await act(async () => {
    ingested = await result.current.ingest([file()]);
  });

  expect(mockUpload).toHaveBeenCalledWith('project-1', expect.any(File));
  expect(ingested).toEqual([
    { document: expect.objectContaining({ id: 'doc-1' }), deduplicated: false },
  ]);
  expect(mockSuccessToast).toHaveBeenCalledWith('Uploaded “glossary.csv”.');
});

test('flags a deduplicated upload and toasts that it is being reused', async () => {
  mockUpload.mockResolvedValue(doc({ deduplicated: true }));
  const { result } = setup();

  let ingested: Awaited<ReturnType<typeof result.current.ingest>> = [];
  await act(async () => {
    ingested = await result.current.ingest([file()]);
  });

  expect(ingested[0].deduplicated).toBe(true);
  expect(mockSuccessToast).toHaveBeenCalledWith(
    '“glossary.csv” is already in this project — reusing it.',
  );
});

test('toasts and drops a file that fails to upload, keeping the others', async () => {
  mockUpload
    .mockResolvedValueOnce(doc({ id: 'ok', filename: 'ok.csv' }))
    .mockRejectedValueOnce(new Error('boom'));
  const { result } = setup();

  let ingested: Awaited<ReturnType<typeof result.current.ingest>> = [];
  await act(async () => {
    ingested = await result.current.ingest([file('ok.csv'), file('bad.csv')]);
  });

  expect(ingested).toHaveLength(1);
  expect(ingested[0].document.id).toBe('ok');
  expect(mockDangerToast).toHaveBeenCalledWith(
    'Could not upload “bad.csv”: boom',
  );
});

test('is a no-op without a project id', async () => {
  const { result } = setup(null);

  let ingested: Awaited<ReturnType<typeof result.current.ingest>> = [];
  await act(async () => {
    ingested = await result.current.ingest([file()]);
  });

  expect(ingested).toEqual([]);
  expect(mockUpload).not.toHaveBeenCalled();
});
