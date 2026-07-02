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
import { diffLines } from './index';

test('identical texts diff to all-same lines', () => {
  expect(diffLines('a\nb', 'a\nb')).toEqual([
    { type: 'same', text: 'a' },
    { type: 'same', text: 'b' },
  ]);
});

test('an edited line shows as del + add', () => {
  const diff = diffLines('keep\nold line\nend', 'keep\nnew line\nend');
  expect(diff).toEqual([
    { type: 'same', text: 'keep' },
    { type: 'del', text: 'old line' },
    { type: 'add', text: 'new line' },
    { type: 'same', text: 'end' },
  ]);
});

test('pure additions and deletions at the tail', () => {
  expect(diffLines('a', 'a\nb')).toEqual([
    { type: 'same', text: 'a' },
    { type: 'add', text: 'b' },
  ]);
  expect(diffLines('a\nb', 'a')).toEqual([
    { type: 'same', text: 'a' },
    { type: 'del', text: 'b' },
  ]);
});
