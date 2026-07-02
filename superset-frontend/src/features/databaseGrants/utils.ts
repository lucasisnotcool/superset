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
import type { DatabaseGrant, GrantStatus, MyDatabaseGrant } from './types';

/**
 * Parse an admin-pasted username list. Accepts any mix of newline, comma,
 * semicolon, and whitespace separators; entries are lowercased (grants match
 * case-insensitively, and the backend stores the canonical lowercased form)
 * and deduplicated while preserving first-seen order.
 */
export function parseUsernames(raw: string): string[] {
  const seen = new Set<string>();
  const usernames: string[] = [];
  raw.split(/[\s,;]+/).forEach(token => {
    const username = token.trim().toLowerCase();
    if (username && !seen.has(username)) {
      seen.add(username);
      usernames.push(username);
    }
  });
  return usernames;
}

/** Lifecycle: pending (no user yet) → claimed (role attached) → acknowledged. */
export function grantStatus(grant: DatabaseGrant): GrantStatus {
  if (grant.acknowledged_at) {
    return 'acknowledged';
  }
  if (grant.user_id !== null && grant.user_id !== undefined) {
    return 'claimed';
  }
  return 'pending';
}

/**
 * Human-readable connection signature, e.g. "bob@dbhost:5432/warehouse".
 * Falls back to the display name when the URI could not be parsed
 * server-side. Never contains the password (the server never sends it).
 */
export function connectionSignature(grant: MyDatabaseGrant): string {
  if (!grant.host) {
    return grant.database_name;
  }
  const user = grant.connection_username ? `${grant.connection_username}@` : '';
  const port = grant.port ? `:${grant.port}` : '';
  const database = grant.database ? `/${grant.database}` : '';
  return `${user}${grant.host}${port}${database}`;
}
