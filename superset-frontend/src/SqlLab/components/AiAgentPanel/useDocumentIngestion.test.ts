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
const mockInfoToast = jest.fn();

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
  addInfoToast: (...args: unknown[]) => {
    mockInfoToast(...args);
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

// jsdom's File does not implement `.text()`/`.size` reliably, so stub them; the
// production code uses the real browser File API.
const makeFile = (
  name: string,
  content: string,
  {
    type = 'application/json',
    size = content.length,
  }: { type?: string; size?: number } = {},
): File => {
  const f = new File([content], name, { type });
  Object.defineProperty(f, 'text', { value: () => Promise.resolve(content) });
  Object.defineProperty(f, 'size', { value: size });
  return f;
};

const mdlJsonFile = (name = 'model.json') =>
  makeFile(name, JSON.stringify({ models: [{ name: 'orders' }] }));

test('shows a one-time notice for an MDL-shaped JSON file (dropped MDL import)', async () => {
  mockUpload.mockResolvedValue(
    doc({ filename: 'model.json', content_type: 'application/json' }),
  );
  const { result } = setup();

  await act(async () => {
    await result.current.ingest([mdlJsonFile()]);
  });

  expect(mockInfoToast).toHaveBeenCalledTimes(1);
  expect(mockInfoToast).toHaveBeenCalledWith(
    expect.stringContaining('looks like an MDL file'),
  );
});

test('fires the MDL notice once even for multiple MDL-shaped JSON files', async () => {
  mockUpload
    .mockResolvedValueOnce(
      doc({ id: 'a', filename: 'a.json', content_type: 'application/json' }),
    )
    .mockResolvedValueOnce(
      doc({ id: 'b', filename: 'b.json', content_type: 'application/json' }),
    );
  const { result } = setup();

  await act(async () => {
    await result.current.ingest([mdlJsonFile('a.json'), mdlJsonFile('b.json')]);
  });

  expect(mockInfoToast).toHaveBeenCalledTimes(1);
});

test('does NOT show the MDL notice for a JSON data file (no MDL keys)', async () => {
  mockUpload.mockResolvedValue(
    doc({ filename: 'data.json', content_type: 'application/json' }),
  );
  const { result } = setup();

  await act(async () => {
    await result.current.ingest([
      makeFile('data.json', JSON.stringify({ rows: [1, 2, 3] })),
    ]);
  });

  expect(mockInfoToast).not.toHaveBeenCalled();
});

test('does NOT show the MDL notice for an oversized JSON file (treated as data)', async () => {
  mockUpload.mockResolvedValue(
    doc({ filename: 'big.json', content_type: 'application/json' }),
  );
  const { result } = setup();

  // MDL-shaped content but reported > 1 MB: skipped by the size cap (no parse).
  await act(async () => {
    await result.current.ingest([
      makeFile('big.json', JSON.stringify({ models: [{ name: 'x' }] }), {
        size: 2_000_000,
      }),
    ]);
  });

  expect(mockInfoToast).not.toHaveBeenCalled();
});

test('does not show the JSON notice for non-JSON files', async () => {
  mockUpload.mockResolvedValue(doc());
  const { result } = setup();

  await act(async () => {
    await result.current.ingest([file()]);
  });

  expect(mockInfoToast).not.toHaveBeenCalled();
});
