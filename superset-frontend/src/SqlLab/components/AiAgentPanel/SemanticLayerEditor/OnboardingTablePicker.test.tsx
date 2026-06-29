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
import { resetDatasetWritePermissionCache } from '../api';
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

const infoPage = (permissions: string[]) => ({ json: { permissions } }) as any;

interface SchemaFixture {
  datasets: { id: number; table_name: string }[];
  physical?: string[];
}

const PUBLIC: SchemaFixture = {
  datasets: [
    { id: 1, table_name: 'orders' },
    { id: 2, table_name: 'customers' },
    { id: 3, table_name: 'products' },
  ],
};

/**
 * URL-aware SupersetClient.get keyed by schema. The picker fully scans each
 * schema's registered datasets (columns-projected dataset list) and its physical
 * tables; this mock routes by the schema embedded in the rison query so a
 * multi-schema tree returns distinct rows per schema.
 */
const mockSupersetGet = (
  bySchema: Record<string, SchemaFixture> = { public: PUBLIC },
  permissions: string[] = ['can_read', 'can_write'],
) =>
  jest.spyOn(SupersetClient, 'get').mockImplementation(({ endpoint }: any) => {
    const url = String(endpoint);
    if (url.includes('/_info')) return Promise.resolve(infoPage(permissions));
    const schema = Object.keys(bySchema).find(
      name =>
        url.includes(`value:${name})`) || url.includes(`schema_name:${name})`),
    );
    const entry = schema ? bySchema[schema] : undefined;
    const datasets = entry?.datasets ?? [];
    const physical = entry?.physical ?? datasets.map(d => d.table_name);
    if (url.includes('/tables/'))
      return Promise.resolve(physicalPage(physical));
    return Promise.resolve(datasetsPage(datasets, datasets.length));
  });

beforeEach(() => {
  // The dataset-write-permission memo is module-level; reset it so a cached
  // grant from one test doesn't leak into the next (e.g. the can_write tests).
  resetDatasetWritePermissionCache();
  mockSupersetGet();
});

afterEach(() => {
  jest.restoreAllMocks();
});

const renderPicker = (
  onConfirm = jest.fn(),
  props: Partial<React.ComponentProps<typeof OnboardingTablePicker>> = {},
) => {
  render(
    <OnboardingTablePicker
      open
      databaseId={1}
      schema="public"
      onCancel={jest.fn()}
      onConfirm={onConfirm}
      {...props}
    />,
  );
  return onConfirm;
};

test('renders a schema header with its tables nested under it', async () => {
  renderPicker();
  expect(await screen.findByTestId('picker-schema-header')).toHaveTextContent(
    'public',
  );
  expect(await screen.findByText('orders')).toBeInTheDocument();
  expect(screen.getByText('customers')).toBeInTheDocument();
});

test('shows a per-schema loading row while the registered scan is in flight (G8)', async () => {
  // Hold the registered-dataset scan open so the in-flight loading state is
  // observable; the schema header still renders, with a loading row beneath it.
  let release: () => void = () => {};
  const gate = new Promise<void>(resolve => {
    release = resolve;
  });
  jest.spyOn(SupersetClient, 'get').mockImplementation(({ endpoint }: any) => {
    const url = String(endpoint);
    if (url.includes('/_info'))
      return Promise.resolve(infoPage(['can_read', 'can_write']));
    if (url.includes('/tables/'))
      return Promise.resolve(physicalPage(['orders']));
    return gate.then(() => datasetsPage([{ id: 1, table_name: 'orders' }], 1));
  });

  renderPicker();

  expect(
    await screen.findByTestId('picker-schema-loading'),
  ).toBeInTheDocument();

  release();
  await waitFor(() =>
    expect(
      screen.queryByTestId('picker-schema-loading'),
    ).not.toBeInTheDocument(),
  );
  expect(await screen.findByText('orders')).toBeInTheDocument();
});

