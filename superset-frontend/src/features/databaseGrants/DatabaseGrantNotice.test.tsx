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
import { render, screen, waitFor } from 'spec/helpers/testing-library';
import userEvent from '@testing-library/user-event';
import DatabaseGrantNotice from './DatabaseGrantNotice';

const mineEndpoint = 'glob:*/api/v1/database_grant/mine';
const acknowledgeEndpoint = 'glob:*/api/v1/database_grant/acknowledge';

const grant = {
  id: 11,
  database_id: 7,
  database_name: 'warehouse_conn',
  backend: 'postgresql',
  driver: 'psycopg2',
  host: 'dbhost',
  port: 5432,
  database: 'warehouse',
  connection_username: 'bob',
  granted_on: '2026-07-01T00:00:00Z',
};

const setup = (userId: number | undefined = 1) =>
  render(<DatabaseGrantNotice />, {
    useRedux: true,
    initialState: { user: userId ? { userId } : {} },
  });

beforeEach(() => {
  fetchMock.removeRoutes();
  fetchMock.clearHistory();
});

test('renders nothing when the user has no unacknowledged grants', async () => {
  fetchMock.get(mineEndpoint, { count: 0, result: [] });
  setup();
  await waitFor(() =>
    expect(fetchMock.callHistory.calls(mineEndpoint)).toHaveLength(1),
  );
  expect(
    screen.queryByTestId('database-grant-notice-body'),
  ).not.toBeInTheDocument();
});

test('does not even fetch for anonymous users', () => {
  fetchMock.get(mineEndpoint, { count: 0, result: [] });
  setup(undefined);
  expect(fetchMock.callHistory.calls(mineEndpoint)).toHaveLength(0);
});

test('stays silent when the endpoint errors (e.g. no permission)', async () => {
  fetchMock.get(mineEndpoint, { status: 403, body: { message: 'Forbidden' } });
  setup();
  await waitFor(() =>
    expect(fetchMock.callHistory.calls(mineEndpoint)).toHaveLength(1),
  );
  expect(
    screen.queryByTestId('database-grant-notice-body'),
  ).not.toBeInTheDocument();
});

test('shows a blocking dialog naming the granted database and its signature', async () => {
  fetchMock.get(mineEndpoint, { count: 1, result: [grant] });
  setup();

  expect(
    await screen.findByTestId('database-grant-notice-body'),
  ).toBeInTheDocument();
  expect(
    screen.getByText('You have been granted database access'),
  ).toBeInTheDocument();
  expect(screen.getByText('warehouse_conn')).toBeInTheDocument();
  // Host, port, database and connection user are exposed — never a password.
  expect(
    screen.getByText(/bob@dbhost:5432\/warehouse \(postgresql\)/),
  ).toBeInTheDocument();
  // No dismissal affordances besides the acknowledge button.
  expect(document.querySelector('.ant-modal-close')).not.toBeInTheDocument();
});

test('acknowledging posts the grant ids and closes the dialog', async () => {
  fetchMock.get(mineEndpoint, { count: 1, result: [grant] });
  fetchMock.post(acknowledgeEndpoint, { acknowledged: 1 });
  setup();

  await screen.findByTestId('database-grant-notice-body');
  await userEvent.click(screen.getByTestId('grant-notice-acknowledge'));

  await waitFor(() =>
    expect(
      screen.queryByTestId('database-grant-notice-body'),
    ).not.toBeInTheDocument(),
  );
  const [call] = fetchMock.callHistory.calls(acknowledgeEndpoint);
  expect(JSON.parse(call.options.body as string)).toEqual({ ids: [11] });
});

test('a failed acknowledgment keeps the dialog up for retry', async () => {
  fetchMock.get(mineEndpoint, { count: 1, result: [grant] });
  fetchMock.post(acknowledgeEndpoint, { status: 500, body: {} });
  setup();

  await screen.findByTestId('database-grant-notice-body');
  await userEvent.click(screen.getByTestId('grant-notice-acknowledge'));

  await waitFor(() =>
    expect(fetchMock.callHistory.calls(acknowledgeEndpoint)).toHaveLength(1),
  );
  expect(screen.getByTestId('database-grant-notice-body')).toBeInTheDocument();
  expect(screen.getByTestId('grant-notice-acknowledge')).toBeEnabled();
});
