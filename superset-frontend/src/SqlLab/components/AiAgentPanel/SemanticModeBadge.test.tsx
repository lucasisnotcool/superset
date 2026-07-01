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
import SemanticModeBadge from './SemanticModeBadge';
import type { SemanticModeFactor, SemanticModeStatus } from './api';

const factor = (over: Partial<SemanticModeFactor>): SemanticModeFactor => ({
  key: 'k',
  label: 'Label',
  state: 'met',
  blocking: false,
  detail: 'detail',
  fixable_by: 'operator',
  ...over,
});

const semanticStatus: SemanticModeStatus = {
  mode: 'semantic',
  factors: [
    factor({ key: 'semantic_sql_enabled', label: 'Semantic SQL enabled' }),
    factor({ key: 'dialect_supported', label: 'Database dialect supported' }),
  ],
  blocking_factors: [],
  user_fixable_blocker: false,
};

// The incident shape: flags on, but Oracle's dialect is unsupported. Native, and
// the blocker is NOT user-fixable here (it's a database property).
const oracleNativeStatus: SemanticModeStatus = {
  mode: 'native',
  factors: [
    factor({ key: 'semantic_sql_enabled', label: 'Semantic SQL enabled' }),
    factor({
      key: 'dialect_supported',
      label: 'Database dialect supported',
      state: 'blocked',
      blocking: true,
      fixable_by: 'database',
      detail:
        "This database's dialect is not supported by the semantic engine; " +
        'queries run as native SQL.',
    }),
  ],
  blocking_factors: ['dialect_supported'],
  user_fixable_blocker: false,
};

const userFixableNativeStatus: SemanticModeStatus = {
  mode: 'native',
  factors: [
    factor({
      key: 'scope_selected',
      label: 'Project or schema selected',
      state: 'blocked',
      blocking: true,
      fixable_by: 'user',
      detail: 'Select a semantic project or a database schema to ground on.',
    }),
  ],
  blocking_factors: ['scope_selected'],
  user_fixable_blocker: true,
};

test('renders nothing when status is null', () => {
  const { container } = render(<SemanticModeBadge status={null} />);
  expect(container).toBeEmptyDOMElement();
});

test('semantic mode shows a green Semantic badge with no warning', () => {
  render(<SemanticModeBadge status={semanticStatus} />);
  const badge = screen.getByTestId('semantic-mode-badge');
  expect(badge).toHaveTextContent('Semantic');
  expect(badge).toHaveAttribute('data-mode', 'semantic');
  expect(screen.queryByTestId('semantic-mode-warning')).not.toBeInTheDocument();
});

test('native mode with a user-fixable blocker shows an amber warning on the badge', () => {
  render(<SemanticModeBadge status={userFixableNativeStatus} />);
  const badge = screen.getByTestId('semantic-mode-badge');
  expect(badge).toHaveTextContent('Native');
  expect(badge).toHaveAttribute('data-mode', 'native');
  expect(screen.getByTestId('semantic-mode-warning')).toBeInTheDocument();
});

test('native mode blocked only by a database factor shows no warning on the badge', () => {
  // Nothing the user can do here → no amber on the badge (avoids overstating
  // severity for an unfixable-here condition).
  render(<SemanticModeBadge status={oracleNativeStatus} />);
  const badge = screen.getByTestId('semantic-mode-badge');
  expect(badge).toHaveTextContent('Native');
  expect(screen.queryByTestId('semantic-mode-warning')).not.toBeInTheDocument();
});

test('the badge trigger is keyboard-focusable and reveals the factor checklist', async () => {
  render(<SemanticModeBadge status={oracleNativeStatus} />);
  const badge = screen.getByTestId('semantic-mode-badge');
  expect(badge).toHaveAttribute('tabindex', '0');
  expect(badge).toHaveAttribute('role', 'button');

  // Focus opens the popover (WCAG 1.4.13: not hover-only) and the blocking
  // factor's reason is surfaced.
  await userEvent.tab();
  expect(badge).toHaveFocus();
  expect(
    await screen.findByText(/dialect is not supported by the semantic engine/i),
  ).toBeInTheDocument();
});
