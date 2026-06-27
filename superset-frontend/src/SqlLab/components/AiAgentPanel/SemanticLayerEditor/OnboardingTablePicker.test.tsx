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
import { SupersetClient } from '@superset-ui/core';
import {
  render,
  screen,
  userEvent,
  waitFor,
} from 'spec/helpers/testing-library';
import OnboardingTablePicker from './OnboardingTablePicker';

const datasetsPage = (
  rows: { id: number; table_name: string }[],
  count: number,
) => ({ json: { result: rows, count } }) as any;

const physicalPage = (names: string[]) =>
  ({
    json: {
      result: names.map(value => ({ value, type: 'table' })),
      count: names.length,
    },
  }) as any;

const DEFAULT_DATASETS = [
  { id: 1, table_name: 'orders' },
  { id: 2, table_name: 'customers' },
  { id: 3, table_name: 'products' },
];

/**
 * URL-aware SupersetClient.get: the picker calls both the dataset list endpoint
 * (registered rows) and the physical-tables endpoint (the full schema). Physical
 * tables include the registered ones; the picker derives "unregistered" as the
 * set difference. Default: physical == registered, so no banner / no unregistered
 * rows unless a test passes extra physicalNames.
 */
const infoPage = (permissions: string[]) => ({ json: { permissions } }) as any;

const mockSupersetGet = ({
  datasets = DEFAULT_DATASETS,
  datasetCount = datasets.length,
  physicalNames = datasets.map(d => d.table_name),
  permissions = ['can_read', 'can_write'],
  // The AUTHORITATIVE registered names (columns-projected list call). Defaults to
  // the display datasets, so by default the display + authoritative views agree.
  registeredAll,
}: {
  datasets?: { id: number; table_name: string }[];
  datasetCount?: number;
  physicalNames?: string[];
  permissions?: string[];
  registeredAll?: string[];
} = {}) =>
  jest.spyOn(SupersetClient, 'get').mockImplementation(({ endpoint }: any) => {
    const url = String(endpoint);
    if (url.includes('/_info')) return Promise.resolve(infoPage(permissions));
    if (url.includes('/tables/')) {
      return Promise.resolve(physicalPage(physicalNames));
    }
    // The authoritative scan projects `columns:!(id,table_name)`; the display
    // list does not — branch on that to let tests diverge the two views.
    if (url.includes('columns')) {
      const names = registeredAll ?? datasets.map(d => d.table_name);
      return Promise.resolve(
        datasetsPage(
          names.map((table_name, id) => ({ id, table_name })),
          names.length,
        ),
      );
    }
    return Promise.resolve(datasetsPage(datasets, datasetCount));
  });

beforeEach(() => {
  mockSupersetGet();
});

afterEach(() => {
  jest.restoreAllMocks();
});

const renderPicker = (onConfirm = jest.fn()) => {
  render(
    <OnboardingTablePicker
      open
      databaseId={1}
      schema="public"
      onCancel={jest.fn()}
      onConfirm={onConfirm}
    />,
  );
  return onConfirm;
};

test('lists datasets and onboards the checked subset', async () => {
  const onConfirm = renderPicker();

  // Rows load from the dataset API.
  expect(await screen.findByText('orders')).toBeInTheDocument();
  expect(screen.getByText('customers')).toBeInTheDocument();

  // Check two rows.
  const checkboxes = await screen.findAllByTestId('picker-checkbox');
  await userEvent.click(checkboxes[0]); // orders (id 1)
  await userEvent.click(checkboxes[2]); // products (id 3)

  expect(screen.getByTestId('picker-count')).toHaveTextContent('2 selected');

  await userEvent.click(screen.getByTestId('picker-confirm'));
  expect(onConfirm).toHaveBeenCalledWith({
    mode: 'include',
    datasetIds: [1, 3],
  });
});

