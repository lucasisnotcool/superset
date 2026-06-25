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
import { render, screen, userEvent } from 'spec/helpers/testing-library';
import { WorkspaceNode } from '../api';
import WorkspaceTree from './WorkspaceTree';

const tree: WorkspaceNode = {
  path: '',
  name: 'workspace',
  kind: 'folder',
  editable: false,
  children: [
    {
      path: 'models',
      name: 'models',
      kind: 'folder',
      editable: false,
      children: [
        {
          path: 'models/orders.json',
          name: 'orders.json',
          kind: 'mdl',
          editable: true,
          status: 'draft',
          file_id: 'file-1',
          validation: { valid: true, messages: [] },
          children: [],
        },
      ],
    },
    {
      path: 'instructions.md',
      name: 'instructions.md',
      kind: 'instructions',
      editable: true,
      status: '2 rule(s)',
      children: [],
    },
  ],
};

test('renders folders and files and selects an MDL leaf by file id', async () => {
  const onSelectFile = jest.fn();
  render(<WorkspaceTree root={tree} onSelectFile={onSelectFile} />);

  expect(screen.getByText('models')).toBeInTheDocument();
  expect(screen.getByText('instructions.md')).toBeInTheDocument();

  await userEvent.click(screen.getByText('orders.json'));

  expect(onSelectFile).toHaveBeenCalledWith('file-1');
});

test('shows an empty state when there are no files', () => {
  render(<WorkspaceTree root={null} onSelectFile={jest.fn()} />);

  expect(screen.getByText('No semantic-layer files yet.')).toBeInTheDocument();
});
