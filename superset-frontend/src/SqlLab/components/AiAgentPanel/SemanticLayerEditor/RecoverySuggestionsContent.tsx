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
import { useCallback, useEffect, useState } from 'react';
import { t } from '@apache-superset/core/translation';
import { useTheme } from '@apache-superset/core/theme';
import { Alert } from '@apache-superset/core/components';
import { Empty, Flex, Typography } from '@superset-ui/core/components';
import { Icons } from '@superset-ui/core/components/Icons';
import {
  applyCopilotChangeset,
  ChangesetItem,
  CoverageRecoveryInfo,
  dismissCoverageRecovery,
  getCoverageRecovery,
} from '../api';
import ChangesetReviewPanel from './ChangesetReviewPanel';

export interface RecoverySuggestionsContentProps {
  projectId: string;
  /** Coverage run whose recovery suggestions to review. */
  runId?: string | null;
  /** Load + render only while active (the panel/pane is visible). */
  active: boolean;
  canWrite?: boolean;
  /** Called after suggestions are applied (drafts created) so callers refresh. */
  onApplied?: () => void;
}

/**
 * The recovery agent's gap-closing suggestions as a reviewable diff changeset
 * (per-item approve/reject); accepted items apply as drafts. This is the modal-
 * free body so it can render either inside the Coverage report dialog (as a
 * second pane) or inside the standalone banner dialog — one implementation, no
 * double-dialogging. Feedback follows NN/g "visibility of system status": every
 * apply resolves to an explicit success or a human-readable error, never a
 * silent close.
 */
const RecoverySuggestionsContent = ({
  projectId,
  runId,
  active,
  canWrite = true,
  onApplied,
}: RecoverySuggestionsContentProps) => {
  const theme = useTheme();
  const [info, setInfo] = useState<CoverageRecoveryInfo | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [applying, setApplying] = useState(false);
  const [appliedCount, setAppliedCount] = useState<number | null>(null);

  const load = useCallback(async () => {
    if (!runId) return;
    setLoading(true);
    setError(null);
    try {
      setInfo(await getCoverageRecovery(projectId, runId));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setLoading(false);
    }
  }, [projectId, runId]);

  useEffect(() => {
    if (active && runId) {
      load();
    }
    if (!active) {
      setInfo(null);
      setError(null);
      setAppliedCount(null);
    }
  }, [active, runId, load]);

  const handleApply = useCallback(
    async (acceptedItems: ChangesetItem[]) => {
      if (!runId) return;
      setApplying(true);
      setError(null);
      try {
        await applyCopilotChangeset(
          projectId,
          acceptedItems,
          info?.conversation_id ?? null,
        );
        // Applying resolves the notification for this run (best-effort).
        await dismissCoverageRecovery(projectId, runId).catch(() => undefined);
        setAppliedCount(acceptedItems.length);
        onApplied?.();
      } catch (caught) {
        // Human-readable, actionable — never surface a raw backend string as the
        // whole message (NN/g form-error guideline).
        const detail =
          caught instanceof Error ? caught.message : String(caught);
        setError(
          t('Could not apply the suggestions. Please try again.') +
            (detail ? ` (${detail})` : ''),
        );
      } finally {
        setApplying(false);
      }
    },
    [projectId, runId, info?.conversation_id, onApplied],
  );

  const status = info?.status;
  const changeset = info?.changeset ?? null;
  const running = status === 'running' || status === 'pending';

  if (appliedCount !== null) {
    return (
      <Alert
        type="success"
        showIcon
        message={t('Applied %s suggestion(s)', String(appliedCount))}
        description={t(
          'New files are saved as drafts — activate them when ready. Coverage re-analyses automatically whenever the active MDL changes.',
        )}
        data-test="recovery-applied"
      />
    );
  }

  if (loading) {
    return (
      <Flex align="center" gap={theme.sizeUnit * 2}>
        <Icons.LoadingOutlined spin iconSize="m" />
        <Typography.Text type="secondary">{t('Loading…')}</Typography.Text>
      </Flex>
    );
  }

  if (error) {
    return (
      <Alert type="error" showIcon message={error} data-test="recovery-error" />
    );
  }

  if (running) {
    return (
      <Flex
        align="center"
        gap={theme.sizeUnit * 2}
        data-test="recovery-preparing"
      >
        <Icons.LoadingOutlined spin iconSize="m" />
        <Typography.Text>
          {t('Preparing suggestions to close coverage gaps…')}
        </Typography.Text>
      </Flex>
    );
  }

  if (changeset && changeset.items.length > 0) {
    return (
      <Flex vertical gap={theme.sizeUnit * 2}>
        {info?.stale ? (
          <Alert
            type="warning"
            showIcon
            message={t(
              'The MDL has changed since these suggestions were generated. Review carefully or re-run coverage.',
            )}
          />
        ) : null}
        <Typography.Text type="secondary">
          {t(
            'The recovery agent proposes these edits to close documentation gaps. Approve the ones you want; they apply as drafts you can activate later.',
          )}
        </Typography.Text>
        <ChangesetReviewPanel
          changeset={changeset}
          actionable
          canWrite={canWrite}
          isApplying={applying}
          onApply={handleApply}
        />
      </Flex>
    );
  }

  return (
    <Empty
      image={Empty.PRESENTED_IMAGE_SIMPLE}
      description={t('No recovery suggestions for this coverage run.')}
    />
  );
};

export default RecoverySuggestionsContent;
