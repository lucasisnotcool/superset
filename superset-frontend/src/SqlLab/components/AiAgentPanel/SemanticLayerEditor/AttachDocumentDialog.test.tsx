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
  fireEvent,
  waitFor,
} from 'spec/helpers/testing-library';
import userEvent from '@testing-library/user-event';
import type { SemanticDocument } from '../api';
import AttachDocumentDialog, {
  type AttachDocumentDialogProps,
} from './AttachDocumentDialog';

// Stub the shared ingestion hook so uploads are exercised without redux/network.
const mockIngest = jest.fn();
let mockIngestingFlag = false;
jest.mock('../useDocumentIngestion', () => ({
  __esModule: true,
  default: () => ({ ingest: mockIngest, isIngesting: mockIngestingFlag }),
}));

// Override only the project-document listing; the rest of `../api` stays real.
const mockList = jest.fn();
jest.mock('../api', () => ({
  ...jest.requireActual('../api'),
  listProjectDocuments: (...args: unknown[]) => mockList(...args),
}));

const doc = (over: Partial<SemanticDocument> = {}): SemanticDocument => ({
  id: 'd1',
  filename: 'spec.pdf',
  content_type: 'application/pdf',
  size_bytes: 2048,
  status: 'extracted',
  scope: { database_id: 1, dataset_ids: [] },
  checksum: 'c',
  storage_uri: 'mem://spec',
  extracted_text: 'Quarterly revenue by region.',
  warnings: [],
  created_at: '2026-06-01T00:00:00Z',
  updated_at: '2026-06-01T00:00:00Z',
  ...over,
});

const makeFile = (
  name: string,
  {
    type = 'application/octet-stream',
    size,
  }: { type?: string; size?: number } = {},
): File => {
  const file = new File(['bytes'], name, { type });
  if (size !== undefined) {
    Object.defineProperty(file, 'size', { value: size });
  }
  return file;
};

const setup = (props: Partial<AttachDocumentDialogProps> = {}) => {
  const handlers = {
    onConfirm: jest.fn(),
    onClose: jest.fn(),
    onDocumentsChanged: jest.fn(),
  };
  const result = render(
    <AttachDocumentDialog
      open
      projectId="project-1"
      attachedDocs={[]}
      canWrite
      {...handlers}
      {...props}
    />,
  );
  return { ...handlers, ...result };
};

beforeEach(() => {
  mockIngest.mockReset();
  mockList.mockReset();
  mockIngestingFlag = false;
  mockList.mockResolvedValue([doc()]);
});

test('lists the project documents and shows the upload dropzone', async () => {
  setup();
  expect(await screen.findByTestId('attach-doc-d1')).toBeInTheDocument();
  expect(screen.getByTestId('copilot-attach-dropzone')).toBeInTheDocument();
  expect(mockList).toHaveBeenCalledWith('project-1');
});

test('pre-checks already-attached documents and reflects the count', async () => {
  setup({ attachedDocs: [doc()] });
  await screen.findByTestId('attach-doc-d1');
  // Seeded selection drives the primary button label.
  expect(screen.getByTestId('modal-confirm-button')).toHaveTextContent(
    'Attach (1)',
  );
});

test('selecting an existing document and confirming hands off the selection', async () => {
  const { onConfirm, onClose } = setup();
  await screen.findByTestId('attach-doc-d1');

  await userEvent.click(screen.getByTestId('attach-doc-d1'));
  expect(screen.getByTestId('modal-confirm-button')).toHaveTextContent(
    'Attach (1)',
  );

  await userEvent.click(screen.getByTestId('modal-confirm-button'));
  expect(onConfirm).toHaveBeenCalledWith([
    expect.objectContaining({ id: 'd1', extracted_text: expect.any(String) }),
  ]);
  expect(onClose).toHaveBeenCalledTimes(1);
});

test('deselecting a pre-attached document removes it from the confirmed set', async () => {
  const { onConfirm } = setup({ attachedDocs: [doc()] });
  await screen.findByTestId('attach-doc-d1');

  // Toggle it off.
  await userEvent.click(screen.getByTestId('attach-doc-d1'));
  await userEvent.click(screen.getByTestId('modal-confirm-button'));
  expect(onConfirm).toHaveBeenCalledWith([]);
});

test('uploading a file ingests it, auto-selects it, and notifies the parent', async () => {
  const uploaded = doc({ id: 'd2', filename: 'notes.md', status: 'extracted' });
  mockIngest.mockResolvedValue([{ document: uploaded, deduplicated: false }]);
  const { onDocumentsChanged } = setup();
  await screen.findByTestId('attach-doc-d1');

  await userEvent.upload(
    screen.getByTestId('copilot-attach-input'),
    makeFile('notes.md', { type: 'text/markdown' }),
  );

  expect(mockIngest).toHaveBeenCalledTimes(1);
  expect(await screen.findByTestId('attach-doc-d2')).toBeInTheDocument();
  expect(onDocumentsChanged).toHaveBeenCalledTimes(1);
  // The freshly-uploaded doc is auto-selected (and the seeded one is not).
  expect(screen.getByTestId('modal-confirm-button')).toHaveTextContent(
    'Attach (1)',
  );
});

