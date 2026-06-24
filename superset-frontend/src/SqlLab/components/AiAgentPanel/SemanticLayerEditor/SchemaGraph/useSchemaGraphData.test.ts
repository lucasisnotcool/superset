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
import { renderHook, waitFor } from '@testing-library/react';
import {
  createWrapper,
  defaultStore as store,
} from 'spec/helpers/testing-library';
import { api } from 'src/hooks/apiResources/queryApi';
import { useSchemaGraphData } from './useSchemaGraphData';

const TABLES = 'glob:*/api/v1/database/1/tables/?q=*';
const META = 'glob:*/api/v1/database/1/table_metadata/*';

beforeEach(() => {
  fetchMock.removeRoutes().clearHistory();
  store.dispatch(api.util.resetApiState());
});

afterEach(() => {
  fetchMock.removeRoutes().clearHistory();
});

const wrapper = createWrapper({ useRedux: true, store });

test('fetches the universe and hydrates the seed into a physical graph', async () => {
  fetchMock.get(TABLES, {
    count: 2,
    result: [
      { value: 'orders', label: 'orders', type: 'table' },
      { value: 'customers', label: 'customers', type: 'table' },
    ],
  });
  fetchMock.get(META, {
    name: 'orders',
    columns: [{ name: 'id', type: 'INT' }],
  });

  const { result } = renderHook(
    () =>
      useSchemaGraphData({
        databaseId: 1,
        catalog: null,
        schema: 'public',
        enabled: true,
        userTables: [],
      }),
    { wrapper },
  );

  await waitFor(() => expect(result.current.universe).toHaveLength(2));
  await waitFor(() =>
    expect(result.current.physicalGraph.nodes.length).toBeGreaterThan(0),
  );
  expect(result.current.universe).toEqual(['orders', 'customers']);
  // seed (both tables) hydrated -> two physical nodes
  await waitFor(() =>
    expect(result.current.physicalGraph.nodes).toHaveLength(2),
  );
});

test('a table whose metadata 500s still renders as a node, marked failed', async () => {
  fetchMock.get(TABLES, {
    count: 2,
    result: [
      { value: 'orders', label: 'orders', type: 'table' },
      { value: 'broken', label: 'broken', type: 'table' },
    ],
  });
  fetchMock.get('glob:*/table_metadata/?name=orders*', {
    name: 'orders',
    columns: [{ name: 'id', type: 'INT' }],
  });
  fetchMock.get('glob:*/table_metadata/?name=broken*', 500);

  const { result } = renderHook(
    () =>
      useSchemaGraphData({
        databaseId: 1,
        catalog: null,
        schema: 'public',
        enabled: true,
        userTables: [],
      }),
    { wrapper },
  );

  // both tables become nodes; the broken one is marked failed, not dropped
  await waitFor(() =>
    expect(result.current.physicalGraph.nodes).toHaveLength(2),
  );
  await waitFor(() => expect(result.current.failedTables).toContain('broken'));
  expect(result.current.loadedTables).toContain('orders');
});

test('does nothing when disabled (no network)', async () => {
  fetchMock.get(TABLES, { count: 0, result: [] });
  const { result } = renderHook(
    () =>
      useSchemaGraphData({
        databaseId: 1,
        catalog: null,
        schema: 'public',
        enabled: false,
        userTables: [],
      }),
    { wrapper },
  );
  // Give effects a tick; nothing should fetch.
  await waitFor(() => expect(result.current.universe).toEqual([]));
  expect(fetchMock.callHistory.calls(TABLES)).toHaveLength(0);
});
