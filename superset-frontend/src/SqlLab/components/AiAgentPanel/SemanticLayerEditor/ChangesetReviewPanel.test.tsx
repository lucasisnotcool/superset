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
import userEvent from '@testing-library/user-event';
import { render, screen } from 'spec/helpers/testing-library';
import { Changeset } from '../api';
import ChangesetReviewPanel from './ChangesetReviewPanel';

const changeset: Changeset = {
  items: [
    {
      op: 'create',
      path: 'models/a.json',
      current_content: '',
      proposed_content: '{"a":1}',
      summary: 'Add model a',
      validation: { valid: true, messages: [] },
    },
    {
      op: 'delete',
      path: 'models/b.json',
      file_id: 'b1',
      summary: 'Remove redundant b (contradicts the glossary)',
      validation: { valid: true, messages: [] },
    },
    {
      op: 'update',
      path: 'models/c.json',
      file_id: 'c1',
      current_content: 'x',
      proposed_content: 'y',
      summary: 'Invalid edit',
      validation: { valid: false, messages: [] },
    },
  ],
  warnings: [],
  steps: [],
  message: 'done',
};

test('renders each item with a diff, and a delete as a removal', () => {
  render(<ChangesetReviewPanel changeset={changeset} onApply={jest.fn()} />);
  expect(screen.getAllByTestId('changeset-review-item')).toHaveLength(3);
  expect(screen.getByText('This file will be deleted.')).toBeInTheDocument();
  // A delete is shown conspicuously (Delete tag) but as a normal reviewable item.
  expect(screen.getByText('Delete')).toBeInTheDocument();
});

test('pre-accepts valid items (incl. deletes) and pre-rejects only invalid ones', async () => {
  const onApply = jest.fn();
  render(<ChangesetReviewPanel changeset={changeset} onApply={onApply} />);

  // a (create, valid) + b (delete, valid) accepted; c (invalid) rejected → 2.
  const applyButton = await screen.findByTestId('changeset-apply');
  expect(applyButton).toHaveTextContent('Apply 2 accepted');

  await userEvent.click(applyButton);
  const accepted = onApply.mock.calls[0][0];
  const paths = accepted.map((item: { path: string }) => item.path);
  // The removal is approved by default — it is NOT singled out for pre-rejection.
  expect(paths).toEqual(['models/a.json', 'models/b.json']);
});

test('rejecting an item removes it from the accepted set', async () => {
  const onApply = jest.fn();
  render(<ChangesetReviewPanel changeset={changeset} onApply={onApply} />);

  // Reject the first item (create a). Buttons are in document order.
  const rejectButtons = screen.getAllByTestId('changeset-reject');
  await userEvent.click(rejectButtons[0]);

  const applyButton = screen.getByTestId('changeset-apply');
  expect(applyButton).toHaveTextContent('Apply 1 accepted');
  await userEvent.click(applyButton);
  expect(onApply.mock.calls[0][0].map((i: { path: string }) => i.path)).toEqual(
    ['models/b.json'],
  );
});

test('history (non-actionable) view hides apply and decision controls', () => {
  render(<ChangesetReviewPanel changeset={changeset} actionable={false} />);
  expect(screen.queryByTestId('changeset-apply')).not.toBeInTheDocument();
  expect(screen.queryByTestId('changeset-accept')).not.toBeInTheDocument();
});
