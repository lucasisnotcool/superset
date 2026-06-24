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

// MDL → semantic graph (wren_graph_view.md G2.1). Pure: the whole manifest is
// already in memory, so the semantic graph builds with zero network calls. Also
// provides coverage overlay (modeled vs unmodeled) for the combined view.

import {
  Cardinality,
  GraphEdge,
  GraphNode,
  MdlManifest,
  MdlModel,
  SchemaGraphModel,
} from './types';
import { edgeId, modelNodeId, physicalIdForModel } from './ids';

const CARDINALITIES: ReadonlySet<string> = new Set([
  'ONE_TO_ONE',
  'ONE_TO_MANY',
  'MANY_TO_ONE',
  'MANY_TO_MANY',
]);

/** Parse an MDL file's JSON content into a manifest, or null when invalid. */
export function parseManifest(content: string): MdlManifest | null {
  try {
    const parsed = JSON.parse(content) as MdlManifest;
    if (!parsed || typeof parsed !== 'object') {
      return null;
    }
    return parsed;
  } catch {
    return null;
  }
}

/**
 * Merge several MDL file contents into one manifest (a project's models can span
 * files). Later files append; duplicate model names keep the first seen.
 */
export function mergeManifests(contents: string[]): MdlManifest {
  const models: MdlModel[] = [];
  const relationships: MdlManifest['relationships'] = [];
  const metrics: MdlManifest['metrics'] = [];
  const seenModel = new Set<string>();
  const seenRel = new Set<string>();
  for (const content of contents) {
    const manifest = parseManifest(content);
    if (!manifest) {
      continue;
    }
    for (const model of manifest.models ?? []) {
      if (model?.name && !seenModel.has(model.name)) {
        seenModel.add(model.name);
        models.push(model);
      }
    }
    for (const rel of manifest.relationships ?? []) {
      if (rel?.name && !seenRel.has(rel.name)) {
        seenRel.add(rel.name);
        relationships.push(rel);
      }
    }
    for (const metric of manifest.metrics ?? []) {
      if (metric?.name) {
        metrics.push(metric);
      }
    }
  }
  return { models, relationships, metrics };
}

const cardinalityOf = (joinType?: string | null): Cardinality | undefined =>
  joinType && CARDINALITIES.has(joinType)
    ? (joinType as Cardinality)
    : undefined;

/**
 * Build the semantic graph: one node per model (with column/metric counts) and
 * one relationship edge per MDL relationship that names two known models.
 * Relationship endpoints that reference unknown models are skipped (they would
 * be coverage errors, surfaced separately).
 */
export function buildSemanticGraph(manifest: MdlManifest): SchemaGraphModel {
  const models = manifest.models ?? [];
  const metricsByObject = new Map<string, number>();
  for (const metric of manifest.metrics ?? []) {
    if (metric.baseObject) {
      metricsByObject.set(
        metric.baseObject,
        (metricsByObject.get(metric.baseObject) ?? 0) + 1,
      );
    }
  }
  const nodes: GraphNode[] = [];
  const modelNames = new Set<string>();
  for (const model of models) {
    if (!model?.name) {
      continue;
    }
    modelNames.add(model.name);
    nodes.push({
      id: modelNodeId(model.name),
      label: model.name,
      kind: 'model',
      table: model.tableReference?.table ?? null,
      schema: model.tableReference?.schema ?? null,
      catalog: model.tableReference?.catalog ?? null,
      columnCount: model.columns?.length ?? 0,
      metricCount: metricsByObject.get(model.name) ?? 0,
      hasCalculatedFields: (model.columns ?? []).some(c => c.isCalculated),
    });
  }
  const edges: GraphEdge[] = [];
  const seen = new Set<string>();
  for (const rel of manifest.relationships ?? []) {
    const [a, b] = rel.models ?? [];
    if (!a || !b || !modelNames.has(a) || !modelNames.has(b)) {
      continue;
    }
    const source = modelNodeId(a);
    const target = modelNodeId(b);
    const id = edgeId(source, target, 'relationship');
    if (seen.has(id)) {
      continue;
    }
    seen.add(id);
    edges.push({
      id,
      source,
      target,
      kind: 'relationship',
      label: rel.name,
      cardinality: cardinalityOf(rel.joinType),
      relationshipName: rel.name,
      condition: rel.condition ?? null,
      columnRefs: parseConditionRefs(rel.condition),
    });
  }
  return { nodes, edges };
}

