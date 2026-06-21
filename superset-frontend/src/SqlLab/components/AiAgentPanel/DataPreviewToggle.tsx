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
import type { ExecutionResult } from './api';

const ResultScroller = styled.div`
  ${({ theme }) => css`
    max-height: 220px;
    overflow: auto;
    border: 1px solid ${theme.colorBorderSecondary};
    border-radius: ${theme.borderRadius}px;
  `}
`;

const ResultTable = styled.table`
  ${({ theme }) => css`
    width: 100%;
    border-collapse: collapse;
    font-size: ${theme.fontSizeSM}px;

    th,
    td {
      padding: ${theme.sizeUnit}px ${theme.sizeUnit * 2}px;
      border-bottom: 1px solid ${theme.colorBorderSecondary};
      text-align: left;
      vertical-align: top;
      white-space: nowrap;
    }

    th {
      background: ${theme.colorBgElevated};
      color: ${theme.colorTextSecondary};
      font-weight: ${theme.fontWeightStrong};
    }

    tr:last-of-type td {
      border-bottom: 0;
    }
  `}
`;

const Details = styled.details`
  ${({ theme }) => css`
    color: ${theme.colorTextSecondary};
    font-size: ${theme.fontSizeSM}px;

    summary {
      cursor: pointer;
    }
  `}
`;

export interface DataPreviewToggleProps {
  result?: ExecutionResult | null;
}

export const formatResultValue = (value: unknown) => {
  if (value === null || value === undefined) {
    return 'NULL';
  }
  if (typeof value === 'object') {
    return JSON.stringify(value);
  }
  return String(value);
};

export const getResultColumns = (result?: ExecutionResult | null) => {
  if (!result) {
    return [];
  }
  if (result.columns.length > 0) {
    return result.columns;
  }
  const firstRow = result.rows[0];
  return firstRow ? Object.keys(firstRow) : [];
};

export default function DataPreviewToggle({ result }: DataPreviewToggleProps) {
  if (!result) {
    return null;
  }
  const resultColumns = getResultColumns(result);
  const resultRows = result.rows.slice(0, 10);
  return (
    <Details open>
      <summary>{t('Data - %s rows', result.row_count)}</summary>
      {resultColumns.length > 0 && resultRows.length > 0 && (
        <ResultScroller>
          <ResultTable>
            <thead>
              <tr>
                {resultColumns.map(column => (
                  <th key={column}>{column}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {resultRows.map((row, rowIndex) => (
                <tr key={rowIndex}>
                  {resultColumns.map(column => (
                    <td key={column}>{formatResultValue(row[column])}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </ResultTable>
        </ResultScroller>
      )}
    </Details>
  );
}
