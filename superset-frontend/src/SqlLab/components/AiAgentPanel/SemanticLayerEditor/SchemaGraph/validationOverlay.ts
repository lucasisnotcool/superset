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

// X3 validation overlay (wren_graph_view.md §7.2 / G2.2). Best-effort: the
// backend `MdlValidationMessage` carries `{severity, message, code}` but no
// structured entity ref (the model/column name lives in the message text), so we
// attach a message to a node when the message mentions that node's name as a
// whole word. Falls back to leaving messages unattached (counted by the caller).

import { GraphNode, SchemaGraphModel } from './types';

export interface GraphValidationMessage {
  severity: 'error' | 'warning' | 'info';
  message: string;
  code?: string | null;
}

const escapeRegExp = (value: string): string =>
  value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

/** Whole-word (identifier-boundary) mention of `name` in `text`. */
export function mentionsEntity(text: string, name: string): boolean {
  if (!name) {
    return false;
  }
  const re = new RegExp(
    `(^|[^A-Za-z0-9_])${escapeRegExp(name)}($|[^A-Za-z0-9_])`,
  );
  return re.test(text);
}

/**
 * Attach validation messages to the nodes they mention (by label). Returns a new
 * model; nodes without a match are unchanged. Messages may match several nodes
 * (e.g. a relationship error naming two models) — attached to each.
 */
export function applyValidations(
  model: SchemaGraphModel,
  messages: GraphValidationMessage[],
): SchemaGraphModel {
  if (messages.length === 0) {
    return model;
  }
  const decorate = (node: GraphNode): GraphNode => {
    const matched = messages.filter(m => mentionsEntity(m.message, node.label));
    if (matched.length === 0) {
      return node;
    }
    return {
      ...node,
      decorations: {
        ...node.decorations,
        validation: [
          ...(node.decorations?.validation ?? []),
          ...matched.map(m => ({ severity: m.severity, message: m.message })),
        ],
      },
    };
  };
  return { nodes: model.nodes.map(decorate), edges: model.edges };
}

/**
 * Count messages that did not mention any node label — surfaced as a
 * project-level validation banner so nothing is silently lost.
 */
export function countUnattached(
  model: SchemaGraphModel,
  messages: GraphValidationMessage[],
): number {
  const labels = model.nodes.map(n => n.label);
  return messages.filter(
    m => !labels.some(label => mentionsEntity(m.message, label)),
  ).length;
}
