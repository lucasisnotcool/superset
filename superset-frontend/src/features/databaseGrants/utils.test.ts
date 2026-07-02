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
import type { DatabaseGrant, MyDatabaseGrant } from './types';
import { connectionSignature, grantStatus, parseUsernames } from './utils';

const grant = (over: Partial<DatabaseGrant> = {}): DatabaseGrant => ({
  id: 1,
  username: 'alice@example.com',
  database_id: 7,
  user_id: null,
  claimed_at: null,
  acknowledged_at: null,
  created_on: '2026-07-01T00:00:00Z',
  ...over,
});

const myGrant = (over: Partial<MyDatabaseGrant> = {}): MyDatabaseGrant => ({
  id: 1,
  database_id: 7,
  database_name: 'warehouse_conn',
  backend: 'postgresql',
  driver: 'psycopg2',
  host: 'dbhost',
  port: 5432,
  database: 'warehouse',
  connection_username: 'bob',
  granted_on: '2026-07-01T00:00:00Z',
  ...over,
});

test('parseUsernames splits on newlines, commas, semicolons, and spaces', () => {
  expect(parseUsernames('a@x.io, b@x.io\nc@x.io;d@x.io  e@x.io')).toEqual([
    'a@x.io',
    'b@x.io',
    'c@x.io',
    'd@x.io',
    'e@x.io',
  ]);
});

test('parseUsernames lowercases and dedupes, preserving first-seen order', () => {
  expect(parseUsernames('Bob@X.io\nalice@x.io\nBOB@x.io')).toEqual([
    'bob@x.io',
    'alice@x.io',
  ]);
});

test('parseUsernames drops empty tokens', () => {
  expect(parseUsernames('  \n , ;  ')).toEqual([]);
  expect(parseUsernames('')).toEqual([]);
});

test('grantStatus follows the pending → claimed → acknowledged lifecycle', () => {
  expect(grantStatus(grant())).toBe('pending');
  expect(
    grantStatus(grant({ user_id: 3, claimed_at: '2026-07-01T01:00:00Z' })),
  ).toBe('claimed');
  expect(
    grantStatus(
      grant({
        user_id: 3,
        claimed_at: '2026-07-01T01:00:00Z',
        acknowledged_at: '2026-07-01T02:00:00Z',
      }),
    ),
  ).toBe('acknowledged');
});

test('connectionSignature formats user@host:port/database', () => {
  expect(connectionSignature(myGrant())).toBe('bob@dbhost:5432/warehouse');
});

test('connectionSignature tolerates missing parts', () => {
  expect(
    connectionSignature(
      myGrant({ connection_username: null, port: null, database: null }),
    ),
  ).toBe('dbhost');
  // Unparseable URI server-side: fall back to the display name.
  expect(connectionSignature(myGrant({ host: null }))).toBe('warehouse_conn');
});