test('dropping a supported file ingests it (drag-drop path)', async () => {
  const uploaded = doc({ id: 'd3', filename: 'data.csv' });
  mockIngest.mockResolvedValue([{ document: uploaded, deduplicated: false }]);
  setup();
  await screen.findByTestId('attach-doc-d1');

  fireEvent.drop(screen.getByTestId('copilot-attach-dropzone'), {
    dataTransfer: { files: [makeFile('data.csv', { type: 'text/csv' })] },
  });

  await waitFor(() => expect(mockIngest).toHaveBeenCalledTimes(1));
  expect(mockIngest.mock.calls[0][0]).toHaveLength(1);
});

test('dropping an unsupported type is skipped, not ingested (drop/pick parity)', async () => {
  setup();
  await screen.findByTestId('attach-doc-d1');

  fireEvent.drop(screen.getByTestId('copilot-attach-dropzone'), {
    dataTransfer: { files: [makeFile('photo.png', { type: 'image/png' })] },
  });

  expect(await screen.findByTestId('copilot-attach-skipped')).toHaveTextContent(
    'photo.png',
  );
  expect(mockIngest).not.toHaveBeenCalled();
});

test('an oversized file is rejected before upload with a size message', async () => {
  setup();
  await screen.findByTestId('attach-doc-d1');

  await userEvent.upload(
    screen.getByTestId('copilot-attach-input'),
    makeFile('huge.pdf', { type: 'application/pdf', size: 10_000_001 }),
  );

  expect(await screen.findByTestId('copilot-attach-skipped')).toHaveTextContent(
    'huge.pdf',
  );
  expect(mockIngest).not.toHaveBeenCalled();
});

test('dragging over highlights the dropzone, leaving restores it', async () => {
  setup();
  const dropzone = await screen.findByTestId('copilot-attach-dropzone');
  expect(dropzone).toHaveTextContent('Click to browse or drag files here');

  fireEvent.dragOver(dropzone);
  expect(dropzone).toHaveTextContent('Drop files to upload');

  fireEvent.dragLeave(dropzone);
  expect(dropzone).toHaveTextContent('Click to browse or drag files here');
});

test('keyboard activation opens the file picker', async () => {
  setup();
  const dropzone = await screen.findByTestId('copilot-attach-dropzone');
  const input = screen.getByTestId('copilot-attach-input') as HTMLInputElement;
  const clickSpy = jest.spyOn(input, 'click');

  fireEvent.keyDown(dropzone, { key: 'Enter' });
  expect(clickSpy).toHaveBeenCalledTimes(1);
});

test('shows an empty state when the project has no documents', async () => {
  mockList.mockResolvedValue([]);
  setup();
  expect(await screen.findByTestId('copilot-attach-empty')).toBeInTheDocument();
});

test('surfaces a load error with a working Retry', async () => {
  mockList.mockRejectedValueOnce(new Error('boom'));
  setup();
  expect(
    await screen.findByTestId('copilot-attach-load-error'),
  ).toHaveTextContent('boom');

  mockList.mockResolvedValue([doc()]);
  await userEvent.click(screen.getByTestId('copilot-attach-retry'));
  expect(await screen.findByTestId('attach-doc-d1')).toBeInTheDocument();
});

test('cancel closes without committing a selection', async () => {
  const { onConfirm, onClose } = setup();
  await screen.findByTestId('attach-doc-d1');

  await userEvent.click(screen.getByTestId('modal-cancel-button'));
  expect(onClose).toHaveBeenCalledTimes(1);
  expect(onConfirm).not.toHaveBeenCalled();
});

test('without write permission the primary action is disabled and uploads are blocked', async () => {
  setup({ canWrite: false });
  await screen.findByTestId('attach-doc-d1');

  expect(screen.getByTestId('modal-confirm-button')).toBeDisabled();
  fireEvent.drop(screen.getByTestId('copilot-attach-dropzone'), {
    dataTransfer: { files: [makeFile('notes.md', { type: 'text/markdown' })] },
  });
  expect(mockIngest).not.toHaveBeenCalled();
});

test('while ingesting, the dropzone shows progress and the primary is disabled', async () => {
  mockIngestingFlag = true;
  setup();
  await screen.findByTestId('attach-doc-d1');
  expect(screen.getByTestId('copilot-attach-uploading')).toBeInTheDocument();
  expect(screen.getByTestId('modal-confirm-button')).toBeDisabled();
});
