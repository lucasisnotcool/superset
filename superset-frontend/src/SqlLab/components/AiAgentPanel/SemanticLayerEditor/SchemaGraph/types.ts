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

// Graph view domain model (wren_graph_view.md). Kept framework-agnostic so the
// pure transforms (ids/overlay/options) are unit-testable without React/ECharts.

export type LayerMode = 'physical' | 'mdl' | 'combined';

export type NodeKind = 'table' | 'view' | 'model';

export type Cardinality =
  | 'ONE_TO_ONE'
  | 'ONE_TO_MANY'
  | 'MANY_TO_ONE'
  | 'MANY_TO_MANY';

export type EdgeKind = 'fk' | 'relationship';

// S-C decoration model: integration-owned, all optional, rendered only when
// present (wren_graph_view.md §7.1). Extensions populate these; graph-core never
// requires them.
export interface NodeDecorations {
  validation?: { severity: 'error' | 'warning' | 'info'; message: string }[];
  status?: 'draft' | 'active';
  agentUsage?: 'matched' | 'candidate';
  provenance?: { documentIds?: string[] };
  instructions?: number;
}

export interface GraphNode {
  id: string;
  label: string;
  kind: NodeKind;
  catalog?: string | null;
  schema?: string | null;
  table?: string | null;
  // True when a physical table has a corresponding MDL model (combined view).
  modeled?: boolean;
  // Compact, render-time metadata (column count, metric count, etc.).
  metricCount?: number;
  columnCount?: number;
  hasCalculatedFields?: boolean;
  // Render-time carry for the hover quick-look (G1.4) — bounded list of columns
  // with type + key badges. Present once the table's metadata has hydrated.
  columns?: PhysicalColumn[];
  decorations?: NodeDecorations;
}

export interface GraphEdge {
  id: string;
  source: string;
  target: string;
  kind: EdgeKind;
  label?: string;
  cardinality?: Cardinality;
  // Render-time carry for the hover quick-look (G1.4): which columns the edge
  // joins, and (for MDL relationships) what it was derived from.
  columnRefs?: { from: string; to: string }[];
  relationshipName?: string | null;
  condition?: string | null;
}

export interface SchemaGraphModel {
  nodes: GraphNode[];
  edges: GraphEdge[];
}

// Theme tokens the graph renders with, sourced from the Superset/antd theme in
// SchemaGraph.tsx and threaded into the (pure) option/tooltip builders so they
// stay framework-free and unit-testable. All optional fields have safe defaults.
export interface GraphPalette {
  text: string;
  textMuted: string;
  bg: string;
  border: string;
  accent: string;
  // Node label: white-ish fill with a dark outline for contrast on any backdrop.
  labelText: string;
  labelOutline: string;
  // Key badges.
  pk: string;
  fk: string;
}

// --- Physical metadata (backend `table_metadata`) ----------------------------
// The shared `TableMetaData` type in src/hooks/apiResources/tables.ts omits the
// top-level `foreignKeys`/`primaryKey` the backend actually returns (the RTK
// `transformResponse` passes the whole JSON through, so they exist at runtime).
// We declare the richer shape we depend on here.

export interface ForeignKeyMeta {
  column_names: string[];
  referred_schema: string | null;
  referred_table: string;
  referred_columns: string[];
  name?: string | null;
}

export interface PrimaryKeyMeta {
  column_names: string[];
  name?: string | null;
}

export interface PhysicalColumn {
  name: string;
  type: string;
  keys?: { type: 'pk' | 'fk' | 'index' }[];
}

export interface PhysicalTableMetadata {
  name: string;
  columns: PhysicalColumn[];
  foreignKeys?: ForeignKeyMeta[];
  primaryKey?: PrimaryKeyMeta | null;
  view?: string;
}

// --- MDL manifest (camelCase, mirrors semantic_layer/mdl_schema.py) ----------

export interface MdlTableReference {
  catalog?: string | null;
  schema?: string | null;
  table?: string | null;
}

export interface MdlColumn {
  name: string;
  type?: string | null;
  isCalculated?: boolean;
  expression?: string | null;
  relationship?: string | null;
  notNull?: boolean;
}

export interface MdlModel {
  name: string;
  tableReference?: MdlTableReference | null;
  refSql?: string | null;
  columns?: MdlColumn[];
  primaryKey?: string | null;
}

export interface MdlRelationship {
  name: string;
  models?: string[];
  joinType?: string | null;
  condition?: string | null;
}

export interface MdlMetric {
  name: string;
  baseObject?: string | null;
  expression?: string | null;
}

export interface MdlManifest {
  catalog?: string;
  schema?: string;
  models?: MdlModel[];
  relationships?: MdlRelationship[];
  metrics?: MdlMetric[];
  views?: { name: string }[];
  cubes?: { name: string; baseObject?: string | null }[];
}
