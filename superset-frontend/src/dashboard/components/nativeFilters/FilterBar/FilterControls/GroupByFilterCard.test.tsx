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
import { LabeledValue } from '@superset-ui/core/components';
import { render, waitFor } from 'spec/helpers/testing-library';
import { cachedSupersetGet } from 'src/utils/cachedSupersetGet';
import GroupByFilterCard, {
  createLabelSortComparator,
  resolveDatasetId,
} from './GroupByFilterCard';

jest.mock('src/utils/cachedSupersetGet', () => ({
  cachedSupersetGet: jest.fn(),
}));

const apple: LabeledValue = { value: 'a', label: 'Apple' };
const banana: LabeledValue = { value: 'b', label: 'Banana' };

test('sorts display values A-Z when sortAscending is true', () => {
  const compare = createLabelSortComparator(true);
  expect(compare(apple, banana)).toBeLessThan(0);
  expect(compare(banana, apple)).toBeGreaterThan(0);
});

test('sorts display values Z-A when sortAscending is false', () => {
  const compare = createLabelSortComparator(false);
  expect(compare(apple, banana)).toBeGreaterThan(0);
  expect(compare(banana, apple)).toBeLessThan(0);
});

test('preserves source order when sortAscending is unset', () => {
  const compare = createLabelSortComparator(undefined);
  expect(compare(apple, banana)).toBe(0);
  expect(compare(banana, apple)).toBe(0);
});

test('resolveDatasetId normalizes id / string / option-object / nullish', () => {
  expect(resolveDatasetId(7)).toBe(7);
  expect(resolveDatasetId('7')).toBe('7');
  expect(resolveDatasetId({ value: 7, label: 't' })).toBe(7);
  expect(resolveDatasetId(undefined)).toBeNull();
  expect(resolveDatasetId(null)).toBeNull();
  expect(resolveDatasetId({})).toBeNull();
});

test('fetches dataset columns with a projected query, not the full payload', async () => {
  (cachedSupersetGet as jest.Mock).mockResolvedValue({
    json: { result: { table_name: 'orders', columns: [] } },
  });

  const customizationItem = {
    id: 'cust-1',
    name: 'Group by',
    targets: [{ datasetId: 7, column: { name: 'col' } }],
    controlValues: {},
  } as any;

  render(
    <GroupByFilterCard
      customizationItem={customizationItem}
      dataMaskSelected={{}}
    />,
    { useRedux: true },
  );

  await waitFor(() => expect(cachedSupersetGet).toHaveBeenCalledTimes(1));
  const endpoint = (cachedSupersetGet as jest.Mock).mock.calls[0][0]
    .endpoint as string;
  // Detail-by-id WITH a rison projection (the heavy un-projected form is gone).
  expect(endpoint).toContain('/api/v1/dataset/7?q=');
  const decoded = decodeURIComponent(endpoint);
  expect(decoded).toContain('table_name');
  expect(decoded).toContain('columns.column_name');
  expect(decoded).toContain('columns.verbose_name');
  expect(decoded).toContain('columns.filterable');
  // Must NOT request metrics or other heavy nested collections.
  expect(decoded).not.toContain('metrics');
});
