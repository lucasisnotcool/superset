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
  applyValidations,
  countUnattached,
  mentionsEntity,
} from './validationOverlay';
import { SchemaGraphModel } from './types';

const model: SchemaGraphModel = {
  nodes: [
    { id: 'mdl:Orders', label: 'Orders', kind: 'model' },
    { id: 'mdl:Order', label: 'Order', kind: 'model' },
  ],
  edges: [],
};

test('mentionsEntity matches whole identifiers only', () => {
  expect(mentionsEntity('Duplicate model name: Orders.', 'Orders')).toBe(true);
  // "Order" must not match inside "Orders"
  expect(mentionsEntity('Duplicate model name: Orders.', 'Order')).toBe(false);
  expect(mentionsEntity('Column id in Order is bad', 'Order')).toBe(true);
});

test('applyValidations attaches a message to the mentioned node only', () => {
  const decorated = applyValidations(model, [
    {
      severity: 'error',
      message: 'Duplicate model name: Orders.',
      code: 'duplicate_model',
    },
  ]);
  const orders = decorated.nodes.find(n => n.label === 'Orders');
  const order = decorated.nodes.find(n => n.label === 'Order');
  expect(orders?.decorations?.validation).toHaveLength(1);
  expect(orders?.decorations?.validation?.[0].severity).toBe('error');
  expect(order?.decorations?.validation).toBeUndefined();
});

test('applyValidations is a no-op with no messages', () => {
  expect(applyValidations(model, [])).toBe(model);
});

test('countUnattached counts messages mentioning no node', () => {
  const messages = [
    { severity: 'error' as const, message: 'Orders has an issue' },
    { severity: 'warning' as const, message: 'Something global went wrong' },
  ];
  expect(countUnattached(model, messages)).toBe(1);
});
