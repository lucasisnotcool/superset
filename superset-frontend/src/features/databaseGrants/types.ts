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

/** One pre-approved (possibly claimed) grant row, as listed by the API. */
export interface DatabaseGrant {
  id: number;
  uuid?: string;
  username: string;
  database_id: number;
  database?: {
    id: number;
    database_name: string;
  };
  user_id: number | null;
  claimed_at: string | null;
  acknowledged_at: string | null;
  created_on: string | null;
  changed_on_delta_humanized?: string;
  created_by?: {
    id: number;
    first_name: string;
    last_name: string;
  } | null;
}

export type GrantStatus = 'pending' | 'claimed' | 'acknowledged';

/** Result payload of the bulk-create endpoint. */
export interface GrantCreationResult {
  created: string[];
  skipped: string[];
  claimed_usernames: string[];
}

/**
 * One of the caller's own unacknowledged grants, with the connection
 * signature (never the password) — drives the notification dialog.
 */
export interface MyDatabaseGrant {
  id: number;
  database_id: number;
  database_name: string;
  backend: string | null;
  driver: string | null;
  host: string | null;
  port: number | null;
  database: string | null;
  connection_username: string | null;
  granted_on: string | null;
}
