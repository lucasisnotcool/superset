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
  addToReverseFkIndex,
  buildPhysicalGraph,
  capGraph,
  ConcurrencyLimiter,
  LruWindow,
  selectSeedTables,
} from './graphData';
import {
  ForeignKeyMeta,
  PhysicalTableMetadata,
  SchemaGraphModel,
} from './types';

const universe = ['orders', 'customers', 'products', 'line_items', 'returns'];

test('selectSeedTables prefers agent tables, then user tables, then first-N', () => {
  const seed = selectSeedTables({
    universe,
    agentTables: ['products'],
    userTables: ['customers'],
    limit: 3,
  });
  // agent first, then user, then fill from the universe head (skipping dupes).
  expect(seed).toEqual(['products', 'customers', 'orders']);
});

test('selectSeedTables ignores names not in the universe and de-dupes', () => {
  const seed = selectSeedTables({
    universe,
    agentTables: ['ghost', 'orders'],
    userTables: ['orders', 'returns'],
    limit: 10,
  });
  expect(seed).toEqual([
    'orders',
    'returns',
    'customers',
    'products',
    'line_items',
  ]);
});

test('selectSeedTables falls back to first-N when no signals', () => {
  expect(selectSeedTables({ universe, limit: 2 })).toEqual([
    'orders',
    'customers',
  ]);
  expect(selectSeedTables({ universe, limit: 0 })).toEqual([]);
});

test('ConcurrencyLimiter never exceeds max in flight', async () => {
  const limiter = new ConcurrencyLimiter(2);
  let active = 0;
  let peak = 0;
  const make = () => () =>
    new Promise<void>(resolve => {
      active += 1;
      peak = Math.max(peak, active);
      setTimeout(() => {
        active -= 1;
        resolve();
      }, 5);
    });
  await Promise.all(Array.from({ length: 6 }, () => limiter.run(make())));
  expect(peak).toBeLessThanOrEqual(2);
  expect(active).toBe(0);
});

test('LruWindow evicts least-recently-touched beyond capacity', () => {
  const lru = new LruWindow(2);
  expect(lru.add('a')).toEqual([]);
  expect(lru.add('b')).toEqual([]);
  lru.touch('a'); // a is now newest
  const evicted = lru.add('c'); // exceeds capacity -> evict oldest (b)
  expect(evicted).toEqual(['b']);
  expect(lru.has('a')).toBe(true);
  expect(lru.has('c')).toBe(true);
  expect(lru.has('b')).toBe(false);
  expect(lru.size).toBe(2);
});

test('LruWindow re-adding an existing id evicts nothing', () => {
  const lru = new LruWindow(2);
  lru.add('a');
  lru.add('b');
  expect(lru.add('a')).toEqual([]);
  expect(lru.size).toBe(2);
});

test('addToReverseFkIndex groups by referred table', () => {
  const index = new Map<string, ForeignKeyMeta[]>();
  addToReverseFkIndex(index, 'orders', [
    {
      column_names: ['customer_id'],
      referred_schema: 'public',
      referred_table: 'customers',
      referred_columns: ['id'],
    },
  ]);
  expect(index.get('customers')).toHaveLength(1);
  expect(index.get('customers')?.[0].name).toBe('orders');
});

test('buildPhysicalGraph emits an FK edge only when both endpoints are loaded', () => {
  const meta = new Map<string, PhysicalTableMetadata>([
    [
      'orders',
      {
        name: 'orders',
        columns: [{ name: 'id', type: 'INT' }],
        foreignKeys: [
          {
            column_names: ['customer_id'],
            referred_schema: 'public',
            referred_table: 'customers',
            referred_columns: ['id'],
          },
          {
            column_names: ['ghost_id'],
            referred_schema: 'public',
            referred_table: 'not_loaded',
            referred_columns: ['id'],
          },
        ],
      },
    ],
    [
      'customers',
      { name: 'customers', columns: [{ name: 'id', type: 'INT' }] },
    ],
  ]);
  const graph = buildPhysicalGraph([...meta.keys()], meta, {
    catalog: null,
    schema: 'public',
  });
  expect(graph.nodes.map(n => n.label).sort()).toEqual(['customers', 'orders']);
  // Only the orders->customers edge; the not_loaded target is skipped.
  expect(graph.edges).toHaveLength(1);
  expect(graph.edges[0]).toMatchObject({
    source: 'phys:.public.orders',
    target: 'phys:.public.customers',
    kind: 'fk',
    columnRefs: [{ from: 'customer_id', to: 'id' }],
  });
  // hydrated node carries its columns for the hover detail
  expect(graph.nodes.find(n => n.label === 'orders')?.columns).toEqual([
    { name: 'id', type: 'INT' },
  ]);
});

test('buildPhysicalGraph marks views by kind', () => {
  const meta = new Map<string, PhysicalTableMetadata>([
    ['v_sales', { name: 'v_sales', columns: [], view: 'SELECT 1' }],
  ]);
  const graph = buildPhysicalGraph([...meta.keys()], meta, {
    catalog: null,
    schema: 'public',
  });
  expect(graph.nodes[0].kind).toBe('view');
});

test('buildPhysicalGraph renders a node for a table with no metadata yet', () => {
  const graph = buildPhysicalGraph(['orders', 'pending'], new Map(), {
    catalog: null,
    schema: 'public',
  });
  expect(graph.nodes.map(n => n.label).sort()).toEqual(['orders', 'pending']);
});

test('buildPhysicalGraph marks failed tables with a warning decoration', () => {
  const graph = buildPhysicalGraph(
    ['orders', 'broken'],
    new Map<string, PhysicalTableMetadata>([
      ['orders', { name: 'orders', columns: [{ name: 'id', type: 'INT' }] }],
    ]),
    { catalog: null, schema: 'public' },
    new Set(['broken']),
  );
  const broken = graph.nodes.find(n => n.label === 'broken');
  const orders = graph.nodes.find(n => n.label === 'orders');
  expect(broken?.decorations?.validation?.[0].severity).toBe('warning');
  expect(orders?.decorations).toBeUndefined();
});

const bigModel = (n: number): SchemaGraphModel => ({
  nodes: Array.from({ length: n }, (_, i) => ({
    id: `n${i}`,
    label: `n${i}`,
    kind: 'table' as const,
  })),
  edges: Array.from({ length: n - 1 }, (_, i) => ({
    id: `e${i}`,
    source: `n${i}`,
    target: `n${i + 1}`,
    kind: 'fk' as const,
  })),
});

test('capGraph caps nodes and drops edges with a dropped endpoint', () => {
  const { model, droppedNodes } = capGraph(bigModel(10), 4, 100);
  expect(model.nodes).toHaveLength(4);
  expect(droppedNodes).toBe(6);
  // Every surviving edge connects two surviving nodes.
  const ids = new Set(model.nodes.map(n => n.id));
  expect(model.edges.every(e => ids.has(e.source) && ids.has(e.target))).toBe(
    true,
  );
});

test('capGraph keeps prioritised ids even past the cap position', () => {
  const { model } = capGraph(bigModel(10), 3, 100, new Set(['n9']));
  expect(model.nodes.map(n => n.id)).toContain('n9');
  expect(model.nodes).toHaveLength(3);
});

test('capGraph caps edges independently', () => {
  const { model, droppedEdges } = capGraph(bigModel(10), 100, 2);
  expect(model.edges).toHaveLength(2);
  expect(droppedEdges).toBe(7);
});
