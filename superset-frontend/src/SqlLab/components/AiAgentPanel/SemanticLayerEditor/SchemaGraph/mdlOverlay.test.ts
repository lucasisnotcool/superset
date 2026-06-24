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
  applyCoverage,
  buildSemanticGraph,
  composeCombined,
  mergeManifests,
  parseConditionRefs,
  parseManifest,
} from './mdlOverlay';
import { physicalNodeId } from './ids';
import { SchemaGraphModel } from './types';

const manifest = {
  catalog: 'wren',
  schema: 'public',
  models: [
    {
      name: 'Orders',
      tableReference: { catalog: 'wren', schema: 'public', table: 'orders' },
      columns: [{ name: 'id' }, { name: 'customer_id' }],
    },
    {
      name: 'Customers',
      tableReference: { catalog: 'wren', schema: 'public', table: 'customers' },
      columns: [{ name: 'id' }],
    },
  ],
  relationships: [
    {
      name: 'OrdersCustomers',
      models: ['Orders', 'Customers'],
      joinType: 'MANY_TO_ONE',
      condition: 'Orders.customer_id = Customers.id',
    },
  ],
  metrics: [{ name: 'revenue', baseObject: 'Orders', expression: 'sum(x)' }],
};

test('parseManifest returns null on invalid JSON', () => {
  expect(parseManifest('{not json')).toBeNull();
  expect(parseManifest('null')).toBeNull();
  expect(parseManifest(JSON.stringify(manifest))).not.toBeNull();
});

test('buildSemanticGraph builds model nodes with column/metric counts', () => {
  const graph = buildSemanticGraph(manifest);
  const orders = graph.nodes.find(n => n.label === 'Orders');
  expect(orders).toMatchObject({
    id: 'mdl:Orders',
    kind: 'model',
    columnCount: 2,
    metricCount: 1,
  });
  expect(graph.nodes.find(n => n.label === 'Customers')?.metricCount).toBe(0);
});

test('buildSemanticGraph builds a relationship edge with cardinality', () => {
  const graph = buildSemanticGraph(manifest);
  expect(graph.edges).toHaveLength(1);
  expect(graph.edges[0]).toMatchObject({
    source: 'mdl:Orders',
    target: 'mdl:Customers',
    kind: 'relationship',
    cardinality: 'MANY_TO_ONE',
  });
});

test('buildSemanticGraph carries relationship name, condition, and parsed refs', () => {
  const graph = buildSemanticGraph(manifest);
  expect(graph.edges[0]).toMatchObject({
    relationshipName: 'OrdersCustomers',
    condition: 'Orders.customer_id = Customers.id',
    columnRefs: [{ from: 'customer_id', to: 'id' }],
  });
});

test('parseConditionRefs extracts column pairs and handles AND / missing', () => {
  expect(parseConditionRefs('a.x = b.y')).toEqual([{ from: 'x', to: 'y' }]);
  expect(parseConditionRefs('a.x = b.y AND a.z = b.w')).toEqual([
    { from: 'x', to: 'y' },
    { from: 'z', to: 'w' },
  ]);
  expect(parseConditionRefs(undefined)).toBeUndefined();
  expect(parseConditionRefs('not an equality')).toBeUndefined();
});

test('buildSemanticGraph drops relationships referencing unknown models', () => {
  const graph = buildSemanticGraph({
    models: [{ name: 'Orders' }],
    relationships: [{ name: 'r', models: ['Orders', 'Ghost'] }],
  });
  expect(graph.edges).toHaveLength(0);
});

test('buildSemanticGraph ignores an unknown joinType for cardinality', () => {
  const graph = buildSemanticGraph({
    models: [{ name: 'A' }, { name: 'B' }],
    relationships: [{ name: 'r', models: ['A', 'B'], joinType: 'WEIRD' }],
  });
  expect(graph.edges[0].cardinality).toBeUndefined();
});

test('mergeManifests combines models across files and de-dupes by name', () => {
  const fileA = JSON.stringify({ models: [{ name: 'Orders' }] });
  const fileB = JSON.stringify({
    models: [{ name: 'Orders' }, { name: 'Customers' }],
  });
  const merged = mergeManifests([fileA, fileB, 'garbage{']);
  expect(merged.models?.map(m => m.name)).toEqual(['Orders', 'Customers']);
});

test('buildSemanticGraph flags models with calculated fields', () => {
  const graph = buildSemanticGraph({
    models: [
      {
        name: 'A',
        columns: [{ name: 'x', isCalculated: true, expression: '1' }],
      },
      { name: 'B', columns: [{ name: 'y' }] },
    ],
  });
  expect(graph.nodes.find(n => n.label === 'A')?.hasCalculatedFields).toBe(
    true,
  );
  expect(graph.nodes.find(n => n.label === 'B')?.hasCalculatedFields).toBe(
    false,
  );
});

test('composeCombined draws MDL relationships between physical table nodes', () => {
  const physical = {
    nodes: [
      {
        id: physicalNodeId('wren', 'public', 'orders'),
        label: 'orders',
        kind: 'table' as const,
      },
      {
        id: physicalNodeId('wren', 'public', 'customers'),
        label: 'customers',
        kind: 'table' as const,
      },
    ],
    edges: [],
  };
  const combined = composeCombined(physical, manifest);
  // coverage marks both as modeled
  expect(combined.nodes.every(n => n.modeled)).toBe(true);
  // one relationship edge mapped onto the physical table ids
  const rel = combined.edges.find(e => e.kind === 'relationship');
  expect(rel).toBeDefined();
  expect(rel?.source).toBe(physicalNodeId('wren', 'public', 'orders'));
  expect(rel?.target).toBe(physicalNodeId('wren', 'public', 'customers'));
});

test('composeCombined skips relationships whose physical endpoints are absent', () => {
  const physical = {
    nodes: [
      {
        id: physicalNodeId('wren', 'public', 'orders'),
        label: 'orders',
        kind: 'table' as const,
      },
    ],
    edges: [],
  };
  const combined = composeCombined(physical, manifest);
  expect(combined.edges.filter(e => e.kind === 'relationship')).toHaveLength(0);
});

test('applyCoverage marks modeled vs unmodeled physical tables', () => {
  const physical: SchemaGraphModel = {
    nodes: [
      {
        id: physicalNodeId('wren', 'public', 'orders'),
        label: 'orders',
        kind: 'table',
      },
      {
        id: physicalNodeId('wren', 'public', 'audit_log'),
        label: 'audit_log',
        kind: 'table',
      },
    ],
    edges: [],
  };
  const covered = applyCoverage(physical, manifest);
  expect(covered.nodes.find(n => n.label === 'orders')?.modeled).toBe(true);
  expect(covered.nodes.find(n => n.label === 'audit_log')?.modeled).toBe(false);
});
