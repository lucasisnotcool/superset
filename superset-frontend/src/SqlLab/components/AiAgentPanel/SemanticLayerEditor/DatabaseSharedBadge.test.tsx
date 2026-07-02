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
import DatabaseSharedBadge from './DatabaseSharedBadge';

test('shows an always-visible "Shared" label (not color/icon alone)', () => {
  render(<DatabaseSharedBadge databaseLabel="Sales DW" />);
  const badge = screen.getByTestId('database-shared-badge');
  expect(badge).toHaveTextContent('Shared');
});

test('is keyboard reachable so the popover is not pointer-only (WCAG 2.1.1)', () => {
  render(<DatabaseSharedBadge databaseLabel="Sales DW" />);
  const badge = screen.getByTestId('database-shared-badge');
  expect(badge).toHaveAttribute('tabindex', '0');
  expect(badge).toHaveAttribute('role', 'button');
  expect(badge).toHaveAttribute(
    'aria-label',
    expect.stringContaining('Sales DW'),
  );
});

test('focus opens a popover naming the database and what is shared', async () => {
  render(<DatabaseSharedBadge databaseLabel="Sales DW" />);
  const badge = screen.getByTestId('database-shared-badge');

  await userEvent.tab();
  expect(badge).toHaveFocus();

  // The popover explains the DB-tied sharing model and names the database.
  expect(
    await screen.findByText(/Everyone who can connect to Sales DW/i),
  ).toBeInTheDocument();
  expect(screen.getByText(/uploaded documents/i)).toBeInTheDocument();
  expect(screen.getByText(/instructions/i)).toBeInTheDocument();
  expect(screen.getByText(/golden queries/i)).toBeInTheDocument();
  // And is explicit that chat stays private (the one non-shared artifact).
  expect(
    screen.getByText(/Only your own chat conversations stay private/i),
  ).toBeInTheDocument();
});

test('falls back to a generic database noun when no label is provided', async () => {
  render(<DatabaseSharedBadge databaseLabel={null} />);
  await userEvent.tab();
  expect(
    await screen.findByText(/Everyone who can connect to this database/i),
  ).toBeInTheDocument();
});
