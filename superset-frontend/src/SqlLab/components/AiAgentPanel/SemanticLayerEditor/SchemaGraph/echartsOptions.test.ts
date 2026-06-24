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
import { toEchartsOption } from './echartsOptions';
import { SchemaGraphModel } from './types';

const baseModel: SchemaGraphModel = {
  nodes: [
    { id: 'mdl:Orders', label: 'Orders', kind: 'model', columnCount: 4 },
    { id: 'mdl:Customers', label: 'Customers', kind: 'model', columnCount: 2 },
  ],
  edges: [
    {
      id: 'e:mdl:Orders->mdl:Customers:relationship',
      source: 'mdl:Orders',
      target: 'mdl:Customers',
      kind: 'relationship',
      label: 'OrdersCustomers',
      cardinality: 'MANY_TO_ONE',
    },
  ],
};

test('toEchartsOption produces a force graph series with nodes and links', () => {
  const option = toEchartsOption(baseModel);
  const [series] = option.series;
  expect(series.type).toBe('graph');
  expect(series.layout).toBe('force');
  expect(series.roam).toBe(true);
  expect(series.data).toHaveLength(2);
  expect(series.links).toHaveLength(1);
});

test('relationship links render dashed; cardinality surfaced in the tooltip', () => {
  const [series] = toEchartsOption(baseModel).series;
  expect(series.links[0].lineStyle.type).toBe('dashed');
  expect(series.links[0].tooltip).toContain('MANY_TO_ONE');
});

test('node labels are always shown', () => {
  const [series] = toEchartsOption(baseModel).series;
  expect(series.label.show).toBe(true);
  expect(series.label.formatter).toBe('{b}');
});

test('each node and link carries a prebuilt tooltip string', () => {
  const [series] = toEchartsOption(baseModel).series;
  expect(series.data[0].tooltip).toContain('Orders');
  expect(series.links[0].tooltip.length).toBeGreaterThan(0);
});

test('FK links render solid', () => {
  const option = toEchartsOption({
    nodes: [
      { id: 'phys:..a', label: 'a', kind: 'table' },
      { id: 'phys:..b', label: 'b', kind: 'table' },
    ],
    edges: [
      {
        id: 'e:phys:..a->phys:..b:fk',
        source: 'phys:..a',
        target: 'phys:..b',
        kind: 'fk',
      },
    ],
  });
  expect(option.series[0].links[0].lineStyle.type).toBe('solid');
});

test('unmodeled physical tables get the Unmodeled category', () => {
  const option = toEchartsOption({
    nodes: [
      { id: 'phys:..x', label: 'x', kind: 'table', modeled: false },
      { id: 'phys:..y', label: 'y', kind: 'table', modeled: true },
    ],
    edges: [],
  });
  const unmodeledIdx = option.series[0].categories.findIndex(
    c => c.name === 'Unmodeled',
  );
  const tableIdx = option.series[0].categories.findIndex(
    c => c.name === 'Table',
  );
  expect(option.series[0].data[0].category).toBe(unmodeledIdx);
  expect(option.series[0].data[1].category).toBe(tableIdx);
});

test('a validation error draws an error border on the node', () => {
  const option = toEchartsOption({
    nodes: [
      {
        id: 'mdl:Bad',
        label: 'Bad',
        kind: 'model',
        decorations: {
          validation: [{ severity: 'error', message: 'missing type' }],
        },
      },
    ],
    edges: [],
  });
  expect(option.series[0].data[0].itemStyle?.borderColor).toBeDefined();
  expect(option.series[0].data[0].itemStyle?.borderWidth).toBe(3);
});

test('agent-matched nodes are highlighted', () => {
  const option = toEchartsOption({
    nodes: [
      {
        id: 'mdl:Used',
        label: 'Used',
        kind: 'model',
        decorations: { agentUsage: 'matched' },
      },
    ],
    edges: [],
  });
  expect(option.series[0].data[0].itemStyle?.borderWidth).toBe(3);
});
