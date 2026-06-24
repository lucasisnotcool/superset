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

// Thin ECharts boundary (wren_graph_view.md D1): the *only* module that imports
// ECharts, using the minimal `echarts/core` + GraphChart surface so the bundle
// cost stays in the lazy SchemaGraph chunk. Isolating it here keeps every other
// SchemaGraph module unit-testable (tests mock this file).

import * as echarts from 'echarts/core';
import { GraphChart } from 'echarts/charts';
import { LegendComponent, TooltipComponent } from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';
import type { EChartsCoreOption } from 'echarts/core';
import type { GraphChartOption } from './echartsOptions';

echarts.use([GraphChart, LegendComponent, TooltipComponent, CanvasRenderer]);

export interface GraphChartHandle {
  setOption: (option: GraphChartOption) => void;
  resize: () => void;
  dispose: () => void;
}

// The one formatter function that touches ECharts (D15): every node/link carries
// a prebuilt HTML `tooltip` string (built by the pure tooltips.ts), so this stays
// a trivial read with a safe fallback to the node/edge name.
function tooltipFormatter(params: {
  data?: { tooltip?: string };
  name?: string;
}) {
  return params?.data?.tooltip ?? params?.name ?? '';
}

/** Initialise an ECharts graph chart bound to `el`. */
export function createGraphChart(el: HTMLDivElement): GraphChartHandle {
  const chart = echarts.init(el);
  return {
    // Our structural GraphChartOption is a valid ECharts graph option; the cast
    // is localized to this one ECharts-touching module. The tooltip formatter is
    // injected here (a function can't live in the pure, testable option object).
    setOption: option =>
      chart.setOption({
        ...option,
        tooltip: { ...option.tooltip, formatter: tooltipFormatter },
      } as unknown as EChartsCoreOption),
    resize: () => chart.resize(),
    dispose: () => chart.dispose(),
  };
}
