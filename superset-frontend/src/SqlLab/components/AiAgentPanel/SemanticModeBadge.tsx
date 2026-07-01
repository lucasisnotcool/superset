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
import { css, styled, useTheme } from '@apache-superset/core/theme';
import { Popover, Tag } from '@superset-ui/core/components';
import { Icons } from '@superset-ui/core/components/Icons';
import type { SemanticFactorState, SemanticModeStatus } from './api';

export interface SemanticModeBadgeProps {
  status?: SemanticModeStatus | null;
}

// The factor list inside the popover. Read-only; the essential mode word lives in
// the always-visible Tag label, so screen-reader/keyboard users get the state
// without the hover surface (WCAG 1.4.1 — never color/hover alone).
const FactorList = styled.ul`
  ${({ theme }) => css`
    list-style: none;
    margin: 0;
    padding: 0;
    max-width: 320px;
    li {
      display: flex;
      align-items: flex-start;
      gap: ${theme.sizeUnit * 2}px;
      padding: ${theme.sizeUnit}px 0;
    }
  `}
`;

const FactorText = styled.span`
  ${({ theme }) => css`
    display: flex;
    flex-direction: column;
    font-size: ${theme.fontSizeSM}px;
    line-height: 1.3;
    .label {
      color: ${theme.colorText};
    }
    .detail {
      color: ${theme.colorTextSecondary};
    }
  `}
`;

const Heading = styled.div`
  ${({ theme }) => css`
    font-weight: ${theme.fontWeightStrong};
    margin-bottom: ${theme.sizeUnit}px;
    color: ${theme.colorText};
  `}
`;

const Subhead = styled.div`
  ${({ theme }) => css`
    font-size: ${theme.fontSizeSM}px;
    color: ${theme.colorTextSecondary};
    margin-bottom: ${theme.sizeUnit * 2}px;
    max-width: 320px;
  `}
`;

function FactorIcon({ state }: { state: SemanticFactorState }) {
  const theme = useTheme();
  if (state === 'met') {
    return (
      <Icons.CheckCircleOutlined
        iconSize="s"
        iconColor={theme.colorSuccess}
        aria-label={t('met')}
      />
    );
  }
  if (state === 'blocked') {
    return (
      <Icons.WarningOutlined
        iconSize="s"
        iconColor={theme.colorWarning}
        aria-label={t('blocking')}
      />
    );
  }
  if (state === 'runtime') {
    return (
      <Icons.InfoCircleOutlined
        iconSize="s"
        iconColor={theme.colorTextSecondary}
        aria-label={t('checked at query time')}
      />
    );
  }
  return (
    <Icons.MinusCircleOutlined
      iconSize="s"
      iconColor={theme.colorTextSecondary}
      aria-label={t('not applicable')}
    />
  );
}

/**
 * Status badge for whether the AI SQL agent will apply semantic rewrite in the
 * current scope. Green "Semantic" when every precondition is met; neutral grey
 * "Native" otherwise (native SQL is a valid fallback, not an error — so it is not
 * red). An amber warning rides on the badge only when a blocker the user can clear
 * here (pick a schema / activate a project) exists. Hover/focus reveals the full
 * factor checklist with a warning beside each blocking factor.
 */
export default function SemanticModeBadge({ status }: SemanticModeBadgeProps) {
  const theme = useTheme();
  if (!status) {
    return null;
  }
  const isSemantic = status.mode === 'semantic';

  const content = (
    <div data-test="semantic-mode-popover">
      <Heading>{isSemantic ? t('Semantic mode') : t('Native mode')}</Heading>
      <Subhead>
        {isSemantic
          ? t(
              'The agent writes against the semantic layer — relationships and ' +
                'calculated columns are used.',
            )
          : t(
              'The agent writes native SQL. Semantic-layer relationships and ' +
                'calculated columns are not used.',
            )}
      </Subhead>
      <FactorList>
        {status.factors.map(factor => (
          <li key={factor.key}>
            <FactorIcon state={factor.state} />
            <FactorText>
              <span className="label">{factor.label}</span>
              <span className="detail">{factor.detail}</span>
            </FactorText>
          </li>
        ))}
      </FactorList>
    </div>
  );

  const ariaSummary = isSemantic
    ? t('Semantic mode active')
    : t(
        'Native mode — %s factor(s) blocking semantic mode',
        String(status.blocking_factors.length),
      );

  return (
    <Popover
      content={content}
      trigger={['hover', 'focus']}
      placement="bottomLeft"
    >
      <Tag
        color={isSemantic ? 'success' : 'default'}
        role="button"
        tabIndex={0}
        aria-label={ariaSummary}
        data-test="semantic-mode-badge"
        data-mode={status.mode}
        css={css`
          cursor: default;
          display: inline-flex;
          align-items: center;
          gap: ${theme.sizeUnit}px;
          margin: 0;
        `}
      >
        {isSemantic ? (
          <Icons.CheckCircleOutlined
            iconSize="s"
            iconColor={theme.colorSuccess}
          />
        ) : (
          status.user_fixable_blocker && (
            <Icons.WarningOutlined
              iconSize="s"
              iconColor={theme.colorWarning}
              data-test="semantic-mode-warning"
            />
          )
        )}
        {isSemantic ? t('Semantic') : t('Native')}
      </Tag>
    </Popover>
  );
}
