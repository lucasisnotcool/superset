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
import GrantAccessModal, { GrantAccessModalProps } from './GrantAccessModal';

// The real DatabaseSelector needs the database list API and redux; stub it
// with a button that picks a fixed connection.
jest.mock('src/components/DatabaseSelector', () => ({
  DatabaseSelector: ({
    onDbChange,
  }: {
    onDbChange: (db: { id: number; database_name: string }) => void;
  }) => (
    <button
      type="button"
      data-test="mock-database-selector"
      onClick={() => onDbChange({ id: 7, database_name: 'warehouse_conn' })}
    >
      pick database
    </button>
  ),
}));

const postEndpoint = 'glob:*/api/v1/database_grant/';

const setup = (props: Partial<GrantAccessModalProps> = {}) => {
  const handlers = {
    onHide: jest.fn(),
    onGranted: jest.fn(),
    addSuccessToast: jest.fn(),
    addDangerToast: jest.fn(),
  };
  render(<GrantAccessModal show {...handlers} {...props} />, {
    useRedux: true,
  });
  return handlers;
};

beforeEach(() => {
  fetchMock.removeRoutes();
  fetchMock.clearHistory();
});

test('warns that listed usernames receive access on sign-in', () => {
  setup();
  expect(screen.getByTestId('grant-access-trust-note')).toHaveTextContent(
    /anyone who signs in with a listed username/i,
  );
});

test('primary action stays disabled until a database and usernames are set', async () => {
  setup();
  const primary = screen.getByTestId('modal-confirm-button');
  expect(primary).toBeDisabled();

  await userEvent.click(screen.getByTestId('mock-database-selector'));
  expect(primary).toBeDisabled();

  await userEvent.type(
    screen.getByTestId('grant-access-usernames'),
    'alice@x.io',
  );
  expect(primary).toBeEnabled();
});

test('shows a live count of unique parsed usernames', async () => {
  setup();
  await userEvent.type(
    screen.getByTestId('grant-access-usernames'),
    'Alice@x.io, bob@y.io alice@x.io',
  );
  expect(screen.getByTestId('grant-access-username-count')).toHaveTextContent(
    '2 unique username(s) detected',
  );
});

test('submits normalized usernames and reports the outcome', async () => {
  fetchMock.post(postEndpoint, {
    result: {
      created: ['alice@x.io', 'bob@y.io'],
      skipped: [],
      claimed_usernames: ['alice@x.io'],
    },
  });
  const { onGranted, onHide, addSuccessToast } = setup();

  await userEvent.click(screen.getByTestId('mock-database-selector'));
  await userEvent.type(
    screen.getByTestId('grant-access-usernames'),
    'Alice@x.io, bob@y.io',
  );
  await userEvent.click(screen.getByTestId('modal-confirm-button'));

  await waitFor(() => expect(onGranted).toHaveBeenCalledTimes(1));
  const [call] = fetchMock.callHistory.calls(postEndpoint);
  expect(JSON.parse(call.options.body as string)).toEqual({
    database_id: 7,
    usernames: ['alice@x.io', 'bob@y.io'],
  });
  expect(addSuccessToast).toHaveBeenCalledWith(
    expect.stringContaining('2 username(s) granted'),
  );
  expect(addSuccessToast).toHaveBeenCalledWith(
    expect.stringContaining('1 existing account(s) received access'),
  );
  expect(onHide).toHaveBeenCalled();
});

test('surfaces API errors without closing the modal', async () => {
  fetchMock.post(postEndpoint, {
    status: 422,
    body: { message: 'Database does not exist or is not accessible' },
  });
  const { onGranted, onHide, addDangerToast } = setup();

  await userEvent.click(screen.getByTestId('mock-database-selector'));
  await userEvent.type(
    screen.getByTestId('grant-access-usernames'),
    'alice@x.io',
  );
  await userEvent.click(screen.getByTestId('modal-confirm-button'));

  await waitFor(() => expect(addDangerToast).toHaveBeenCalled());
  expect(addDangerToast.mock.calls[0][0]).toMatch(/issue granting access/i);
  expect(onGranted).not.toHaveBeenCalled();
  expect(onHide).not.toHaveBeenCalled();
});
