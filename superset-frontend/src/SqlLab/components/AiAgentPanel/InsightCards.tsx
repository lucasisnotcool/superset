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
import type { InsightCard } from './api';

const Grid = styled.div`
  ${({ theme }) => css`
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(132px, 1fr));
    gap: ${theme.sizeUnit * 2}px;
  `}
`;

const InsightItem = styled.div<{ 'data-severity': InsightCard['severity'] }>`
  ${({ theme, 'data-severity': severity }) => css`
    min-width: 0;
    padding: ${theme.sizeUnit * 2}px;
    border-left: 3px solid
      ${severity === 'success'
        ? theme.colorSuccess
        : severity === 'warning'
          ? theme.colorWarning
          : theme.colorPrimary};
    background: ${theme.colorBgElevated};
  `}
`;

const Value = styled(Typography.Text)`
  ${({ theme }) => css`
    display: block;
    font-size: ${theme.fontSizeLG}px;
    font-weight: ${theme.fontWeightStrong};
    overflow-wrap: anywhere;
  `}
`;

const Meta = styled(Typography.Text)`
  ${({ theme }) => css`
    display: block;
    color: ${theme.colorTextSecondary};
    font-size: ${theme.fontSizeSM}px;
    overflow-wrap: anywhere;
  `}
`;

export interface InsightCardsProps {
  cards?: InsightCard[];
}

export default function InsightCards({ cards = [] }: InsightCardsProps) {
  if (cards.length === 0) {
    return null;
  }
  return (
    <Grid aria-label={t('Insights')}>
      {cards.slice(0, 3).map(card => (
        <InsightItem key={card.title} data-severity={card.severity}>
          <Meta>{card.title}</Meta>
          {card.value !== undefined && card.value !== null && (
            <Value>{String(card.value)}</Value>
          )}
          {card.description && <Meta>{card.description}</Meta>}
        </InsightItem>
      ))}
    </Grid>
  );
}
