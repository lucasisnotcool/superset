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
import { edgeTooltipHtml, escapeHtml, nodeTooltipHtml } from './tooltips';
import { GraphEdge, GraphNode } from './types';

test('escapeHtml neutralises HTML-significant characters', () => {
  expect(escapeHtml('<img src=x onerror="1">')).toBe(
    '&lt;img src=x onerror=&quot;1&quot;&gt;',
  );
  expect(escapeHtml(undefined)).toBe('');
});

test('node tooltip lists columns with type and key badges', () => {
  const node: GraphNode = {
    id: 'phys:..orders',
    label: 'orders',
    kind: 'table',
    modeled: true,
    columns: [
      { name: 'id', type: 'INTEGER', keys: [{ type: 'pk' }] },
      { name: 'customer_id', type: 'INTEGER', keys: [{ type: 'fk' }] },
      { name: 'total', type: 'NUMERIC' },
    ],
  };
  const html = nodeTooltipHtml(node);
  expect(html).toContain('orders');
  expect(html).toContain('id');
  expect(html).toContain('INTEGER');
  expect(html).toContain('🔑'); // PK icon
  expect(html).toContain('customer_id');
  expect(html).toContain('🔗'); // FK icon
  expect(html).toContain('Modeled');
});

test('node tooltip caps long column lists with a "+N more" line', () => {
  const columns = Array.from({ length: 20 }, (_, i) => ({
    name: `c${i}`,
    type: 'TEXT',
  }));
  const html = nodeTooltipHtml({
    id: 'phys:..wide',
    label: 'wide',
    kind: 'table',
    columns,
  });
  expect(html).toContain('+5 more');
});

test('node tooltip surfaces the metadata-failure note instead of columns', () => {
  const html = nodeTooltipHtml({
    id: 'phys:..bad',
    label: 'bad',
    kind: 'table',
    decorations: {
      validation: [
        { severity: 'warning', message: 'Table metadata could not be loaded.' },
      ],
    },
  });
  expect(html).toContain('Table metadata could not be loaded.');
  expect(html).not.toContain('No column metadata loaded.');
});

test('node tooltip escapes a malicious table/column name', () => {
  const html = nodeTooltipHtml({
    id: 'phys:..x',
    label: '<script>alert(1)</script>',
    kind: 'table',
    columns: [{ name: '<b>', type: 'TEXT' }],
  });
  expect(html).not.toContain('<script>');
  expect(html).toContain('&lt;script&gt;');
});

test('FK edge tooltip shows the column mapping', () => {
  const edge: GraphEdge = {
    id: 'e:a->b:fk',
    source: 'a',
    target: 'b',
    kind: 'fk',
    columnRefs: [{ from: 'customer_id', to: 'id' }],
  };
  const html = edgeTooltipHtml(edge);
  expect(html).toContain('Foreign key');
  expect(html).toContain('customer_id');
  expect(html).toContain('→');
  expect(html).toContain('id');
});

test('relationship edge tooltip shows name, cardinality, and condition', () => {
  const edge: GraphEdge = {
    id: 'e:a->b:relationship',
    source: 'a',
    target: 'b',
    kind: 'relationship',
    relationshipName: 'OrdersCustomers',
    cardinality: 'MANY_TO_ONE',
    condition: 'orders.customer_id = customers.id',
    columnRefs: [{ from: 'customer_id', to: 'id' }],
  };
  const html = edgeTooltipHtml(edge);
  expect(html).toContain('OrdersCustomers');
  expect(html).toContain('MDL relationship');
  expect(html).toContain('MANY_TO_ONE');
  expect(html).toContain('customer_id');
  expect(html).toContain('→');
  expect(html).toContain('orders.customer_id = customers.id');
});
