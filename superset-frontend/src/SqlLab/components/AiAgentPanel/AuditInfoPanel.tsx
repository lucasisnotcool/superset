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
import type { AuditInfo } from './api';

const Details = styled.details`
  ${({ theme }) => css`
    color: ${theme.colorTextSecondary};
    font-size: ${theme.fontSizeSM}px;

    summary {
      cursor: pointer;
    }
  `}
`;

const List = styled.dl`
  ${({ theme }) => css`
    display: grid;
    grid-template-columns: max-content 1fr;
    gap: ${theme.sizeUnit}px ${theme.sizeUnit * 2}px;
    margin: ${theme.sizeUnit}px 0 0;

    dt {
      color: ${theme.colorTextSecondary};
    }

    dd {
      margin: 0;
      overflow-wrap: anywhere;
    }
  `}
`;

export interface AuditInfoPanelProps {
  audit?: AuditInfo | null;
}

export default function AuditInfoPanel({ audit }: AuditInfoPanelProps) {
  if (!audit) {
    return null;
  }
  const entries = Object.entries(audit).filter(
    ([, value]) => value !== null && value !== undefined && value !== '',
  );
  if (entries.length === 0) {
    return null;
  }
  return (
    <Details>
      <summary>{t('Audit')}</summary>
      <List>
        {entries.map(([key, value]) => (
          <div key={key}>
            <dt>{key}</dt>
            <dd>{String(value)}</dd>
          </div>
        ))}
      </List>
    </Details>
  );
}
