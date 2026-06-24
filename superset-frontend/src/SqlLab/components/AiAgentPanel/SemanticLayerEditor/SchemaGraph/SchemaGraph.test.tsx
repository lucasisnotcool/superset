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
import { fireEvent } from '@testing-library/react';
import { render, screen, userEvent } from 'spec/helpers/testing-library';
import SchemaGraph, { GraphMdlFile } from './SchemaGraph';
import { SchemaGraphModel } from './types';

// Isolate the ECharts boundary (mock-prefixed for the jest.mock factory).
const mockSetOption = jest.fn();
const mockDispose = jest.fn();
const mockResize = jest.fn();
jest.mock('./echartsRender', () => ({
  createGraphChart: () => ({
    setOption: mockSetOption,
    resize: mockResize,
    dispose: mockDispose,
  }),
}));

// Control the physical data layer so the component is tested in isolation.
const mockLoadTable = jest.fn();
let mockHookReturn: {
  universe: string[];
  physicalGraph: SchemaGraphModel;
  loadedTables: string[];
  failedTables: string[];
  isLoadingUniverse: boolean;
  isHydrating: boolean;
  error: string | null;
  loadTable: jest.Mock;
};
jest.mock('./useSchemaGraphData', () => ({
  useSchemaGraphData: () => mockHookReturn,
}));

const emptyPhysical: SchemaGraphModel = { nodes: [], edges: [] };

beforeEach(() => {
  mockSetOption.mockClear();
  mockDispose.mockClear();
  mockResize.mockClear();
  mockLoadTable.mockClear();
  mockHookReturn = {
    universe: [],
    physicalGraph: emptyPhysical,
    loadedTables: [],
    failedTables: [],
    isLoadingUniverse: false,
    isHydrating: false,
    error: null,
    loadTable: mockLoadTable,
  };
});

const mdlFiles: GraphMdlFile[] = [
  {
    content: JSON.stringify({
      models: [
        {
          name: 'Orders',
          tableReference: { schema: 'public', table: 'orders' },
          columns: [{ name: 'id' }],
        },
        {
          name: 'Customers',
          tableReference: { schema: 'public', table: 'customers' },
          columns: [{ name: 'id' }],
        },
      ],
      relationships: [
        { name: 'r', models: ['Orders', 'Customers'], joinType: 'MANY_TO_ONE' },
      ],
    }),
  },
];

const renderGraph = (props = {}) =>
  render(
    <SchemaGraph
      mdlFiles={mdlFiles}
      databaseId={1}
      catalogName={null}
      schemaName="public"
      {...props}
    />,
    { useRedux: true },
  );

test('renders the layer toggle', () => {
  renderGraph();
  expect(screen.getByText('Combined')).toBeInTheDocument();
  expect(screen.getByText('Database')).toBeInTheDocument();
  expect(screen.getByText('Semantic')).toBeInTheDocument();
});

test('Semantic layer renders the MDL graph from the manifest (no network)', async () => {
  renderGraph();
  await userEvent.click(screen.getByText('Semantic'));
  expect(screen.getByTestId('schema-graph-canvas')).toBeInTheDocument();
  expect(mockSetOption).toHaveBeenCalled();
  const option = mockSetOption.mock.calls.at(-1)?.[0];
  expect(option.series[0].data).toHaveLength(2);
  expect(option.series[0].links).toHaveLength(1);
});

test('Combined layer renders physical nodes from the data hook', () => {
  mockHookReturn.physicalGraph = {
    nodes: [
      { id: 'phys:.public.orders', label: 'orders', kind: 'table' },
      { id: 'phys:.public.customers', label: 'customers', kind: 'table' },
    ],
    edges: [],
  };
  renderGraph();
  // default layer is Combined
  expect(screen.getByTestId('schema-graph-canvas')).toBeInTheDocument();
  expect(mockSetOption).toHaveBeenCalled();
});

test('shows a database empty state in Combined when nothing is loaded', () => {
  renderGraph({ mdlFiles: [] });
  expect(
    screen.getByText('No tables to visualize for this schema yet.'),
  ).toBeInTheDocument();
});

test('shows the MDL empty state in Semantic when there are no models', async () => {
  renderGraph({ mdlFiles: [] });
  await userEvent.click(screen.getByText('Semantic'));
  expect(screen.getByText(/No models to visualize yet/)).toBeInTheDocument();
});

test('search triggers a table load', () => {
  renderGraph();
  const input = screen.getByPlaceholderText('Find a table…');
  fireEvent.change(input, { target: { value: 'orders' } });
  fireEvent.keyDown(input, { key: 'Enter', code: 'Enter', keyCode: 13 });
  expect(mockLoadTable).toHaveBeenCalledWith('orders');
});

test('surfaces a data-layer error as an alert', () => {
  mockHookReturn.error = 'boom';
  renderGraph();
  expect(
    screen.getByText(/Could not load the database schema/),
  ).toBeInTheDocument();
});

test('lists tables whose metadata failed to load', () => {
  mockHookReturn.physicalGraph = {
    nodes: [{ id: 'phys:.public.broken', label: 'broken', kind: 'table' }],
    edges: [],
  };
  mockHookReturn.failedTables = ['broken'];
  renderGraph();
  expect(screen.getByText(/Metadata could not be loaded/)).toBeInTheDocument();
  expect(screen.getByText(/broken/)).toBeInTheDocument();
});

test('a validation message draws an error border on the MDL node', async () => {
  renderGraph({
    mdlFiles: [
      {
        content: mdlFiles[0].content,
        validation: {
          messages: [
            {
              severity: 'error',
              message: 'Duplicate model name: Orders.',
              code: 'duplicate_model',
            },
          ],
        },
      },
    ],
  });
  await userEvent.click(screen.getByText('Semantic'));
  const option = mockSetOption.mock.calls.at(-1)?.[0];
  const ordersNode = option.series[0].data.find(
    (n: { name: string }) => n.name === 'Orders',
  );
  expect(ordersNode.itemStyle.borderWidth).toBe(3);
});
