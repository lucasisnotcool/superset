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

// S-A — stable node/edge IDs (wren_graph_view.md §7.1). Every integration
// addresses nodes by these ids; the model⇄node resolver is the single source of
// truth so extensions never re-derive ids.

import { EdgeKind, MdlModel } from './types';

const part = (value: string | null | undefined): string => value ?? '';

/** Physical table node id: `phys:{catalog}.{schema}.{table}`. */
export const physicalNodeId = (
  catalog: string | null | undefined,
  schema: string | null | undefined,
  table: string,
): string => `phys:${part(catalog)}.${part(schema)}.${table}`;

/** Semantic model node id: `mdl:{modelName}`. */
export const modelNodeId = (modelName: string): string => `mdl:${modelName}`;

/** Stable edge id, direction- and kind-aware. */
export const edgeId = (
  source: string,
  target: string,
  kind: EdgeKind,
): string => `e:${source}->${target}:${kind}`;

/**
 * Resolve the physical table node an MDL model grounds onto via its
 * `tableReference`, or null when the model has no physical mapping
 * (model-without-mapping — surfaced as a coverage gap elsewhere).
 */
export const physicalIdForModel = (model: MdlModel): string | null => {
  const ref = model.tableReference;
  if (!ref || !ref.table) {
    return null;
  }
  return physicalNodeId(ref.catalog, ref.schema, ref.table);
};