test('select-all-matching sends mode=all with exclusions', async () => {
  const onConfirm = renderPicker();
  await screen.findByText('orders');

  await userEvent.click(screen.getByText('Select all'));
  expect(screen.getByTestId('picker-count')).toHaveTextContent(
    'All 3 matching selected',
  );

  // Uncheck one → it becomes an exclusion.
  const checkboxes = screen.getAllByTestId('picker-checkbox');
  await userEvent.click(checkboxes[1]); // customers (id 2)
  expect(screen.getByTestId('picker-count')).toHaveTextContent(
    'All 2 matching selected',
  );

  await userEvent.click(screen.getByTestId('picker-confirm'));
  expect(onConfirm).toHaveBeenCalledWith({
    mode: 'all',
    excludeDatasetIds: [2],
    search: null,
  });
});

test('select-all also checks the listed unregistered tables', async () => {
  jest.restoreAllMocks();
  // Schema has an extra physical table that isn't a registered dataset.
  mockSupersetGet({
    physicalNames: ['orders', 'customers', 'products', 'events'],
  });
  const onConfirm = renderPicker();
  await screen.findByText('orders');
  // The unregistered row is listed.
  expect(await screen.findByText('events')).toBeInTheDocument();

  await userEvent.click(screen.getByText('Select all'));
  // Count covers the 3 registered datasets + the 1 unregistered table.
  expect(screen.getByTestId('picker-count')).toHaveTextContent(
    'All 4 matching selected',
  );
  // The unregistered checkbox is now checked, like the registered rows.
  expect(screen.getByTestId('picker-unregistered-checkbox')).toBeChecked();

  // Confirm registers & onboards: the unregistered table is created first, then
  // the all-minus-excludes registered selection is sent.
  jest
    .spyOn(SupersetClient, 'post')
    .mockResolvedValue({ json: { id: 99 } } as any);
  await userEvent.click(screen.getByTestId('picker-confirm'));
  await waitFor(() =>
    expect(onConfirm).toHaveBeenCalledWith({
      mode: 'all',
      excludeDatasetIds: [],
      search: null,
    }),
  );
});

test('select-all leaves unregistered tables alone without register permission', async () => {
  jest.restoreAllMocks();
  mockSupersetGet({
    physicalNames: ['orders', 'customers', 'products', 'events'],
    permissions: ['can_read'], // no can_write → cannot register
  });
  renderPicker();
  await screen.findByText('orders');
  await screen.findByText('events');

  await userEvent.click(screen.getByText('Select all'));
  // Only the 3 registered datasets are selected; the unregistered row stays off
  // (its checkbox is disabled) so no bulk registration is implied.
  expect(screen.getByTestId('picker-count')).toHaveTextContent(
    'All 3 matching selected',
  );
  expect(screen.getByTestId('picker-unregistered-checkbox')).not.toBeChecked();
});

test('confirm is disabled with nothing selected', async () => {
  renderPicker();
  await screen.findByText('orders');
  expect(screen.getByTestId('picker-confirm')).toBeDisabled();
});

test('shift-click selects a contiguous range of loaded rows', async () => {
  const onConfirm = renderPicker();
  await screen.findByText('orders');
  const rows = screen.getAllByTestId('picker-row');

  await userEvent.click(rows[0]); // anchor on orders (id 1)
  await userEvent.click(rows[2], { shiftKey: true }); // range 1..3

  expect(screen.getByTestId('picker-count')).toHaveTextContent('3 selected');
  await userEvent.click(screen.getByTestId('picker-confirm'));
  expect(onConfirm).toHaveBeenCalledWith({
    mode: 'include',
    datasetIds: [1, 2, 3],
  });
});

test('empty state shows when the schema has no registered tables', async () => {
  jest.restoreAllMocks();
  mockSupersetGet({ datasets: [], physicalNames: [] });
  renderPicker();

  await waitFor(() =>
    expect(screen.getByText(/No registered tables found/)).toBeInTheDocument(),
  );
  // The dead end is now actionable: a deep link to the Add Dataset flow.
  expect(screen.getByTestId('picker-register-link-empty')).toHaveAttribute(
    'href',
    '/dataset/add/',
  );
  expect(screen.getByTestId('picker-confirm')).toBeDisabled();
});

// 3 registered datasets, 30 physical tables → 27 unregistered.
const THIRTY_PHYSICAL = [
  ...DEFAULT_DATASETS.map(d => d.table_name),
  ...Array.from({ length: 27 }, (_, i) => `extra_${i}`),
];

