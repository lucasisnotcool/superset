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
import { act, render, screen } from 'spec/helpers/testing-library';
import { MemoryRouter } from 'react-router-dom';
import { QueryParamProvider } from 'use-query-params';
import { ReactRouter5Adapter } from 'use-query-params/adapters/react-router-5';
import DatabaseAccessGrantsList from '.';

const listEndpoint = 'glob:*/api/v1/database_grant/?*';
const infoEndpoint = 'glob:*/api/v1/database_grant/_info*';

const mockGrants = [
  {
    id: 1,
    username: 'alice@example.com',
    database_id: 7,
    database: { id: 7, database_name: 'warehouse_conn' },
    user_id: 3,
    claimed_at: '2026-07-01T01:00:00Z',
    acknowledged_at: null,
    created_on: '2026-07-01T00:00:00Z',
    changed_on_delta_humanized: '1 day ago',
    created_by: { id: 1, first_name: 'Ada', last_name: 'Admin' },
  },
  {
    id: 2,
    username: 'bob@example.com',
    database_id: 7,
    database: { id: 7, database_name: 'warehouse_conn' },
    user_id: null,
    claimed_at: null,
    acknowledged_at: null,
    created_on: '2026-07-01T00:00:00Z',
    changed_on_delta_humanized: '1 day ago',
    created_by: { id: 1, first_name: 'Ada', last_name: 'Admin' },
  },
];

fetchMock.get(
  listEndpoint,
  { result: mockGrants, count: 2 },
  { name: listEndpoint },
);
fetchMock.get(
  infoEndpoint,
  { permissions: ['can_read', 'can_write'] },
  { name: infoEndpoint },
);

const mockUser = { userId: 1 };

async function renderAndWait() {
  return act(async () => {
    render(
      <MemoryRouter>
        <QueryParamProvider adapter={ReactRouter5Adapter}>
          <DatabaseAccessGrantsList user={mockUser} />
        </QueryParamProvider>
      </MemoryRouter>,
      { useRedux: true },
    );
  });
}

test('renders the grants list with usernames and database', async () => {
  await renderAndWait();
  expect(screen.getAllByText('Database Access Grants')[0]).toBeVisible();
  expect(screen.getByText('alice@example.com')).toBeInTheDocument();
  expect(screen.getByText('bob@example.com')).toBeInTheDocument();
  expect(screen.getAllByText('warehouse_conn')).toHaveLength(2);
});

test('derives a status tag per grant', async () => {
  await renderAndWait();
  const tags = screen.getAllByTestId('grant-status-tag');
  expect(tags.map(tag => tag.textContent)).toEqual(['Claimed', 'Pending']);
});

test('fetches ordered by last modified', async () => {
  fetchMock.clearHistory();
  await renderAndWait();
  const apiCalls = fetchMock.callHistory.calls(/database_grant\/\?q/);
  expect(apiCalls).toHaveLength(1);
  expect(apiCalls[0].url).toContain(
    'order_column:changed_on_delta_humanized,order_direction:desc',
  );
});

test('shows the Grant access button and revoke action for writers', async () => {
  await renderAndWait();
  expect(await screen.findByTestId('add-grant')).toBeInTheDocument();
  expect(screen.getAllByTestId('grant-revoke-icon')).toHaveLength(2);
});
