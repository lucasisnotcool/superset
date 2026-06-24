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
import { useEffect, useMemo, useRef, useState } from 'react';
import { useSelector } from 'react-redux';
import { t } from '@apache-superset/core/translation';
import { css, styled, useTheme } from '@apache-superset/core/theme';
import { Alert } from '@apache-superset/core/components';
import {
  Empty,
  Input,
  Loading,
  Radio,
  Typography,
} from '@superset-ui/core/components';
import { capGraph } from './graphData';
import {
  buildSemanticGraph,
  composeCombined,
  mergeManifests,
} from './mdlOverlay';
import {
  applyValidations,
  countUnattached,
  GraphValidationMessage,
} from './validationOverlay';
import { toEchartsOption } from './echartsOptions';
import { createGraphChart, GraphChartHandle } from './echartsRender';
import { useSchemaGraphData } from './useSchemaGraphData';
import { MAX_EDGES, MAX_NODES } from './graphConfig';
import { GraphPalette, LayerMode, SchemaGraphModel } from './types';

export interface GraphMdlFile {
  content: string;
  validation?: { messages?: GraphValidationMessage[] } | null;
}

export interface SchemaGraphProps {
  mdlFiles: GraphMdlFile[];
  databaseId?: number;
  catalogName?: string | null;
  schemaName?: string | null;
}

interface PartialSqlLabState {
  sqlLab?: {
    tables?: { dbId?: number; schema?: string; name: string }[];
  };
}

const Root = styled.div`
  ${({ theme }) => css`
    display: flex;
    flex: 1;
    min-height: 0;
    flex-direction: column;
    gap: ${theme.sizeUnit * 2}px;
    padding: ${theme.sizeUnit * 3}px;
  `}
`;

const Toolbar = styled.div`
  ${({ theme }) => css`
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: ${theme.sizeUnit * 2}px;
  `}
`;

// `position: relative` + an absolutely-filled canvas is the robust ECharts
// container pattern: it bounds the chart to the flex-sized visible area (with
// min-height:0 + overflow:hidden up the chain) instead of letting the canvas
// grow to its own content height and overflow the panel.
const CanvasWrap = styled.div`
  position: relative;
  display: flex;
  flex: 1;
  min-height: 240px;
  align-items: center;
  justify-content: center;
  overflow: hidden;
`;

const Canvas = styled.div`
  position: absolute;
  inset: 0;
`;

