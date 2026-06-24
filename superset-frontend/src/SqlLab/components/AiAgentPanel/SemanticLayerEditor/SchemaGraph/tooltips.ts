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

// Pure builders for the graph hover tooltips (wren_graph_view.md G1.4, D15).
// They produce escaped, theme-styled HTML strings attached to each ECharts
// node/link and read back by a single formatter in echartsRender.ts — so the
// content is fully unit-testable without importing ECharts.

import { GraphEdge, GraphNode, GraphPalette, PhysicalColumn } from './types';

// Columns beyond this are summarised as "+N more" so a wide table's hover stays
// readable (the full set lives in the click detail panel, G1.3).
const MAX_TOOLTIP_COLUMNS = 15;

// Fallback palette so the builders work (and unit tests run) without a theme.
const DEFAULT_PALETTE: GraphPalette = {
  text: '#1B1B1B',
  textMuted: '#879399',
  bg: '#FFFFFF',
  border: '#E0E0E0',
  accent: '#20A7C9',
  labelText: '#FFFFFF',
  labelOutline: '#1B1B1B',
  pk: '#FCC700',
  fk: '#20A7C9',
};

/** Escape the five HTML-significant characters; never returns undefined. */
export function escapeHtml(value: unknown): string {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

// Small glyph "icons" for column keys (the tooltip is an HTML string, so we use
// inline glyphs rather than React icon components).
const KEY_ICON: Record<string, string> = {
  pk: '🔑',
  fk: '🔗',
  index: '#',
};

const KEY_TITLE: Record<string, string> = {
  pk: 'Primary key',
  fk: 'Foreign key',
  index: 'Indexed',
};

function keyIcons(column: PhysicalColumn, palette: GraphPalette): string {
  return (column.keys ?? [])
    .map(key => {
      const icon = KEY_ICON[key.type];
      if (!icon) {
        return '';
      }
      const color = key.type === 'pk' ? palette.pk : palette.fk;
      return ` <span title="${KEY_TITLE[key.type]}" style="color:${color}">${icon}</span>`;
    })
    .join('');
}

/** A column row: name (+ key icons) left-aligned, type right-aligned. */
function columnRow(column: PhysicalColumn, palette: GraphPalette): string {
  const name = `${escapeHtml(column.name)}${keyIcons(column, palette)}`;
  const type = column.type
    ? `<span style="color:${palette.textMuted};font-family:monospace;font-size:11px">${escapeHtml(column.type)}</span>`
    : '';
  return (
    `<div style="display:flex;justify-content:space-between;align-items:baseline;gap:16px">` +
    `<span style="color:${palette.text}">${name}</span>${type}</div>`
  );
}

function divider(palette: GraphPalette): string {
  return `<div style="border-top:1px solid ${palette.border};margin:4px 0"></div>`;
}

function header(
  title: string,
  subtitle: string,
  palette: GraphPalette,
): string {
  const sub = subtitle
    ? ` <span style="color:${palette.textMuted};font-weight:400">· ${subtitle}</span>`
    : '';
  return `<div style="font-weight:600;color:${palette.text}">${title}${sub}</div>`;
}

/**
 * Hover content for a node: a header (name · kind · coverage), then the column
 * list with names left-aligned and types right-aligned. A node whose metadata
 * failed to load shows that note instead of an empty column list.
 */
export function nodeTooltipHtml(
  node: GraphNode,
  palette: GraphPalette = DEFAULT_PALETTE,
): string {
  const kindLabel =
    node.kind === 'model' ? 'Model' : node.kind === 'view' ? 'View' : 'Table';
  const subtitleParts = [kindLabel];
  if (node.kind !== 'model' && node.modeled !== undefined) {
    subtitleParts.push(node.modeled ? 'Modeled' : 'Not modeled');
  }
  const body: string[] = [];

  const warnings = (node.decorations?.validation ?? []).filter(
    message => message.severity === 'warning' || message.severity === 'error',
  );
  warnings.forEach(message =>
    body.push(
      `<div style="color:${palette.textMuted}">${escapeHtml(message.message)}</div>`,
    ),
  );

  const summary: string[] = [];
  if (node.metricCount) {
    summary.push(`${node.metricCount} metric(s)`);
  }
  if (node.hasCalculatedFields) {
    summary.push('has calculated fields');
  }
  if (summary.length) {
    body.push(
      `<div style="color:${palette.textMuted};font-size:11px">${escapeHtml(summary.join(' · '))}</div>`,
    );
  }

  const columns = node.columns ?? [];
  if (columns.length) {
    body.push(
      `<div style="color:${palette.textMuted};font-size:11px;margin-bottom:2px">Columns (${columns.length})</div>`,
    );
    columns
      .slice(0, MAX_TOOLTIP_COLUMNS)
      .forEach(column => body.push(columnRow(column, palette)));
    if (columns.length > MAX_TOOLTIP_COLUMNS) {
      body.push(
        `<div style="color:${palette.textMuted}">+${columns.length - MAX_TOOLTIP_COLUMNS} more…</div>`,
      );
    }
  } else if (!warnings.length) {
    body.push(
      `<div style="color:${palette.textMuted};font-style:italic">No column metadata loaded.</div>`,
    );
  }

  return (
    `<div style="min-width:180px">` +
    header(escapeHtml(node.label), subtitleParts.join(' · '), palette) +
    divider(palette) +
    body.join('') +
    `</div>`
  );
}

/**
 * Hover content for an edge: what kind of join it is, which columns it pairs
 * (left → right), and (for MDL relationships) the relationship name,
 * cardinality, and the raw `condition` it was derived from.
 */
export function edgeTooltipHtml(
  edge: GraphEdge,
  palette: GraphPalette = DEFAULT_PALETTE,
): string {
  const isRel = edge.kind === 'relationship';
  const title = isRel
    ? escapeHtml(edge.relationshipName ?? edge.label ?? 'Relationship')
    : 'Foreign key';
  const subtitle = isRel
    ? ['MDL relationship', edge.cardinality ?? ''].filter(Boolean).join(' · ')
    : 'Physical';

  const body: string[] = [];
  for (const ref of edge.columnRefs ?? []) {
    body.push(
      `<div style="color:${palette.text}">${escapeHtml(ref.from)} <span style="color:${palette.textMuted}">→</span> ${escapeHtml(ref.to)}</div>`,
    );
  }
  if (isRel && edge.condition) {
    body.push(
      `<div style="color:${palette.textMuted};font-family:monospace;font-size:11px">${escapeHtml(edge.condition)}</div>`,
    );
  }

  return (
    `<div style="min-width:140px">` +
    header(title, subtitle, palette) +
    (body.length ? divider(palette) + body.join('') : '') +
    `</div>`
  );
}
