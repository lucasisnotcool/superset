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

// Pure data-layer helpers for the physical graph (wren_graph_view.md G0.2/G1):
// seed selection (D11), a concurrency limiter, an LRU window, and an incremental
// reverse-FK index. These are framework-free so they are fully unit-testable;
// the React/RTK hook composes them on top of src/hooks/apiResources/tables.ts.

import {
  ForeignKeyMeta,
  GraphEdge,
  GraphNode,
  PhysicalTableMetadata,
  SchemaGraphModel,
} from './types';
import { edgeId, physicalNodeId } from './ids';

/**
 * Choose the seed table set on open (§4.2, D11), all zero-cost signals:
 *   1. tables the agent most recently used (matched/candidate models),
 *   2. tables the user has open in the SQL Lab left bar,
 *   3. fallback: the first N of the table-name universe.
 * Inputs are intersected with the universe and de-duplicated, preserving the
 * priority order. Never parses SQL.
 */
export function selectSeedTables({
  universe,
  agentTables = [],
  userTables = [],
  limit,
}: {
  universe: string[];
  agentTables?: string[];
  userTables?: string[];
  limit: number;
}): string[] {
  if (limit <= 0) {
    return [];
  }
  const known = new Set(universe);
  const seen = new Set<string>();
  const out: string[] = [];
  const take = (names: string[]) => {
    for (const name of names) {
      if (out.length >= limit) {
        return;
      }
      if (known.has(name) && !seen.has(name)) {
        seen.add(name);
        out.push(name);
      }
    }
  };
  take(agentTables);
  take(userTables);
  take(universe);
  return out;
}

/**
 * Bounded-concurrency runner (§4.6) — at most `max` tasks in flight; the rest
 * queue. Keeps expanding a hub from firing a request storm at table_metadata.
 */
export class ConcurrencyLimiter {
  private readonly max: number;

  private active = 0;

  private queue: (() => void)[] = [];

  constructor(max: number) {
    this.max = Math.max(1, max);
  }

  run<T>(task: () => Promise<T>): Promise<T> {
    return new Promise<T>((resolve, reject) => {
      const start = () => {
        this.active += 1;
        task()
          .then(resolve, reject)
          .finally(() => {
            this.active -= 1;
            const next = this.queue.shift();
            if (next) {
              next();
            }
          });
      };
      if (this.active < this.max) {
        start();
      } else {
        this.queue.push(start);
      }
    });
  }
}

/**
 * Fixed-capacity LRU set of node ids (§4.3). `touch` records use; `add` inserts
 * and returns the ids evicted to stay within capacity (least-recently-touched
 * first). The caller unsubscribes/aborts evicted nodes' fetches.
 */
export class LruWindow {
  private readonly capacity: number;

  // Map preserves insertion order; re-insertion on touch moves to newest.
  private order = new Map<string, true>();

  constructor(capacity: number) {
    this.capacity = Math.max(1, capacity);
  }

  has(id: string): boolean {
    return this.order.has(id);
  }

  get size(): number {
    return this.order.size;
  }

  touch(id: string): void {
    if (this.order.has(id)) {
      this.order.delete(id);
      this.order.set(id, true);
    }
  }

  add(id: string): string[] {
    if (this.order.has(id)) {
      this.touch(id);
      return [];
    }
    this.order.set(id, true);
    const evicted: string[] = [];
    while (this.order.size > this.capacity) {
      const oldest = this.order.keys().next().value as string | undefined;
      if (oldest === undefined) {
        break;
      }
      this.order.delete(oldest);
      evicted.push(oldest);
    }
    return evicted;
  }
}

/**
 * Add a table's forward foreign keys into a reverse index keyed by the referred
 * table name (§4.5). Lets "who references me" fill in incrementally as metadata
 * loads, without a global FK crawl.
 */
