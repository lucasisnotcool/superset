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

export interface DatabaseSharedBadgeProps {
  /** Human-readable name of the project's database, for the popover copy. */
  databaseLabel?: string | null;
}

const Body = styled.div`
  ${({ theme }) => css`
    max-width: 320px;
    font-size: ${theme.fontSizeSM}px;
    color: ${theme.colorTextSecondary};
    line-height: 1.4;
  `}
`;

const Heading = styled.div`
  ${({ theme }) => css`
    font-weight: ${theme.fontWeightStrong};
    margin-bottom: ${theme.sizeUnit}px;
    color: ${theme.colorText};
  `}
`;

const SharedList = styled.ul`
  ${({ theme }) => css`
    margin: ${theme.sizeUnit}px 0 0;
    padding-left: ${theme.sizeUnit * 4}px;
    li {
      margin: ${theme.sizeUnit / 2}px 0;
    }
  `}
`;

/**
 * Badge stating that this MDL project's authored content is DB-tied: shared
 * with every user who can connect to the project's database. It exists because
 * the sharing model is otherwise invisible — a user authoring an instruction,
 * uploading a document, or promoting a golden query has no cue that teammates
 * (anyone with their own valid connection to the same physical database) see it
 * too. This is the canonical, always-visible signal; point-of-authoring panels
 * (instructions, documents) reinforce it inline.
 *
 * Neutral/blue, not success or warning: sharing is a stable fact of the
 * collaboration model, not a state to celebrate or a hazard to flag. The text
 * label is always shown (never color/icon alone) and the badge is keyboard
 * focusable, so the popover is reachable without a pointer (WCAG 1.4.1 / 2.1.1).
 */
export default function DatabaseSharedBadge({
  databaseLabel,
}: DatabaseSharedBadgeProps) {
  const theme = useTheme();
  const dbName = databaseLabel || t('this database');

  const content = (
    <div data-test="database-shared-popover">
      <Heading>{t('Shared with database access')}</Heading>
      <Body>
        {t(
          'Everyone who can connect to %s sees this project and shares its:',
          dbName,
        )}
        <SharedList>
          <li>{t('models, relationships, views, and cubes')}</li>
          <li>{t('uploaded documents')}</li>
          <li>{t('instructions')}</li>
          <li>{t('golden queries')}</li>
          <li>{t("the agent's learned SQL examples")}</li>
        </SharedList>
        <div
          css={css`
            margin-top: ${theme.sizeUnit * 2}px;
          `}
        >
          {t('Only your own chat conversations stay private.')}
        </div>
      </Body>
    </div>
  );

  return (
    <Popover
      content={content}
      trigger={['hover', 'focus']}
      placement="bottomLeft"
    >
      <Tag
        color="processing"
        role="button"
        tabIndex={0}
        data-test="database-shared-badge"
        aria-label={t(
          'Shared with everyone who can connect to %s. Press to see what is ' +
            'shared.',
          dbName,
        )}
        css={css`
          cursor: default;
          display: inline-flex;
          align-items: center;
          gap: ${theme.sizeUnit}px;
          margin: 0;
        `}
      >
        <Icons.UsergroupAddOutlined iconSize="s" iconColor={theme.colorInfo} />
        {t('Shared')}
      </Tag>
    </Popover>
  );
}
