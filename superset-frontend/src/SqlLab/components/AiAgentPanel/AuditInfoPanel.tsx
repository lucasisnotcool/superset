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
import type { AuditInfo, WrenContextArtifact } from './api';

const Details = styled.details`
  ${({ theme }) => css`
    color: ${theme.colorTextSecondary};
    font-size: ${theme.fontSizeSM}px;

    summary {
      cursor: pointer;
    }
  `}
`;

const Badges = styled.div`
  ${({ theme }) => css`
    display: flex;
    flex-wrap: wrap;
    gap: ${theme.sizeUnit}px;
    margin-bottom: ${theme.sizeUnit}px;
  `}
`;

const Badge = styled.span`
  ${({ theme }) => css`
    display: inline-flex;
    align-items: center;
    height: 22px;
    padding: 0 ${theme.sizeUnit * 2}px;
    border: 1px solid ${theme.colorBorder};
    border-radius: ${theme.borderRadius}px;
    color: ${theme.colorTextSecondary};
    background: ${theme.colorBgContainer};
    font-size: ${theme.fontSizeSM}px;
    white-space: nowrap;
  `}
`;

// Friendly labels for the audit keys parity work surfaces; unknown keys fall
// back to their raw name so new backend fields still render.
const FIELD_LABELS: Record<string, string> = {
  engine: t('Engine'),
  semantic_sql: t('Semantic SQL'),
  native_sql: t('Native SQL'),
  executed_sql: t('Executed SQL'),
  row_limit: t('Row limit'),
  adapter: t('Adapter'),
};

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
  wrenContext?: WrenContextArtifact | null;
}

export default function AuditInfoPanel({
  audit,
  wrenContext,
}: AuditInfoPanelProps) {
  if (!audit) {
    return null;
  }
  const entries = Object.entries(audit).filter(
    ([, value]) => value !== null && value !== undefined && value !== '',
  );
  // At-a-glance provenance: which engine rewrote the SQL and which retriever
  // produced the context (wren_full.md RV2/RV3 surfacing).
  const engine = audit.engine;
  const retrievalMode = wrenContext?.retrieval_mode;
  const retrievedCount = wrenContext?.retrieved_item_count ?? 0;
  const recalledCount = wrenContext?.recalled_example_count ?? 0;
  // View provenance at a glance: how many vetted, named views grounded the answer.
  const viewCount = wrenContext?.matched_views?.length ?? 0;
  const hasBadges = Boolean(
    engine || retrievalMode || recalledCount > 0 || viewCount > 0,
  );
  if (entries.length === 0 && !hasBadges) {
    return null;
  }
  return (
    <Details>
      <summary>{t('Audit')}</summary>
      {hasBadges ? (
        <Badges>
          {engine ? <Badge>{t('Engine: %s', engine)}</Badge> : null}
          {retrievalMode ? (
            <Badge>
              {retrievedCount > 0
                ? t('Retrieval: %s (%s chunks)', retrievalMode, retrievedCount)
                : t('Retrieval: %s', retrievalMode)}
            </Badge>
          ) : null}
          {recalledCount > 0 ? (
            <Badge>{t('Reused %s learned example(s)', recalledCount)}</Badge>
          ) : null}
          {viewCount > 0 ? (
            <Badge>{t('Surfaced %s view(s)', viewCount)}</Badge>
          ) : null}
        </Badges>
      ) : null}
      <List>
        {entries.map(([key, value]) => (
          <div key={key}>
            <dt>{FIELD_LABELS[key] ?? key}</dt>
            <dd>{String(value)}</dd>
          </div>
        ))}
      </List>
    </Details>
  );
}
