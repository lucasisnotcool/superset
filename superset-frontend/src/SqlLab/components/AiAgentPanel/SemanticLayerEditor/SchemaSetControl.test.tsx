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

import { render, screen } from 'spec/helpers/testing-library';
import userEvent from '@testing-library/user-event';
import SchemaSetControl from './SchemaSetControl';

jest.mock('src/hooks/apiResources/schemas', () => ({
  useSchemasQuery: () => ({
    data: [
      { value: 'sales', label: 'sales', title: 'sales' },
      { value: 'crm', label: 'crm', title: 'crm' },
      { value: 'archive', label: 'archive', title: 'archive' },
    ],
  }),
}));

test('renders a chip for every schema in the set', () => {
  render(
    <SchemaSetControl
      schemaNames={['sales', 'crm']}
      primarySchema="sales"
      databaseId={1}
      onAddSchema={jest.fn()}
    />,
  );
  expect(screen.getByText('sales')).toBeInTheDocument();
  expect(screen.getByText('crm')).toBeInTheDocument();
});

test('hides the add-schema control without edit permission', () => {
  render(
    <SchemaSetControl
      schemaNames={['sales']}
      primarySchema="sales"
      databaseId={1}
      canEdit={false}
      onAddSchema={jest.fn()}
    />,
  );
  expect(screen.queryByTestId('add-schema-button')).not.toBeInTheDocument();
});

test('shows the add-schema control with edit permission', () => {
  render(
    <SchemaSetControl
      schemaNames={['sales']}
      primarySchema="sales"
      databaseId={1}
      canEdit
      onAddSchema={jest.fn()}
    />,
  );
  expect(screen.getByTestId('add-schema-button')).toBeInTheDocument();
});

test('add-schema control opens a schema picker', async () => {
  render(
    <SchemaSetControl
      schemaNames={['sales']}
      primarySchema="sales"
      databaseId={1}
      canEdit
      onAddSchema={jest.fn()}
    />,
  );
  await userEvent.click(screen.getByTestId('add-schema-button'));
  expect(await screen.findByTestId('add-schema-popover')).toBeInTheDocument();
});

test('shows a spinner on the add-schema button while an add is in flight', () => {
  render(
    <SchemaSetControl
      schemaNames={['sales']}
      primarySchema="sales"
      databaseId={1}
      canEdit
      adding
      onAddSchema={jest.fn()}
    />,
  );
  const button = screen.getByTestId('add-schema-button').closest('button');
  expect(button).toHaveClass('ant-btn-loading');
});

test('selecting a schema invokes onAddSchema with the chosen schema', async () => {
  const onAddSchema = jest.fn();
  render(
    <SchemaSetControl
      schemaNames={['sales']}
      primarySchema="sales"
      databaseId={1}
      canEdit
      onAddSchema={onAddSchema}
    />,
  );
  await userEvent.click(screen.getByTestId('add-schema-button'));
  const combobox = await screen.findByRole('combobox');
  await userEvent.click(combobox);
  // 'crm' is offered (not already in the set); 'sales' is filtered out.
  await userEvent.click(await screen.findByText('crm'));
  expect(onAddSchema).toHaveBeenCalledWith('crm');
});
