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
  fireEvent,
  render,
  screen,
  userEvent,
} from 'spec/helpers/testing-library';
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

test('does not render a redundant active status badge next to the toggle', () => {
  const activeTree: WorkspaceNode = {
    ...tree,
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
            status: 'active',
            file_id: 'file-1',
            validation: { valid: true, messages: [] },
            children: [],
          },
        ],
      },
    ],
  };
  render(<WorkspaceTree root={activeTree} onSelectFile={jest.fn()} />);

  // The Active/Draft toggle conveys status; the standalone "active" tag is gone.
  expect(screen.queryByText('active')).not.toBeInTheDocument();
});

test('context menu duplicates and deletes an MDL file', async () => {
  const onDuplicateFile = jest.fn();
  const onDeleteFiles = jest.fn();
  render(
    <WorkspaceTree
      root={tree}
      onSelectFile={jest.fn()}
      onDuplicateFile={onDuplicateFile}
      onDeleteFiles={onDeleteFiles}
    />,
  );

  fireEvent.contextMenu(screen.getByText('orders.json'));
  await userEvent.click(await screen.findByText('Duplicate'));
  expect(onDuplicateFile).toHaveBeenCalledWith('file-1');

  fireEvent.contextMenu(screen.getByText('orders.json'));
  await userEvent.click(await screen.findByText('Delete'));
  expect(onDeleteFiles).toHaveBeenCalledWith(['file-1']);
});

test('selects a raw/ document node by document id', async () => {
  const treeWithDoc: WorkspaceNode = {
    ...tree,
    children: [
      ...tree.children,
      {
        path: 'raw',
        name: 'raw',
        kind: 'folder',
        editable: false,
        status: '1 document(s)',
        children: [
          {
            path: 'raw/doc-1',
            name: 'glossary.md',
            kind: 'document',
            editable: false,
            status: 'extracted',
            document_id: 'doc-1',
            children: [],
          },
        ],
      },
    ],
  };
  const onSelectFile = jest.fn();
  const onSelectDocument = jest.fn();
  render(
    <WorkspaceTree
      root={treeWithDoc}
      onSelectFile={onSelectFile}
      onSelectDocument={onSelectDocument}
    />,
  );

  await userEvent.click(screen.getByText('glossary.md'));

  expect(onSelectDocument).toHaveBeenCalledWith('doc-1');
  expect(onSelectFile).not.toHaveBeenCalled();
});