test('gap banner appears when the schema has unregistered physical tables', async () => {
  jest.restoreAllMocks();
  mockSupersetGet({ physicalNames: THIRTY_PHYSICAL });
  renderPicker();

  const banner = await screen.findByTestId('picker-gap-banner');
  expect(banner).toHaveTextContent('3 of 30 tables');
  expect(banner).toHaveTextContent('public');
  expect(screen.getByTestId('picker-register-link')).toHaveAttribute(
    'href',
    '/dataset/add/',
  );
});

test('classifies against the authoritative set, not just the loaded display page', async () => {
  jest.restoreAllMocks();
  // Display page shows only `orders`, but the authoritative scan knows
  // `customers` is registered too. `shipments` is the only true unregistered one.
  mockSupersetGet({
    datasets: [{ id: 1, table_name: 'orders' }],
    registeredAll: ['orders', 'customers'],
    physicalNames: ['orders', 'customers', 'shipments'],
  });
  renderPicker();

  await screen.findByText('orders');
  const unreg = await screen.findAllByTestId('picker-unregistered-row');
  expect(unreg).toHaveLength(1);
  expect(unreg[0]).toHaveTextContent('shipments');
  // `customers` is NOT offered for registration (already a dataset).
  expect(screen.queryByText('customers')).not.toBeInTheDocument();
});

test('gap banner is hidden when every physical table is registered', async () => {
  renderPicker(); // default: 3 registered == 3 physical
  await screen.findByText('orders');
  expect(screen.queryByTestId('picker-gap-banner')).not.toBeInTheDocument();
});

test('returning to the tab (window focus) refetches the dataset list', async () => {
  const spy = jest.spyOn(SupersetClient, 'get');
  renderPicker();
  await screen.findByText('orders');
  const before = spy.mock.calls.length;

  window.dispatchEvent(new Event('focus'));

  await waitFor(() => expect(spy.mock.calls.length).toBeGreaterThan(before));
});

// 3 registered + 2 unregistered physical tables (shipments, refunds).
const WITH_UNREGISTERED = [
  ...DEFAULT_DATASETS.map(d => d.table_name),
  'shipments',
  'refunds',
];

test('registers checked unregistered tables, then onboards the full set', async () => {
  jest.restoreAllMocks();
  mockSupersetGet({ physicalNames: WITH_UNREGISTERED });
  const post = jest
    .spyOn(SupersetClient, 'post')
    .mockResolvedValueOnce({ json: { id: 101 } } as any)
    .mockResolvedValueOnce({ json: { id: 102 } } as any);
  const onConfirm = renderPicker();

  // One registered dataset + the two unregistered tables.
  await screen.findByText('orders');
  await userEvent.click(screen.getAllByTestId('picker-checkbox')[0]); // orders (id 1)
  const unregRows = await screen.findAllByTestId(
    'picker-unregistered-checkbox',
  );
  expect(unregRows).toHaveLength(2);
  await userEvent.click(unregRows[0]); // shipments
  await userEvent.click(unregRows[1]); // refunds

  expect(screen.getByTestId('picker-count')).toHaveTextContent('3 selected');
  expect(screen.getByTestId('picker-confirm')).toHaveTextContent(
    'Register & onboard 3 table(s)',
  );

  await userEvent.click(screen.getByTestId('picker-confirm'));

  await waitFor(() => expect(post).toHaveBeenCalledTimes(2));
  expect(post.mock.calls[0][0]).toEqual({
    endpoint: '/api/v1/dataset/',
    jsonPayload: {
      database: 1,
      catalog: null,
      schema: 'public',
      table_name: 'shipments',
    },
  });
  await waitFor(() =>
    expect(onConfirm).toHaveBeenCalledWith({
      mode: 'include',
      datasetIds: [1, 101, 102],
    }),
  );
});

test('unregistered section explains datasets get default columns + the current owner', async () => {
  jest.restoreAllMocks();
  mockSupersetGet({ physicalNames: WITH_UNREGISTERED });
  renderPicker();

  await screen.findByText('orders');
  const hint = await screen.findByTestId('picker-register-hint');
  expect(hint).toHaveTextContent('default columns');
  expect(hint).toHaveTextContent('you as owner');
});

