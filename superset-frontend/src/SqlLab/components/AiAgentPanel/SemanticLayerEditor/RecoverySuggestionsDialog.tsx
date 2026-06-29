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
import {
  Button,
  Empty,
  Flex,
  Modal,
  Typography,
} from '@superset-ui/core/components';
import { Icons } from '@superset-ui/core/components/Icons';
import {
  applyCopilotChangeset,
  ChangesetItem,
  CoverageRecoveryInfo,
  dismissCoverageRecovery,
  getCoverageRecovery,
} from '../api';
import ChangesetReviewPanel from './ChangesetReviewPanel';

export interface RecoverySuggestionsDialogProps {
  projectId: string;
  /** Coverage run whose recovery suggestions to review. */
  runId?: string | null;
  open: boolean;
  canWrite?: boolean;
  onClose: () => void;
  /** Called after suggestions are applied (drafts created) so callers refresh. */
  onApplied?: () => void;
}

/**
 * Reviews the coverage recovery agent's gap-closing suggestions as a diff
 * changeset (per-item approve/reject) and applies the accepted ones as drafts.
 * Applying or dismissing clears the "suggestions ready" notification for the run.
 * Never auto-applies; activation stays a separate, deliberate step.
 */
const RecoverySuggestionsDialog = ({
  projectId,
  runId,
  open,
  canWrite = true,
  onClose,
  onApplied,
}: RecoverySuggestionsDialogProps) => {
  const theme = useTheme();
  const [info, setInfo] = useState<CoverageRecoveryInfo | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [applying, setApplying] = useState(false);

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
    if (open && runId) {
      load();
    }
    if (!open) {
      setInfo(null);
      setError(null);
    }
  }, [open, runId, load]);

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
        // Applying resolves the notification for this run.
        await dismissCoverageRecovery(projectId, runId).catch(() => undefined);
        onApplied?.();
        onClose();
      } catch (caught) {
        setError(caught instanceof Error ? caught.message : String(caught));
      } finally {
        setApplying(false);
      }
    },
    [projectId, runId, info?.conversation_id, onApplied, onClose],
  );

  const handleDismiss = useCallback(async () => {
    if (!runId) return;
    try {
      await dismissCoverageRecovery(projectId, runId);
      onApplied?.();
    } catch {
      // best-effort: a failed dismiss leaves the banner; not surfaced
    }
    onClose();
  }, [projectId, runId, onApplied, onClose]);

  const status = info?.status;
  const changeset = info?.changeset ?? null;
  const running = status === 'running' || status === 'pending';

  let body;
  if (loading) {
    body = (
      <Flex align="center" gap={theme.sizeUnit * 2}>
        <Icons.LoadingOutlined spin iconSize="m" />
        <Typography.Text type="secondary">{t('Loading…')}</Typography.Text>
      </Flex>
    );
  } else if (error) {
    body = <Alert type="error" showIcon message={error} />;
  } else if (running) {
    body = (
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
  } else if (changeset && changeset.items.length > 0) {
    body = (
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
  } else {
    body = (
      <Empty
        image={Empty.PRESENTED_IMAGE_SIMPLE}
        description={t('No recovery suggestions for this coverage run.')}
      />
    );
  }

  return (
    <Modal
      show={open}
      onHide={onClose}
      title={t('Coverage suggestions')}
      footer={
        <Flex justify="end" gap={theme.sizeUnit * 2}>
          <Button onClick={handleDismiss} data-test="recovery-dismiss">
            {t('Dismiss')}
          </Button>
          <Button onClick={onClose} data-test="recovery-close">
            {t('Close')}
          </Button>
        </Flex>
      }
      data-test="recovery-dialog"
    >
      {body}
    </Modal>
  );
};

export default RecoverySuggestionsDialog;
