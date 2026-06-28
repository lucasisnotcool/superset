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

import { render, screen, within, waitFor } from 'spec/helpers/testing-library';
import userEvent from '@testing-library/user-event';
import AutoOnboardModal, {
  type AutoOnboardModalProps,
} from './AutoOnboardModal';
import { SemanticDocument } from '../api';

const doc = (over: Partial<SemanticDocument> = {}): SemanticDocument => ({
  id: 'd1',
  filename: 'spec.pdf',
  content_type: 'application/pdf',
  size_bytes: 2048,
  status: 'extracted',
  scope: { database_id: 1, dataset_ids: [] },
  checksum: 'c',
  storage_uri: 'mem://spec',
  warnings: [],
  created_at: '2026-06-01T00:00:00Z',
  updated_at: '2026-06-01T00:00:00Z',
  ...over,
});

const setup = (props: Partial<AutoOnboardModalProps> = {}) => {
  const handlers = {
    onUpload: jest.fn().mockResolvedValue([]),
    onCancel: jest.fn(),
    onConfirm: jest.fn(),
  };
  render(
    <AutoOnboardModal
      open
      canWrite
      documents={[doc()]}
      {...handlers}
      {...props}
    />,
  );
  return handlers;
};

test('confirm is disabled until a document is selected, then hands off the selection', async () => {
  const { onConfirm } = setup();

  const confirm = screen.getByTestId('auto-onboard-confirm');
  expect(confirm).toBeDisabled();

  await userEvent.click(screen.getByTestId('auto-onboard-checkbox'));
  expect(confirm).toBeEnabled();

  await userEvent.click(confirm);
  expect(onConfirm).toHaveBeenCalledWith([
    expect.objectContaining({ id: 'd1' }),
  ]);
});

test('an uploaded document is appended and auto-selected', async () => {
  const onUpload = jest
    .fn()
    .mockResolvedValue([doc({ id: 'd2', filename: 'new.md' })]);
  setup({ documents: [], onUpload });

  const input = screen.getByTestId('auto-onboard-file-input');
  await userEvent.upload(
    input,
    new File(['hi'], 'new.md', { type: 'text/markdown' }),
  );

  await screen.findByText('new.md');
  // Auto-selected → confirm becomes enabled with no manual click.
  await waitFor(() =>
    expect(screen.getByTestId('auto-onboard-confirm')).toBeEnabled(),
  );
});

test('a still-extracting selection blocks confirm', async () => {
  setup({ documents: [doc({ status: 'extracting' })] });

  await userEvent.click(screen.getByTestId('auto-onboard-checkbox'));

  expect(screen.getByTestId('auto-onboard-confirm')).toBeDisabled();
  expect(
    screen.getByText('Waiting for documents to finish processing…'),
  ).toBeInTheDocument();
});

test('read-only access cannot confirm or upload', () => {
  setup({ canWrite: false });

  expect(screen.getByTestId('auto-onboard-confirm')).toBeDisabled();
  expect(screen.getByTestId('auto-onboard-upload')).toBeDisabled();
});

test('empty corpus shows the upload prompt', () => {
  setup({ documents: [] });

  expect(
    within(screen.getByTestId('auto-onboard-list')).getByText(
      'No documents yet — upload one to get started.',
    ),
  ).toBeInTheDocument();
});
