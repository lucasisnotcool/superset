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
import { t } from '@apache-superset/core/translation';
import { css, styled } from '@apache-superset/core/theme';
import { Typography } from '@superset-ui/core/components';
import type { ChartSpec, ExecutionResult } from './api';

const ChartShell = styled.div`
  ${({ theme }) => css`
    display: flex;
    flex-direction: column;
    gap: ${theme.sizeUnit}px;
    min-height: 152px;
    padding: ${theme.sizeUnit * 2}px 0;
  `}
`;

const BarRow = styled.div`
  ${({ theme }) => css`
    display: grid;
    grid-template-columns: minmax(72px, 34%) 1fr auto;
    align-items: center;
    gap: ${theme.sizeUnit * 2}px;
    min-height: 24px;
  `}
`;

const BarTrack = styled.div`
  ${({ theme }) => css`
    height: 14px;
    background: ${theme.colorBgElevated};
    border: 1px solid ${theme.colorBorderSecondary};
  `}
`;

const BarFill = styled.div<{ width: number }>`
  ${({ theme, width }) => css`
    width: ${width}%;
    height: 100%;
    background: ${theme.colorPrimary};
  `}
`;

const AxisLabel = styled(Typography.Text)`
  ${({ theme }) => css`
    color: ${theme.colorTextSecondary};
    font-size: ${theme.fontSizeSM}px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  `}
`;

const LineSvg = styled.svg`
  ${({ theme }) => css`
    width: 100%;
    height: 152px;
    border: 1px solid ${theme.colorBorderSecondary};
    background: ${theme.colorBgElevated};
  `}
`;

export interface AiChartPreviewProps {
  chartSpec?: ChartSpec | null;
  result?: ExecutionResult | null;
}

export default function AiChartPreview({
  chartSpec,
  result,
}: AiChartPreviewProps) {
  if (!chartSpec || !result || chartSpec.type === 'table') {
    return null;
  }
  const y = Array.isArray(chartSpec.encoding.y)
    ? chartSpec.encoding.y[0]
    : chartSpec.encoding.y;
  if (!chartSpec.encoding.x || !y) {
    return null;
  }
  const rows = result.rows.slice(0, 12);
  const values = rows.map(row => Number(row[y] ?? 0)).filter(Number.isFinite);
  const maxValue = Math.max(...values, 0);
  if (rows.length === 0 || maxValue <= 0) {
    return null;
  }

  if (chartSpec.type === 'line') {
    const points = values
      .map((value, index) => {
        const x =
          values.length === 1 ? 50 : (index / (values.length - 1)) * 100;
        const chartY = 92 - (value / maxValue) * 76;
        return `${x},${chartY}`;
      })
      .join(' ');
    return (
      <ChartShell aria-label={chartSpec.title || t('Chart preview')}>
        <Typography.Text strong>
          {chartSpec.title || t('Chart')}
        </Typography.Text>
        <LineSvg viewBox="0 0 100 100" preserveAspectRatio="none">
          <polyline
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            points={points}
          />
        </LineSvg>
      </ChartShell>
    );
  }

  return (
    <ChartShell aria-label={chartSpec.title || t('Chart preview')}>
      <Typography.Text strong>{chartSpec.title || t('Chart')}</Typography.Text>
      {rows.map((row, index) => {
        const value = Number(row[y] ?? 0);
        const label = String(row[chartSpec.encoding.x || ''] ?? t('Unknown'));
        return (
          <BarRow key={`${label}-${index}`}>
            <AxisLabel title={label}>{label}</AxisLabel>
            <BarTrack>
              <BarFill width={(value / maxValue) * 100} />
            </BarTrack>
            <AxisLabel>{value.toLocaleString()}</AxisLabel>
          </BarRow>
        );
      })}
    </ChartShell>
  );
}
