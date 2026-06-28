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

import {
  render,
  screen,
  selectOption,
  userEvent,
} from 'spec/helpers/testing-library';
import NewProjectModal from './NewProjectModal';

jest.mock('src/hooks/apiResources/schemas', () => ({
  useSchemasQuery: () => ({
    data: [
      { label: 'sales', value: 'sales' },
      { label: 'crm', value: 'crm' },
    ],
  }),
}));

const setup = () => {
  const onSubmit = jest.fn();
  const onCancel = jest.fn();
  render(
    <NewProjectModal
      open
      databaseId={1}
      catalogName={null}
      onSubmit={onSubmit}
      onCancel={onCancel}
    />,
  );
  return { onSubmit, onCancel };
};

test('Create is disabled until a schema is chosen, then submits the schema set', async () => {
  const { onSubmit } = setup();

  // No schema selected yet → cannot create.
  expect(screen.getByTestId('new-project-create')).toBeDisabled();

  await selectOption('sales', 'Project schemas');

  const create = screen.getByTestId('new-project-create');
  expect(create).toBeEnabled();
  await userEvent.click(create);
  expect(onSubmit).toHaveBeenCalledWith({ name: '', schemaNames: ['sales'] });
});

test('passes a typed name and multiple schemas (first is primary)', async () => {
  const { onSubmit } = setup();

  await userEvent.type(screen.getByTestId('new-project-name'), 'Revenue');
  await selectOption('crm', 'Project schemas');
  await selectOption('sales', 'Project schemas');

  await userEvent.click(screen.getByTestId('new-project-create'));
  expect(onSubmit).toHaveBeenCalledWith({
    name: 'Revenue',
    schemaNames: ['crm', 'sales'],
  });
});