export default function SchemaGraph({
  mdlFiles,
  databaseId,
  catalogName = null,
  schemaName = null,
}: SchemaGraphProps) {
  const [layer, setLayer] = useState<LayerMode>('combined');
  const theme = useTheme();
  const palette: GraphPalette = useMemo(
    () => ({
      text: theme.colorText,
      textMuted: theme.colorTextSecondary,
      bg: theme.colorBgElevated,
      border: theme.colorBorder,
      accent: theme.colorPrimary,
      // White-ish label fill with a dark outline for contrast on any backdrop.
      labelText: theme.colorBgContainer,
      labelOutline: theme.colorText,
      pk: theme.colorWarning,
      fk: theme.colorPrimary,
    }),
    [theme],
  );

  const contents = mdlFiles.map(file => file.content);
  const contentKey = contents.join('\n-- --\n');
  const manifest = useMemo(
    () => mergeManifests(contents),
    // contentKey captures meaningful change; contents identity churns.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [contentKey],
  );
  const messages: GraphValidationMessage[] = mdlFiles.flatMap(
    file => file.validation?.messages ?? [],
  );

  const sqlLabTables = useSelector(
    (state: PartialSqlLabState) => state.sqlLab?.tables,
  );
  const userTables = useMemo(
    () =>
      (sqlLabTables ?? [])
        .filter(
          table => table.dbId === databaseId && table.schema === schemaName,
        )
        .map(table => table.name),
    [sqlLabTables, databaseId, schemaName],
  );

  const physical = useSchemaGraphData({
    databaseId,
    catalog: catalogName,
    schema: schemaName,
    enabled: layer !== 'mdl',
    userTables,
  });

  const semanticGraph = useMemo(
    () => applyValidations(buildSemanticGraph(manifest), messages),
    // messages is derived each render; key on a stable signature.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [manifest, JSON.stringify(messages)],
  );

  const baseModel: SchemaGraphModel = useMemo(() => {
    if (layer === 'mdl') {
      return semanticGraph;
    }
    if (layer === 'physical') {
      return physical.physicalGraph;
    }
    return composeCombined(physical.physicalGraph, manifest);
  }, [layer, semanticGraph, physical.physicalGraph, manifest]);

  const { model, droppedNodes } = useMemo(
    () => capGraph(baseModel, MAX_NODES, MAX_EDGES),
    [baseModel],
  );

  const unattached = useMemo(
    () => (layer === 'mdl' ? countUnattached(semanticGraph, messages) : 0),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [layer, semanticGraph, JSON.stringify(messages)],
  );

  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<GraphChartHandle | null>(null);
  const observerRef = useRef<ResizeObserver | null>(null);

  // Dispose once on unmount (the option-update effect reuses the instance).
  useEffect(
    () => () => {
      observerRef.current?.disconnect();
      observerRef.current = null;
      chartRef.current?.dispose();
      chartRef.current = null;
    },
    [],
  );

  useEffect(() => {
    const el = containerRef.current;
    // When the graph empties, tear the chart down so it re-inits cleanly (and
    // measures the real container size) the next time there is something to draw.
    if (!el || model.nodes.length === 0) {
      observerRef.current?.disconnect();
      observerRef.current = null;
      chartRef.current?.dispose();
      chartRef.current = null;
      return undefined;
    }
    if (!chartRef.current) {
      chartRef.current = createGraphChart(el);
      // Resize on container changes — the chart mounts inside a lazy tab/flex
      // pane whose size is often not final at init (the cause of the undersized
      // canvas + clipped nodes). ResizeObserver + a deferred resize fix both.
      if (typeof ResizeObserver !== 'undefined') {
        observerRef.current = new ResizeObserver(() =>
          chartRef.current?.resize(),
        );
        observerRef.current.observe(el);
      }
    }
    const chart = chartRef.current;
    chart.setOption(toEchartsOption(model, palette));
    const raf = requestAnimationFrame(() => chart.resize());
    return () => cancelAnimationFrame(raf);
  }, [model, palette]);

  const layerOptions = [
    { label: t('Combined'), value: 'combined' },
    { label: t('Database'), value: 'physical' },
    { label: t('Semantic'), value: 'mdl' },
  ];

  const isBusy =
    layer !== 'mdl' && (physical.isLoadingUniverse || physical.isHydrating);

  return (
    <Root data-test="schema-graph">
      <Toolbar>
        <Radio.GroupWrapper
          options={layerOptions}
          value={layer}
          onChange={event => setLayer(event.target.value as LayerMode)}
          optionType="button"
          buttonStyle="solid"
        />
        {layer !== 'mdl' && (
          <Input
            data-test="schema-graph-search"
            allowClear
            placeholder={t('Find a table…')}
            style={{ maxWidth: 240 }}
            onPressEnter={event => {
              const value = (event.target as HTMLInputElement).value.trim();
              if (value) {
                physical.loadTable(value);
              }
            }}
          />
        )}
        <Typography.Text type="secondary" data-test="schema-graph-counts">
          {t('%s nodes · %s edges', model.nodes.length, model.edges.length)}
          {droppedNodes > 0 ? t(' · %s hidden (capped)', droppedNodes) : ''}
          {layer !== 'mdl' && physical.universe.length > 0
            ? t(
                ' · loaded %s of %s tables',
                physical.loadedTables.length,
                physical.universe.length,
              )
            : ''}
        </Typography.Text>
      </Toolbar>

      {physical.error && layer !== 'mdl' && (
        <Alert
          type="warning"
          message={t('Could not load the database schema: %s', physical.error)}
        />
      )}
      {layer !== 'mdl' && physical.failedTables.length > 0 && (
        <Alert
          type="warning"
          message={t(
            'Metadata could not be loaded for %s table(s); they appear without ' +
              'columns or relationships: %s',
            physical.failedTables.length,
            physical.failedTables.join(', '),
          )}
        />
      )}
      {unattached > 0 && (
        <Alert
          type="info"
          message={t(
            '%s validation message(s) could not be tied to a specific model.',
            unattached,
          )}
        />
      )}

      <CanvasWrap>
        {model.nodes.length === 0 ? (
          (() => {
            if (isBusy) {
              return <Loading position="inline" />;
            }
            const description =
              layer === 'mdl'
                ? t(
                    'No models to visualize yet. Add or activate MDL models to see the graph.',
                  )
                : t('No tables to visualize for this schema yet.');
            return (
              <Empty
                image={Empty.PRESENTED_IMAGE_SIMPLE}
                description={description}
              />
            );
          })()
        ) : (
          <Canvas ref={containerRef} data-test="schema-graph-canvas" />
        )}
      </CanvasWrap>
    </Root>
  );
}
