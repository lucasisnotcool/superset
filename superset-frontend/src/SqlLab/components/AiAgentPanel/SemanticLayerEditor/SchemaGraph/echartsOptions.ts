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

// Pure transform: SchemaGraphModel -> ECharts `graph` series option. Kept free
// of ECharts imports (returns a plain option object) so it is unit-testable and
// the bundle cost stays in the lazy SchemaGraph chunk.

import { GraphEdge, GraphNode, GraphPalette, SchemaGraphModel } from './types';
import { edgeTooltipHtml, nodeTooltipHtml } from './tooltips';

export interface GraphSeriesNode {
  id: string;
  name: string;
  category: number;
  symbolSize: number;
  itemStyle?: { color?: string; borderColor?: string; borderWidth?: number };
  // Prebuilt HTML read by the single tooltip formatter in echartsRender (D15).
  tooltip: string;
}

export interface GraphSeriesLink {
  source: string;
  target: string;
  lineStyle: { type: 'solid' | 'dashed'; color?: string };
  symbol?: [string, string];
  // Prebuilt HTML read by the single tooltip formatter in echartsRender (D15).
  tooltip: string;
}

export interface GraphChartOption {
  tooltip: {
    trigger: 'item';
    backgroundColor?: string;
    borderColor?: string;
    textStyle?: { color?: string; fontSize?: number };
    extraCssText?: string;
  };
  legend: { data: string[] }[];
  series: {
    type: 'graph';
    layout: 'force';
    roam: boolean;
    draggable: boolean;
    label: {
      show: boolean;
      position: 'right';
      formatter: string;
      overflow: 'truncate';
      width: number;
      color?: string;
      fontWeight?: number;
      textBorderColor?: string;
      textBorderWidth?: number;
    };
    categories: { name: string }[];
    data: GraphSeriesNode[];
    links: GraphSeriesLink[];
    force: { repulsion: number; edgeLength: number; gravity: number };
    emphasis: { focus: 'adjacency' };
  }[];
}

// Category index by node presentation class (drives legend + base color).
const CATEGORIES = ['Model', 'Table', 'View', 'Unmodeled'] as const;

const COLORS: Record<string, string> = {
  Model: '#1FA8C9',
  Table: '#7F8C9A',
  View: '#9B7FB5',
  Unmodeled: '#C9C9C9',
};

const ERROR_BORDER = '#E04355';
const WARNING_BORDER = '#FCC700';
const MATCHED_BORDER = '#2ECC71';

function categoryFor(node: GraphNode): (typeof CATEGORIES)[number] {
  if (node.kind === 'model') {
    return 'Model';
  }
  if (node.kind === 'view') {
    return 'View';
  }
  // combined-view physical table: dim when not modeled
  if (node.modeled === false) {
    return 'Unmodeled';
  }
  return 'Table';
}

function toSeriesNode(
  node: GraphNode,
  palette?: GraphPalette,
): GraphSeriesNode {
  const category = categoryFor(node);
  const validation = node.decorations?.validation ?? [];
  const hasError = validation.some(v => v.severity === 'error');
  const hasWarning = validation.some(v => v.severity === 'warning');
  const matched = node.decorations?.agentUsage === 'matched';
  const itemStyle: GraphSeriesNode['itemStyle'] = { color: COLORS[category] };
  if (hasError) {
    itemStyle.borderColor = ERROR_BORDER;
    itemStyle.borderWidth = 3;
  } else if (hasWarning) {
    itemStyle.borderColor = WARNING_BORDER;
    itemStyle.borderWidth = 3;
  } else if (matched) {
    itemStyle.borderColor = MATCHED_BORDER;
    itemStyle.borderWidth = 3;
  }
  return {
    id: node.id,
    name: node.label,
    category: CATEGORIES.indexOf(category),
    // Slightly larger nodes for richer entities (more columns) — bounded.
    symbolSize: Math.min(40, 18 + (node.columnCount ?? 0)),
    itemStyle,
    tooltip: nodeTooltipHtml(node, palette),
  };
}

function toSeriesLink(
  edge: GraphEdge,
  palette?: GraphPalette,
): GraphSeriesLink {
  const isRelationship = edge.kind === 'relationship';
  return {
    source: edge.source,
    target: edge.target,
    lineStyle: {
      type: isRelationship ? 'dashed' : 'solid',
      color: isRelationship ? '#1FA8C9' : '#B0B0B0',
    },
    // Arrowhead on the target; relationship edges read as directed joins.
    symbol: ['none', 'arrow'],
    tooltip: edgeTooltipHtml(edge, palette),
  };
}

/**
 * Build the ECharts `graph` series option from the domain model. `palette`
 * (from the Superset theme) styles labels and tooltips to match the app; it is
 * optional so the pure transform stays unit-testable with sensible defaults.
 */
export function toEchartsOption(
  model: SchemaGraphModel,
  palette?: GraphPalette,
): GraphChartOption {
  return {
    tooltip: {
      trigger: 'item',
      backgroundColor: palette?.bg,
      borderColor: palette?.border,
      textStyle: palette ? { color: palette.text, fontSize: 12 } : undefined,
    },
    legend: [{ data: [...CATEGORIES] }],
    series: [
      {
        type: 'graph',
        layout: 'force',
        roam: true,
        draggable: true,
        // Node names are always visible (the owner's minimum bar); long names
        // truncate rather than overflow the canvas. Light fill + dark outline
        // keeps labels legible over nodes/edges and the canvas alike.
        label: {
          show: true,
          position: 'right',
          formatter: '{b}',
          overflow: 'truncate',
          width: 120,
          color: palette?.labelText ?? '#FFFFFF',
          fontWeight: 600,
          textBorderColor: palette?.labelOutline ?? '#1B1B1B',
          textBorderWidth: 3,
        },
        categories: CATEGORIES.map(name => ({ name })),
        data: model.nodes.map(node => toSeriesNode(node, palette)),
        links: model.edges.map(edge => toSeriesLink(edge, palette)),
        // Higher gravity keeps small graphs clustered near centre so they fit the
        // viewport without panning; roam lets the user explore larger ones.
        force: { repulsion: 180, edgeLength: 100, gravity: 0.15 },
        emphasis: { focus: 'adjacency' },
      },
    ],
  };
}
