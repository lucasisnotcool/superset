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

// Physical-schema data layer (wren_graph_view.md G1) over the existing RTK
// resource (src/hooks/apiResources/tables.ts) — RTK provides caching/dedup; this
// hook adds the graph-specific concerns: seed selection (D11), bounded-
// concurrency hydration (D6), and table-level graph assembly. Lazy RTK triggers
// let us fetch a dynamic N of tables imperatively without manual dispatch typing.

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  useLazyTablesQuery,
  useLazyTableMetadataQuery,
} from 'src/hooks/apiResources/tables';
import {
  ConcurrencyLimiter,
  buildPhysicalGraph,
  selectSeedTables,
} from './graphData';
import { PhysicalTableMetadata, SchemaGraphModel } from './types';
import { MAX_INFLIGHT, SEED_LIMIT } from './graphConfig';

export interface UseSchemaGraphDataArgs {
  databaseId?: number;
  catalog?: string | null;
  schema?: string | null;
  // Only does any work (or network) when true — the tab is open.
  enabled: boolean;
  // Zero-cost user signal for seeding (SQL Lab left-bar tables, D11).
  userTables?: string[];
}

export interface UseSchemaGraphDataResult {
  universe: string[];
  physicalGraph: SchemaGraphModel;
  loadedTables: string[];
  failedTables: string[];
  isLoadingUniverse: boolean;
  isHydrating: boolean;
  error: string | null;
  // Hydrate one more table (search-to-focus); no-op if already loaded.
  loadTable: (name: string) => void;
}

export function useSchemaGraphData({
  databaseId,
  catalog,
  schema,
  enabled,
  userTables = [],
}: UseSchemaGraphDataArgs): UseSchemaGraphDataResult {
  const [triggerTables] = useLazyTablesQuery();
  const [triggerMeta] = useLazyTableMetadataQuery();

  const [universe, setUniverse] = useState<string[]>([]);
  // The tables to render as nodes (seed + searched), independent of whether their
  // metadata loaded — so a table never disappears on a metadata 500.
  const [nodeTables, setNodeTables] = useState<string[]>([]);
  const [metaByTable, setMetaByTable] = useState<
    Map<string, PhysicalTableMetadata>
  >(new Map());
  const [failedTables, setFailedTables] = useState<Set<string>>(new Set());
  const [isLoadingUniverse, setIsLoadingUniverse] = useState(false);
  const [isHydrating, setIsHydrating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const limiterRef = useRef(new ConcurrencyLimiter(MAX_INFLIGHT));

  const hydrate = useCallback(
    async (tables: string[]) => {
      if (!databaseId || !schema || tables.length === 0) {
        return;
      }
      // Render the requested tables as nodes immediately; metadata fills in (or
      // is marked failed) as it resolves.
      setNodeTables(prev => {
        const merged = new Set(prev);
        tables.forEach(table => merged.add(table));
        return merged.size === prev.length ? prev : [...merged];
      });
      setIsHydrating(true);
      try {
        await Promise.all(
          tables.map(table =>
            limiterRef.current.run(async () => {
              try {
                const meta = await triggerMeta({
                  dbId: databaseId,
                  catalog: catalog ?? undefined,
                  schema,
                  table,
                }).unwrap();
                setFailedTables(prev => {
                  if (!prev.has(table)) {
                    return prev;
                  }
                  const next = new Set(prev);
                  next.delete(table);
                  return next;
                });
                setMetaByTable(prev => {
                  if (prev.has(table)) {
                    return prev;
                  }
                  const next = new Map(prev);
                  next.set(table, meta as unknown as PhysicalTableMetadata);
                  return next;
                });
              } catch {
                // Degrade-closed: the table still renders as a node, marked as
                // "metadata could not be loaded" (e.g. a table_metadata 500),
                // rather than silently vanishing.
                setFailedTables(prev => {
                  if (prev.has(table)) {
                    return prev;
                  }
                  const next = new Set(prev);
                  next.add(table);
                  return next;
                });
              }
            }),
          ),
        );
      } finally {
        setIsHydrating(false);
      }
    },
    [databaseId, catalog, schema, triggerMeta],
  );

  useEffect(() => {
    if (!enabled || !databaseId || !schema) {
      setUniverse([]);
      setNodeTables([]);
      setMetaByTable(new Map());
      setFailedTables(new Set());
      return undefined;
    }
    let cancelled = false;
    setIsLoadingUniverse(true);
    setError(null);
    triggerTables({
      dbId: databaseId,
      catalog: catalog ?? undefined,
      schema,
      forceRefresh: false,
    })
      .unwrap()
      .then(data => {
        if (cancelled) {
          return;
        }
        const names = (data.options ?? [])
          .map(option => option.value)
          .filter((value): value is string => Boolean(value));
        setUniverse(names);
        setNodeTables([]);
        setMetaByTable(new Map());
        setFailedTables(new Set());
        const seed = selectSeedTables({
          universe: names,
          userTables,
          limit: SEED_LIMIT,
        });
        hydrate(seed);
      })
      .catch(ex => {
        if (!cancelled) {
          setError(ex instanceof Error ? ex.message : String(ex));
        }
      })
      .finally(() => {
        if (!cancelled) {
          setIsLoadingUniverse(false);
        }
      });
    return () => {
      cancelled = true;
    };
    // userTables is read once at fetch time; including it would refetch on every
    // left-bar change. Scope change (db/catalog/schema/enabled) drives reloads.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, databaseId, catalog, schema, triggerTables, hydrate]);

  const loadTable = useCallback(
    (name: string) => {
      // Re-hydrate if not yet loaded (also retries a previously failed table).
      if (!metaByTable.has(name)) {
        hydrate([name]);
      }
    },
    [metaByTable, hydrate],
  );

  const physicalGraph = useMemo(
    () =>
      buildPhysicalGraph(
        nodeTables,
        metaByTable,
        { catalog, schema },
        failedTables,
      ),
    [nodeTables, metaByTable, failedTables, catalog, schema],
  );

  return {
    universe,
    physicalGraph,
    loadedTables: [...metaByTable.keys()],
    failedTables: [...failedTables],
    isLoadingUniverse,
    isHydrating,
    error,
    loadTable,
  };
}
