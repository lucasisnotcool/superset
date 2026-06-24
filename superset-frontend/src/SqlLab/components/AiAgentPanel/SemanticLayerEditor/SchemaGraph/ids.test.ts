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
import { edgeId, modelNodeId, physicalIdForModel, physicalNodeId } from './ids';

test('physicalNodeId is stable and namespaced', () => {
  expect(physicalNodeId('cat', 'public', 'orders')).toBe(
    'phys:cat.public.orders',
  );
});

test('physicalNodeId tolerates null catalog/schema', () => {
  expect(physicalNodeId(null, null, 'orders')).toBe('phys:..orders');
  expect(physicalNodeId(undefined, 'public', 'orders')).toBe(
    'phys:.public.orders',
  );
});

test('modelNodeId and edgeId are namespaced and direction/kind aware', () => {
  expect(modelNodeId('Orders')).toBe('mdl:Orders');
  expect(edgeId('mdl:A', 'mdl:B', 'relationship')).toBe(
    'e:mdl:A->mdl:B:relationship',
  );
  expect(edgeId('mdl:A', 'mdl:B', 'fk')).not.toBe(
    edgeId('mdl:A', 'mdl:B', 'relationship'),
  );
});

test('physicalIdForModel resolves via tableReference, null without a table', () => {
  expect(
    physicalIdForModel({
      name: 'Orders',
      tableReference: { catalog: 'c', schema: 'public', table: 'orders' },
    }),
  ).toBe('phys:c.public.orders');
  expect(physicalIdForModel({ name: 'Orders' })).toBeNull();
  expect(
    physicalIdForModel({
      name: 'Orders',
      tableReference: { schema: 'public' },
    }),
  ).toBeNull();
});