export function addToReverseFkIndex(
  index: Map<string, ForeignKeyMeta[]>,
  fromTable: string,
  foreignKeys: ForeignKeyMeta[] | undefined,
): void {
  for (const fk of foreignKeys ?? []) {
    const list = index.get(fk.referred_table) ?? [];
    // Stamp the originating table so callers can build the edge endpoints.
    list.push({ ...fk, name: fk.name ?? fromTable });
    index.set(fk.referred_table, list);
  }
}

/**
 * Cap a graph to at most `maxNodes` nodes and `maxEdges` edges for render
 * safety (§4.3). `keepIds` are prioritised (never dropped while under cap), then
 * remaining nodes in their existing order. Edges are kept only when both
 * endpoints survive. Returns a new model plus how many of each were dropped.
 */
export function capGraph(
  model: SchemaGraphModel,
  maxNodes: number,
  maxEdges: number,
  keepIds: ReadonlySet<string> = new Set(),
): { model: SchemaGraphModel; droppedNodes: number; droppedEdges: number } {
  const prioritised = [
    ...model.nodes.filter(n => keepIds.has(n.id)),
    ...model.nodes.filter(n => !keepIds.has(n.id)),
  ];
  const kept = prioritised.slice(0, Math.max(0, maxNodes));
  const keptIds = new Set(kept.map(n => n.id));
  const edges: GraphEdge[] = [];
  for (const edge of model.edges) {
    if (edges.length >= maxEdges) {
      break;
    }
    if (keptIds.has(edge.source) && keptIds.has(edge.target)) {
      edges.push(edge);
    }
  }
  return {
    model: { nodes: kept, edges },
    droppedNodes: model.nodes.length - kept.length,
    droppedEdges: model.edges.length - edges.length,
  };
}

/**
 * Build a table-level physical graph. A node is emitted for **every** table in
 * `tables` — even before its metadata loads or if introspection failed — so a
 * table never silently disappears (e.g. a `table_metadata` 500). Metadata, when
 * present, enriches the node (columns, kind) and contributes FK edges; a table
 * in `failedTables` is marked with a warning decoration. An FK edge is emitted
 * only when **both** endpoints are in `tables` (§4.5 — edges fill in as loaded).
 */
export function buildPhysicalGraph(
  tables: string[],
  metadataByTable: Map<string, PhysicalTableMetadata>,
  scope: { catalog?: string | null; schema?: string | null },
  failedTables: ReadonlySet<string> = new Set(),
): SchemaGraphModel {
  const nodes: GraphNode[] = [];
  const idByTable = new Map<string, string>();
  for (const table of tables) {
    const meta = metadataByTable.get(table);
    const id = physicalNodeId(scope.catalog, scope.schema, table);
    idByTable.set(table, id);
    nodes.push({
      id,
      label: table,
      kind: meta?.view ? 'view' : 'table',
      catalog: scope.catalog ?? null,
      schema: scope.schema ?? null,
      table,
      columnCount: meta?.columns?.length ?? 0,
      columns: meta?.columns,
      decorations: failedTables.has(table)
        ? {
            validation: [
              {
                severity: 'warning',
                message: 'Table metadata could not be loaded.',
              },
            ],
          }
        : undefined,
    });
  }
  const edges: GraphEdge[] = [];
  const seenEdge = new Set<string>();
  for (const table of tables) {
    const meta = metadataByTable.get(table);
    const source = idByTable.get(table);
    if (!meta || !source) {
      continue;
    }
    for (const fk of meta.foreignKeys ?? []) {
      const target = idByTable.get(fk.referred_table);
      if (!target) {
        continue; // referred table not in the rendered set (yet)
      }
      const id = edgeId(source, target, 'fk');
      if (seenEdge.has(id)) {
        continue;
      }
      seenEdge.add(id);
      // Pair each constrained column with its referred column positionally
      // (the backend returns parallel arrays) for the hover detail.
      const columnRefs = fk.column_names.map((from, i) => ({
        from,
        to: fk.referred_columns[i] ?? '?',
      }));
      edges.push({
        id,
        source,
        target,
        kind: 'fk',
        label: fk.column_names.join(', '),
        columnRefs,
      });
    }
  }
  return { nodes, edges };
}