test('onboards the checked subset as an explicit include list', async () => {
  const onConfirm = renderPicker();

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

test('a schema can be collapsed and expanded like the SQL DB browser', async () => {
  renderPicker();
  await screen.findByText('orders');

  // Collapsing the schema hides its tables…
  await userEvent.click(screen.getByTestId('picker-schema-header'));
  await waitFor(() =>
    expect(screen.queryByText('orders')).not.toBeInTheDocument(),
  );
  // …and the header is still shown so it can be expanded again.
  await userEvent.click(screen.getByTestId('picker-schema-header'));
  expect(await screen.findByText('orders')).toBeInTheDocument();
});

test('cross-schema: selections add up across schemas and the count reflects the total', async () => {
  jest.restoreAllMocks();
  mockSupersetGet({
    public: {
      datasets: [
        { id: 1, table_name: 'orders' },
        { id: 2, table_name: 'customers' },
      ],
    },
    sales: {
      datasets: [
        { id: 10, table_name: 'leads' },
        { id: 11, table_name: 'deals' },
      ],
    },
  });
  const onConfirm = jest.fn();
  render(
    <OnboardingTablePicker
      open
      databaseId={1}
      schemas={['public', 'sales']}
      primarySchema="public"
      onCancel={jest.fn()}
      onConfirm={onConfirm}
    />,
  );

  // Both schemas render as headers in the tree.
  await waitFor(() =>
    expect(screen.getAllByTestId('picker-schema-header')).toHaveLength(2),
  );
  // Tables from BOTH schemas are listed (all expanded by default).
  await screen.findByText('orders');
  await screen.findByText('leads');

  // Row checkboxes are ordered public rows then sales rows.
  const checkboxes = await screen.findAllByTestId('picker-checkbox');
  await userEvent.click(checkboxes[0]); // public.orders (id 1)
  expect(screen.getByTestId('picker-count')).toHaveTextContent('1 selected');
  await userEvent.click(checkboxes[2]); // sales.leads (id 10)
  // The count is the cross-schema total, not just the visible schema's.
  expect(screen.getByTestId('picker-count')).toHaveTextContent('2 selected');

  await userEvent.click(screen.getByTestId('picker-confirm'));
  expect(onConfirm).toHaveBeenCalledWith({
    mode: 'include',
    datasetIds: [1, 10],
  });
});

test('per-schema select-all checks every table in that schema only', async () => {
  jest.restoreAllMocks();
  mockSupersetGet({
    public: {
      datasets: [
        { id: 1, table_name: 'orders' },
        { id: 2, table_name: 'customers' },
      ],
    },
    sales: { datasets: [{ id: 10, table_name: 'leads' }] },
  });
  const onConfirm = jest.fn();
  render(
    <OnboardingTablePicker
      open
      databaseId={1}
      schemas={['public', 'sales']}
      primarySchema="public"
      onCancel={jest.fn()}
      onConfirm={onConfirm}
    />,
  );
  await waitFor(() =>
    expect(screen.getAllByTestId('picker-schema-checkbox')).toHaveLength(2),
  );

  // Toggle the primary schema's header checkbox → both its tables, not sales'.
  await userEvent.click(screen.getAllByTestId('picker-schema-checkbox')[0]);
  expect(screen.getByTestId('picker-count')).toHaveTextContent('2 selected');

  await userEvent.click(screen.getByTestId('picker-confirm'));
  expect(onConfirm).toHaveBeenCalledWith({
    mode: 'include',
    datasetIds: [1, 2],
  });
});

test('Select all spans every schema; Clear empties the selection', async () => {
  jest.restoreAllMocks();
  mockSupersetGet({
    public: { datasets: [{ id: 1, table_name: 'orders' }] },
    sales: { datasets: [{ id: 10, table_name: 'leads' }] },
  });
  const onConfirm = jest.fn();
  render(
    <OnboardingTablePicker
      open
      databaseId={1}
      schemas={['public', 'sales']}
      primarySchema="public"
      onCancel={jest.fn()}
      onConfirm={onConfirm}
    />,
  );
  await screen.findByText('leads');

  await userEvent.click(screen.getByText('Select all'));
  expect(screen.getByTestId('picker-count')).toHaveTextContent('2 selected');

  await userEvent.click(screen.getByText('Clear'));
  expect(screen.getByTestId('picker-count')).toHaveTextContent('0 selected');
  expect(screen.getByTestId('picker-confirm')).toBeDisabled();

  await userEvent.click(screen.getByText('Select all'));
  await userEvent.click(screen.getByTestId('picker-confirm'));
  expect(onConfirm).toHaveBeenCalledWith({
    mode: 'include',
    datasetIds: [1, 10],
  });
});

test('confirm is disabled with nothing selected', async () => {
  renderPicker();
  await screen.findByText('orders');
  expect(screen.getByTestId('picker-confirm')).toBeDisabled();
});

test('shift-click selects a contiguous range of rows', async () => {
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

test('search filters the tables shown under each schema', async () => {
  renderPicker();
  await screen.findByText('orders');

  await userEvent.type(screen.getByTestId('picker-search'), 'cust');
  await waitFor(() =>
    expect(screen.queryByText('orders')).not.toBeInTheDocument(),
  );
  expect(screen.getByText('customers')).toBeInTheDocument();
});

test('empty state shows when no schema has registered tables', async () => {
  jest.restoreAllMocks();
  mockSupersetGet({ public: { datasets: [], physical: [] } });
  renderPicker();

  await waitFor(() =>
    expect(screen.getByText(/No registered tables found/)).toBeInTheDocument(),
  );
  expect(screen.getByTestId('picker-register-link-empty')).toHaveAttribute(
    'href',
    '/dataset/add/',
  );
  expect(screen.getByTestId('picker-confirm')).toBeDisabled();
});

// 3 registered + 2 unregistered physical tables.
const WITH_UNREGISTERED: SchemaFixture = {
  datasets: PUBLIC.datasets,
  physical: [...PUBLIC.datasets.map(d => d.table_name), 'shipments', 'refunds'],
};

test('lists unregistered physical tables under their schema for inline registration', async () => {
  jest.restoreAllMocks();
  mockSupersetGet({ public: WITH_UNREGISTERED });
  renderPicker();

  await screen.findByText('orders');
  expect(
    await screen.findByTestId('picker-unregistered-header'),
  ).toHaveTextContent('Not registered (2)');
  const unreg = await screen.findAllByTestId('picker-unregistered-row');
  expect(unreg).toHaveLength(2);
});

test('registers checked unregistered tables, then onboards the full include set', async () => {
  jest.restoreAllMocks();
  mockSupersetGet({ public: WITH_UNREGISTERED });
  const post = jest
    .spyOn(SupersetClient, 'post')
    .mockResolvedValueOnce({ json: { id: 101 } } as any)
    .mockResolvedValueOnce({ json: { id: 102 } } as any);
  const onConfirm = renderPicker();

  await screen.findByText('orders');
  await userEvent.click(screen.getAllByTestId('picker-checkbox')[0]); // orders (id 1)
  const unregRows = await screen.findAllByTestId(
    'picker-unregistered-checkbox',
  );
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

test('registers unregistered tables in the SCHEMA they belong to (cross-schema)', async () => {
  jest.restoreAllMocks();
  mockSupersetGet({
    public: { datasets: [{ id: 1, table_name: 'orders' }] },
    sales: {
      datasets: [{ id: 10, table_name: 'leads' }],
      physical: ['leads', 'prospects'], // prospects unregistered in sales
    },
  });
  const post = jest
    .spyOn(SupersetClient, 'post')
    .mockResolvedValueOnce({ json: { id: 200 } } as any);
  const onConfirm = jest.fn();
  render(
    <OnboardingTablePicker
      open
      databaseId={1}
      schemas={['public', 'sales']}
      primarySchema="public"
      onCancel={jest.fn()}
      onConfirm={onConfirm}
    />,
  );

  // The unregistered 'prospects' belongs to sales; register it there.
  const unreg = await screen.findByText('prospects');
  await userEvent.click(unreg);
  await userEvent.click(screen.getByTestId('picker-confirm'));

  await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
  expect(post.mock.calls[0][0]).toEqual({
    endpoint: '/api/v1/dataset/',
    jsonPayload: {
      database: 1,
      catalog: null,
      schema: 'sales', // NOT the primary schema
      table_name: 'prospects',
    },
  });
  await waitFor(() =>
    expect(onConfirm).toHaveBeenCalledWith({
      mode: 'include',
      datasetIds: [200],
    }),
  );
});

test('a registration failure keeps the picker open and surfaces the error', async () => {
  jest.restoreAllMocks();
  mockSupersetGet({ public: WITH_UNREGISTERED });
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
  expect(onConfirm).not.toHaveBeenCalled();
  expect(await screen.findByText(/Could not register/)).toHaveTextContent(
    'refunds',
  );
});

test('virtualizes a large unregistered list to a bounded number of DOM rows', async () => {
  jest.restoreAllMocks();
  const many = Array.from({ length: 2000 }, (_, i) => `phys_${i}`);
  mockSupersetGet({
    public: {
      datasets: [{ id: 1, table_name: 'orders' }],
      physical: ['orders', ...many],
    },
  });
  renderPicker();

  expect(
    await screen.findByText(/Not registered \(2000\)/),
  ).toBeInTheDocument();
  expect(screen.getAllByTestId('picker-unregistered-row').length).toBeLessThan(
    60,
  );
});

test('unregistered tables are read-only without project write permission', async () => {
  jest.restoreAllMocks();
  mockSupersetGet({ public: WITH_UNREGISTERED });
  renderPicker(jest.fn(), { canWrite: false });

  await screen.findByText('orders');
  const unregRows = await screen.findAllByTestId(
    'picker-unregistered-checkbox',
  );
  unregRows.forEach(checkbox => expect(checkbox).toBeDisabled());
});

test('unregistered tables are read-only without real dataset can_write', async () => {
  jest.restoreAllMocks();
  mockSupersetGet({ public: WITH_UNREGISTERED }, ['can_read']);
  renderPicker();

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

test('an ordinary window focus does NOT refetch the schemas', async () => {
  // A plain alt-tab back to the app must not re-list every schema's datasets;
  // only a real return from the Add-Dataset flow should (see next test).
  const spy = jest.spyOn(SupersetClient, 'get');
  renderPicker();
  await screen.findByText('orders');
  const before = spy.mock.calls.length;

  window.dispatchEvent(new Event('focus'));

  // Give any (unwanted) refetch a chance to fire, then assert none did.
  await new Promise(resolve => setTimeout(resolve, 50));
  expect(spy.mock.calls.length).toBe(before);
});

test('returning from the Add-Dataset tab (focus after Register click) refetches', async () => {
  jest.restoreAllMocks();
  resetDatasetWritePermissionCache();
  const spy = mockSupersetGet({ public: { datasets: [], physical: [] } });
  renderPicker();

  // Empty state surfaces the "Register tables as datasets" link.
  const link = await screen.findByTestId('picker-register-link-empty');
  const before = spy.mock.calls.length;

  // Clicking the link arms the return-from-Add-Dataset guard...
  await userEvent.click(link);
  // ...so the next window focus reloads the schemas.
  window.dispatchEvent(new Event('focus'));

  await waitFor(() => expect(spy.mock.calls.length).toBeGreaterThan(before));
});
