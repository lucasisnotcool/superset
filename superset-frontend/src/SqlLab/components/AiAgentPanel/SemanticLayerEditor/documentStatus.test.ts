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
import { isPendingDocumentStatus, formatBytes } from './documentStatus';

test('isPendingDocumentStatus is true only for in-flight states', () => {
  expect(isPendingDocumentStatus('uploaded')).toBe(true);
  expect(isPendingDocumentStatus('extracting')).toBe(true);
});

test('isPendingDocumentStatus is false for terminal and legacy states', () => {
  ['extracted', 'needs_ocr', 'error', 'indexed', 'approved', 'unknown'].forEach(
    status => expect(isPendingDocumentStatus(status)).toBe(false),
  );
});

test('formatBytes renders compact units', () => {
  expect(formatBytes(512)).toBe('512 B');
  expect(formatBytes(2048)).toBe('2.0 KB');
  expect(formatBytes(3 * 1024 * 1024)).toBe('3.0 MB');
});
