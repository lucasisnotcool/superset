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
import DatasetSelect from './DatasetSelect';

afterEach(() => {
  fetchMock.clearHistory().removeRoutes();
});

test('hydrates labels for already-selected dataset ids', async () => {
  fetchMock.get('glob:*/api/v1/dataset/16', {
    result: { id: 16, table_name: 'birth_names' },
  });

  render(
    <DatasetSelect
      databaseId={1}
      schema="public"
      value={[16]}
      onChange={jest.fn()}
    />,
  );

  // The chip resolves from the bare id to the dataset name.
  await waitFor(() => {
    expect(screen.getByText('birth_names')).toBeInTheDocument();
  });
  const [hydrationCall] = fetchMock.callHistory.calls(
    'glob:*/api/v1/dataset/16',
  );
  expect(hydrationCall).toBeTruthy();
});

test('is disabled until a database is selected', () => {
  const { container } = render(
    <DatasetSelect value={[]} onChange={jest.fn()} />,
  );
  expect(container.querySelector('.ant-select')).toHaveClass(
    'ant-select-disabled',
  );
});
