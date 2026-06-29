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
import { Alert } from '@apache-superset/core/components';
import { Button, Flex } from '@superset-ui/core/components';
import {
  CoverageStatusInfo,
  dismissCoverageRecovery,
  getCoverageStatus,
} from '../api';
import { COVERAGE_EVENT_TYPES, useProjectEvents } from './useProjectEvents';
import RecoverySuggestionsDialog from './RecoverySuggestionsDialog';

// Safety net for missed SSE events; the recovery_suggestions_ready event drives
// the live update, this only covers buffering proxies. Matches CoverageBadge.
const FALLBACK_POLL_MS = 30000;

export interface RecoveryBannerProps {
  projectId?: string | null;
  canWrite?: boolean;
}

/**
 * Persistent, dismissible notification (Material/Carbon banner register — not a
 * toast): shown when the latest coverage run's recovery agent has gap-closing
 * suggestions and the user has not dismissed them. Opens the review dialog;
 * dismissal is durable (server-side, per run). One banner per run.
 */
const RecoveryBanner = ({
  projectId,
  canWrite = true,
}: RecoveryBannerProps) => {
  const [info, setInfo] = useState<CoverageStatusInfo | null>(null);
  const [open, setOpen] = useState(false);

  const poll = useCallback(async () => {
    if (!projectId) return;
    try {
      setInfo(await getCoverageStatus(projectId));
    } catch {
      // Best-effort banner — a transient status failure is not surfaced.
    }
  }, [projectId]);

  useEffect(() => {
    setInfo(null);
    setOpen(false);
  }, [projectId]);

  useProjectEvents(projectId, COVERAGE_EVENT_TYPES, poll, Boolean(projectId));

  useEffect(() => {
    poll();
  }, [poll]);

  useEffect(() => {
    if (!projectId) return undefined;
    const id = setInterval(poll, FALLBACK_POLL_MS);
    return () => clearInterval(id);
  }, [projectId, poll]);

  const onDismiss = useCallback(async () => {
    const runId = info?.recovery_run_id;
    if (!projectId || !runId) return;
    try {
      await dismissCoverageRecovery(projectId, runId);
    } catch {
      // ignore — the banner stays until the next successful dismiss
    }
    poll();
  }, [projectId, info?.recovery_run_id, poll]);

  const runId = info?.recovery_run_id ?? null;
  const visible =
    Boolean(projectId) &&
    info?.recovery_status === 'ready' &&
    info?.recovery_dismissed === false &&
    Boolean(runId);

  if (!visible) return null;

  return (
    <div data-test="recovery-banner">
      <Alert
        type="info"
        showIcon
        message={t('Coverage suggestions ready')}
        description={
          <Flex justify="space-between" align="center" gap="small" wrap="wrap">
            <span>
              {t(
                'The recovery agent proposed edits to close documentation gaps.',
              )}
            </span>
            <Flex gap="small">
              <Button
                buttonSize="small"
                buttonStyle="primary"
                onClick={() => setOpen(true)}
                data-test="recovery-banner-review"
              >
                {t('Review')}
              </Button>
              <Button
                buttonSize="small"
                onClick={onDismiss}
                data-test="recovery-banner-dismiss"
              >
                {t('Dismiss')}
              </Button>
            </Flex>
          </Flex>
        }
      />
      <RecoverySuggestionsDialog
        projectId={projectId as string}
        runId={runId}
        open={open}
        canWrite={canWrite}
        onClose={() => setOpen(false)}
        onApplied={poll}
      />
    </div>
  );
};

export default RecoveryBanner;
