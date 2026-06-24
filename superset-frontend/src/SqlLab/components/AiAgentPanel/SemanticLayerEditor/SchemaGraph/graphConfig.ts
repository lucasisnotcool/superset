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

// Graph view tuning constants (wren_graph_view.md §4.3). Frontend-only by
// design (D8) — no backend config so the limits stay off the contended
// config.py. All are performance guards for large schemas.

export const SEED_LIMIT = 10; // initial table nodes on open (§4.2)
export const MAX_NODES = 200; // hard cap on rendered nodes; LRU-evict beyond
export const MAX_EDGES = 400; // companion edge cap
export const EXPAND_FANOUT_CAP = 25; // max neighbors added per expand action
// Concurrent table_metadata fetches. Kept modest as defense-in-depth: the
// backend introspection race that surfaced here (a shared engine's connect
// listeners mutated under concurrency) is fixed at source in models/core.py,
// but a gentler fan-out also eases load on the target database.
export const MAX_INFLIGHT = 3;
