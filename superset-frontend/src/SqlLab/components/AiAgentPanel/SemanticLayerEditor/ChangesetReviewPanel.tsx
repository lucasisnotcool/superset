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
import { ReactNode, useMemo, useState } from 'react';
import ReactDiffViewer from 'react-diff-viewer-continued';
import { t } from '@apache-superset/core/translation';
import { css, isThemeDark, useTheme } from '@apache-superset/core/theme';
import { Button, Flex, Tag, Typography } from '@superset-ui/core/components';
import { Changeset, ChangesetItem } from '../api';

type Decision = 'accepted' | 'rejected';

const opLabel = (op: ChangesetItem['op']) => {
  if (op === 'create') return t('Create');
  if (op === 'delete') return t('Delete');
  return t('Update');
};

// Default decision is op-agnostic: pre-accept unless the item fails validation.
// Removals (deletes / element-stripping updates) are first-class proposals — they
// are NOT singled out for pre-rejection, only made conspicuous (red op tag / diff).
const defaultDecisions = (changeset: Changeset): Record<string, Decision> =>
  Object.fromEntries(
    changeset.items.map(item => [
      item.path,
      item.validation?.valid === false ? 'rejected' : 'accepted',
    ]),
  );

export interface ChangesetReviewPanelProps {
  changeset: Changeset;
  /** False renders a read-only history view (no accept/reject/apply). */
  actionable?: boolean;
  canWrite?: boolean;
  isApplying?: boolean;
  /** Called with the accepted items when the user applies. */
  onApply?: (acceptedItems: ChangesetItem[]) => void;
  /** Optional extra content per item (e.g. the coverage claim a fix closes). */
  renderItemExtra?: (item: ChangesetItem) => ReactNode;
}

/**
 * Per-item changeset review: a diff per item with accept/reject toggles and an
 * apply button. Self-contained (owns its decision state) so it is reused by both
 * the Copilot panel and the coverage recovery dialog. Removals are reviewed like
 * any other change; only invalid items pre-reject.
 */
const ChangesetReviewPanel = ({
  changeset,
  actionable = true,
  canWrite = true,
  isApplying = false,
  onApply,
  renderItemExtra,
}: ChangesetReviewPanelProps) => {
  const theme = useTheme();
  const [decisions, setDecisions] = useState<Record<string, Decision>>(() =>
    defaultDecisions(changeset),
  );

  const diffStyles = useMemo(() => {
    const variables = {
      diffViewerBackground: theme.colorBgContainer,
      diffViewerColor: theme.colorText,
      addedBackground: theme.colorSuccessBg,
      addedColor: theme.colorText,
      removedBackground: theme.colorErrorBg,
      removedColor: theme.colorText,
      gutterBackground: theme.colorBgLayout,
      gutterColor: theme.colorTextTertiary,
      emptyLineBackground: theme.colorBgContainer,
    };
    return {
      variables: { dark: variables, light: variables },
      diffContainer: {
        borderRadius: `${theme.borderRadius}px`,
        border: `1px solid ${theme.colorBorder}`,
      },
    };
  }, [theme]);

  const acceptedItems = useMemo(
    () => changeset.items.filter(item => decisions[item.path] === 'accepted'),
    [changeset, decisions],
  );

  if (!changeset.items.length) return null;

  return (
    <Flex vertical gap={theme.sizeUnit * 2} data-test="changeset-review">
      <Flex justify="space-between" align="center">
        <Typography.Text strong>
          {actionable
            ? t('%s proposed change(s)', changeset.items.length)
            : t('%s proposed change(s) (history)', changeset.items.length)}
        </Typography.Text>
        {actionable && onApply ? (
          <Button
            buttonStyle="primary"
            buttonSize="small"
            disabled={!canWrite || isApplying || acceptedItems.length === 0}
            loading={isApplying}
            onClick={() => onApply(acceptedItems)}
            data-test="changeset-apply"
          >
            {t('Apply %s accepted', acceptedItems.length)}
          </Button>
        ) : null}
      </Flex>
      {changeset.items.map(item => {
        const decision = decisions[item.path];
        const invalid = item.validation?.valid === false;
        return (
          <Flex
            vertical
            key={item.path}
            gap={theme.sizeUnit}
            css={css`
              border: 1px solid ${theme.colorBorderSecondary};
              border-radius: ${theme.borderRadius}px;
              padding: ${theme.sizeUnit * 2}px;
              opacity: ${actionable && decision === 'rejected' ? 0.55 : 1};
            `}
            data-test="changeset-review-item"
          >
            <Flex justify="space-between" align="center" wrap="wrap">
              <Flex align="center" gap={theme.sizeUnit}>
                <Tag color={item.op === 'delete' ? 'error' : 'processing'}>
                  {opLabel(item.op)}
                </Tag>
                <Typography.Text code>{item.path}</Typography.Text>
                {invalid ? <Tag color="error">{t('invalid')}</Tag> : null}
              </Flex>
              {actionable ? (
                <Flex gap={theme.sizeUnit}>
                  <Button
                    buttonSize="small"
                    buttonStyle={
                      decision === 'accepted' ? 'primary' : 'secondary'
                    }
                    onClick={() =>
                      setDecisions(prev => ({
                        ...prev,
                        [item.path]: 'accepted',
                      }))
                    }
                    data-test="changeset-accept"
                  >
                    {t('Accept')}
                  </Button>
                  <Button
                    buttonSize="small"
                    buttonStyle={
                      decision === 'rejected' ? 'danger' : 'secondary'
                    }
                    onClick={() =>
                      setDecisions(prev => ({
                        ...prev,
                        [item.path]: 'rejected',
                      }))
                    }
                    data-test="changeset-reject"
                  >
                    {t('Reject')}
                  </Button>
                </Flex>
              ) : null}
            </Flex>
            {item.summary ? (
              <Typography.Text type="secondary">{item.summary}</Typography.Text>
            ) : null}
            {renderItemExtra ? renderItemExtra(item) : null}
            {item.op !== 'delete' ? (
              <ReactDiffViewer
                oldValue={item.current_content || ''}
                newValue={item.proposed_content || ''}
                splitView={false}
                useDarkTheme={isThemeDark(theme)}
                styles={diffStyles}
              />
            ) : (
              <Typography.Text type="danger">
                {t('This file will be deleted.')}
              </Typography.Text>
            )}
          </Flex>
        );
      })}
    </Flex>
  );
};

export default ChangesetReviewPanel;