test('a registration failure keeps the picker open and surfaces the error', async () => {
  jest.restoreAllMocks();
  mockSupersetGet({ physicalNames: WITH_UNREGISTERED });
  const post = jest
    .spyOn(SupersetClient, 'post')
    .mockResolvedValueOnce({ json: { id: 101 } } as any) // shipments ok
    .mockRejectedValueOnce(new Error('Boom')); // refunds fails
  const onConfirm = renderPicker();

  await screen.findByText('orders');
  const unregRows = await screen.findAllByTestId(
    'picker-unregistered-checkbox',
  );
  await userEvent.click(unregRows[0]); // shipments
  await userEvent.click(unregRows[1]); // refunds
  await userEvent.click(screen.getByTestId('picker-confirm'));

  await waitFor(() => expect(post).toHaveBeenCalledTimes(2));
  // Stays open, no onboarding, and the failure is shown.
  expect(onConfirm).not.toHaveBeenCalled();
  expect(await screen.findByText(/Could not register/)).toHaveTextContent(
    'refunds',
  );
});

test('virtualizes a large unregistered list to a bounded number of DOM rows', async () => {
  jest.restoreAllMocks();
  const many = Array.from({ length: 2000 }, (_, i) => `phys_${i}`);
  mockSupersetGet({
    datasets: [{ id: 1, table_name: 'orders' }],
    registeredAll: ['orders'],
    physicalNames: ['orders', ...many], // 2000 unregistered
  });
  renderPicker();

  // The header confirms the full set is known…
  expect(
    await screen.findByText(/Not registered \(2000\)/),
  ).toBeInTheDocument();
  // …but only a windowed slice is mounted in the DOM.
  expect(screen.getAllByTestId('picker-unregistered-row').length).toBeLessThan(
    60,
  );
});

test('unregistered tables are read-only without project write permission', async () => {
  jest.restoreAllMocks();
  mockSupersetGet({ physicalNames: WITH_UNREGISTERED });
  render(
    <OnboardingTablePicker
      open
      databaseId={1}
      schema="public"
      canWrite={false}
      onCancel={jest.fn()}
      onConfirm={jest.fn()}
    />,
  );

  await screen.findByText('orders');
  const unregRows = await screen.findAllByTestId(
    'picker-unregistered-checkbox',
  );
  unregRows.forEach(checkbox => expect(checkbox).toBeDisabled());
});

test('unregistered tables are read-only without real dataset can_write', async () => {
  jest.restoreAllMocks();
  // Project write is granted, but the user lacks Dataset can_write.
  mockSupersetGet({
    physicalNames: WITH_UNREGISTERED,
    permissions: ['can_read'],
  });
  renderPicker(); // canWrite defaults to true

  await screen.findByText('orders');
  await waitFor(() =>
    screen
      .getAllByTestId('picker-unregistered-checkbox')
      .forEach(checkbox => expect(checkbox).toBeDisabled()),
  );
  expect(
    screen.getByText(/ask an admin to register these/),
  ).toBeInTheDocument();
});

test('registration stays enabled when the _info permission lookup fails', async () => {
  jest.restoreAllMocks();
  // Dataset list + tables resolve; only the _info lookup rejects.
  jest.spyOn(SupersetClient, 'get').mockImplementation(({ endpoint }: any) => {
    const url = String(endpoint);
    if (url.includes('/_info')) return Promise.reject(new Error('nope'));
    if (url.includes('/tables/')) {
      return Promise.resolve(physicalPage(WITH_UNREGISTERED));
    }
    return Promise.resolve(
      datasetsPage(DEFAULT_DATASETS, DEFAULT_DATASETS.length),
    );
  });
  renderPicker();

  await screen.findByText('orders');
  // Permissive fallback: rows remain enabled (the create POST still enforces).
  await waitFor(() =>
    expect(
      screen.getAllByTestId('picker-unregistered-checkbox')[0],
    ).toBeEnabled(),
  );
});