/**
 * Best-effort parse of an MDL relationship `condition` into column pairs for the
 * hover detail (e.g. `"orders.customer_id = customers.id"` → `customer_id → id`).
 * The raw condition is always carried separately; this only enriches the display
 * when the common `a.x = b.y [AND …]` shape is recognised, never throws.
 */
export function parseConditionRefs(
  condition?: string | null,
): { from: string; to: string }[] | undefined {
  if (!condition) {
    return undefined;
  }
  const refs: { from: string; to: string }[] = [];
  for (const clause of condition.split(/\bAND\b/i)) {
    const [lhs, rhs] = clause.split('=');
    if (lhs && rhs) {
      const from = lhs.trim().split('.').pop()?.trim();
      const to = rhs.trim().split('.').pop()?.trim();
      if (from && to) {
        refs.push({ from, to });
      }
    }
  }
  return refs.length > 0 ? refs : undefined;
}

/**
 * Mark which physical table nodes are modeled by the manifest (combined view
 * coverage, G2.2). Returns a new node array; non-table nodes pass through.
 */
export function applyCoverage(
  physical: SchemaGraphModel,
  manifest: MdlManifest,
): SchemaGraphModel {
  const modeledPhysicalIds = new Set<string>();
  for (const model of manifest.models ?? []) {
    const id = physicalIdForModel(model);
    if (id) {
      modeledPhysicalIds.add(id);
    }
  }
  return {
    nodes: physical.nodes.map(node =>
      node.kind === 'model'
        ? node
        : { ...node, modeled: modeledPhysicalIds.has(node.id) },
    ),
    edges: physical.edges,
  };
}

/**
 * Combined view (G2.2): physical tables with coverage, plus MDL relationship
 * edges drawn between the **physical** tables their models map to (via
 * `tableReference`). A relationship edge is added only when both endpoints'
 * physical nodes are present in the physical graph. Physical FK edges are kept,
 * so the two edge sets are visible side by side — divergence is the signal.
 */
export function composeCombined(
  physical: SchemaGraphModel,
  manifest: MdlManifest,
): SchemaGraphModel {
  const covered = applyCoverage(physical, manifest);
  const presentIds = new Set(covered.nodes.map(n => n.id));
  const physicalIdByModel = new Map<string, string>();
  for (const model of manifest.models ?? []) {
    const id = physicalIdForModel(model);
    if (model.name && id) {
      physicalIdByModel.set(model.name, id);
    }
  }
  const relEdges: GraphEdge[] = [];
  const seen = new Set<string>();
  for (const rel of manifest.relationships ?? []) {
    const [a, b] = rel.models ?? [];
    const source = a ? physicalIdByModel.get(a) : undefined;
    const target = b ? physicalIdByModel.get(b) : undefined;
    if (
      !source ||
      !target ||
      !presentIds.has(source) ||
      !presentIds.has(target)
    ) {
      continue;
    }
    const id = edgeId(source, target, 'relationship');
    if (seen.has(id)) {
      continue;
    }
    seen.add(id);
    relEdges.push({
      id,
      source,
      target,
      kind: 'relationship',
      label: rel.name,
      cardinality: cardinalityOf(rel.joinType),
      relationshipName: rel.name,
      condition: rel.condition ?? null,
      columnRefs: parseConditionRefs(rel.condition),
    });
  }
  return { nodes: covered.nodes, edges: [...covered.edges, ...relEdges] };
}
