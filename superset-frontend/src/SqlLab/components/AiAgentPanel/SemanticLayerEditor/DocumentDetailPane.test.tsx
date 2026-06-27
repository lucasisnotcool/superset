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
import {
  render,
  screen,
  userEvent,
  waitFor,
} from 'spec/helpers/testing-library';
import type { SemanticDocument } from '../api';
import DocumentDetailPane from './DocumentDetailPane';

const mockReindex = jest.fn();
const mockSummarize = jest.fn();
const mockListChunks = jest.fn();

jest.mock('../api', () => ({
  // Spread the real module so this file-scoped mock never leaves other suites
  // with an incomplete `../api` (only the document fns are overridden here).
  ...jest.requireActual('../api'),
  downloadDocumentUrl: (id: string) => `/ai-agent/download/${id}`,
  listDocumentChunks: (...args: unknown[]) => mockListChunks(...args),
  reindexSemanticDocument: (...args: unknown[]) => mockReindex(...args),
  summarizeSemanticDocument: (...args: unknown[]) => mockSummarize(...args),
  deleteSemanticDocument: jest.fn(),
}));

const document: SemanticDocument = {
  id: 'doc-1',
  filename: 'glossary.csv',
  // Non-markdown -> rendered in a <pre>, so the text assertion does not depend
  // on SafeMarkdown's async markdown rendering.
  content_type: 'text/csv',
  size_bytes: 2048,
  status: 'extracted',
  scope: { database_id: 1, dataset_ids: [] },
  checksum: 'abc',
  storage_uri: 'mem://x',
  summary: 'A glossary.',
  extracted_text: 'Revenue by region.',
  warnings: [],
  created_at: '',
  updated_at: '',
};

beforeEach(() => {
  jest.clearAllMocks();
  mockListChunks.mockResolvedValue([]);
});

test('renders header, extracted text, and a download link', () => {
  const { container } = render(
    <DocumentDetailPane
      document={document}
      canWrite
      onDeleted={jest.fn()}
      onChanged={jest.fn()}
    />,
  );

  expect(screen.getByText('glossary.csv')).toBeInTheDocument();
  expect(screen.getByText('Revenue by region.')).toBeInTheDocument();
  expect(
    container.querySelector('a[href="/ai-agent/download/doc-1"]'),
  ).toBeInTheDocument();
});

test('re-index calls the api and refreshes', async () => {
  mockReindex.mockResolvedValue([]);
  const onChanged = jest.fn();
  render(
    <DocumentDetailPane
      document={document}
      canWrite
      onDeleted={jest.fn()}
      onChanged={onChanged}
    />,
  );

  await userEvent.click(screen.getByText('Re-index'));

  await waitFor(() => expect(mockReindex).toHaveBeenCalledWith('doc-1'));
  expect(onChanged).toHaveBeenCalled();
});

test('actions are disabled without write access', () => {
  const { container } = render(
    <DocumentDetailPane
      document={document}
      canWrite={false}
      onDeleted={jest.fn()}
      onChanged={jest.fn()}
    />,
  );

  expect(screen.getByText('Re-index').closest('button')).toBeDisabled();
  expect(screen.getByText('Delete').closest('button')).toBeDisabled();
  // Download stays available read-only.
  expect(
    container.querySelector('a[href="/ai-agent/download/doc-1"]'),
  ).toBeInTheDocument();
});

// The backend tags image-only PDFs needs_ocr and large uploads extracting; the
// shared api type does not list these yet, so cast at the test boundary.
const withStatus = (status: string): SemanticDocument => ({
  ...document,
  status: status as SemanticDocument['status'],
});

test('needs_ocr shows a Needs OCR badge and an explanatory note', () => {
  render(
    <DocumentDetailPane
      document={withStatus('needs_ocr')}
      canWrite
      onDeleted={jest.fn()}
      onChanged={jest.fn()}
    />,
  );

  expect(screen.getByText('Needs OCR')).toBeInTheDocument();
  expect(screen.getByText(/looks scanned or image-only/i)).toBeInTheDocument();
});

test('extracting shows a progress badge', () => {
  render(
    <DocumentDetailPane
      document={withStatus('extracting')}
      canWrite
      onDeleted={jest.fn()}
      onChanged={jest.fn()}
    />,
  );

  expect(screen.getByText('Extracting…')).toBeInTheDocument();
  // The needs_ocr note must not appear for other statuses.
  expect(screen.queryByText(/looks scanned/i)).not.toBeInTheDocument();
});
