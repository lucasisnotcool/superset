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
  Divider,
  Empty,
  Flex,
  Modal,
  Typography,
} from '@superset-ui/core/components';
import { Icons } from '@superset-ui/core/components/Icons';
import { CoverageReport, CoverageStatusInfo, getLatestCoverage } from '../api';
import { CoverageReportBody } from './CoverageReportModal';
import CoverageProgress from './CoverageProgress';
import RecoverySuggestionsContent from './RecoverySuggestionsContent';

export interface CoveragePanelProps {
  projectId: string;
  /** Live status that drives which view renders; owned by the badge. */
  info: CoverageStatusInfo | null;
  open: boolean;
  onClose: () => void;
  /** Schedule a fresh run (explicit action, never the badge click itself). */
  onRerun: () => void;
  /** Gates applying the recovery agent's suggestions from the second pane. */
  canWrite?: boolean;
  /** Re-poll live status (e.g. after suggestions apply → coverage re-analyses). */
  onRefresh?: () => void;
}

const formatTimestamp = (iso?: string | null): string => {
  if (!iso) return '';
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleString();
};

/**
 * The coverage viewer (Feature A). Opened by clicking the coverage badge; shows
 * the in-flight run's progress, the stored report, or an empty state with an
 * explicit run action. It never re-runs analysis on open — re-run is a separate,
 * clearly-labelled button (industry convention: a status badge is a passive
 * indicator that opens detail; actions are separate controls).
 */
const CoveragePanel = ({
  projectId,
  info,
  open,
  onClose,
  onRerun,
  canWrite = true,
  onRefresh,
}: CoveragePanelProps) => {
  const theme = useTheme();
  const [report, setReport] = useState<CoverageReport | null>(null);
  const [computedAt, setComputedAt] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [suggestionsOpen, setSuggestionsOpen] = useState(false);

  const status = info?.status;
  const running = status === 'analysing';
  const stale = status === 'stale';
  const hasReport = status === 'ready' || status === 'stale';
  // Second entrypoint to the recovery suggestions (the banner is the first); both
  // survive each other's dismissal because the state is server-side. The report
  // also surfaces the recovery agent's in-flight / failed states here so the user
  // is not left wondering whether suggestions are coming.
  const recoveryRunId = info?.recovery_run_id ?? null;
  const recoveryStatus = info?.recovery_status;
  const recoveryReady =
    recoveryStatus === 'ready' &&
    info?.recovery_dismissed === false &&
    Boolean(recoveryRunId);
  const recoveryPreparing =
    recoveryStatus === 'pending' || recoveryStatus === 'running';
  const recoveryFailed = recoveryStatus === 'failed';

  const loadReport = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const run = await getLatestCoverage(projectId);
      setReport(run?.report ?? null);
      setComputedAt(run?.updated_at ?? null);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  // Fetch the stored report whenever the panel is open and a completed run
  // exists. Re-fetches when the run id changes (e.g. analysing → ready while the
  // panel stays open), so a run finishing live swaps progress → report.
  useEffect(() => {
    if (open && hasReport) {
      loadReport();
    }
    if (!open) {
      setReport(null);
      setError(null);
      setSuggestionsOpen(false);
    }
  }, [open, hasReport, info?.run_id, loadReport]);

  let body;
  if (running) {
    body = <CoverageProgress progress={info?.progress} />;
  } else if (hasReport) {
    // The freshness alerts and "Computed …" stamp belong with the score/badges
    // in the pinned summary, so they stay in view while the claims scroll.
    const summaryExtra = (
      <>
        {stale ? (
          <Alert
            type="warning"
            showIcon
            message={t(
              'This report was computed for an earlier version of the MDL. Re-run to refresh.',
            )}
          />
        ) : null}
        {computedAt ? (
          <Typography.Text type="secondary">
            {t('Computed %s', formatTimestamp(computedAt))}
          </Typography.Text>
        ) : null}
        {recoveryReady ? (
          <Alert
            type="info"
            showIcon
            message={t('Coverage suggestions ready')}
            description={t(
              'The recovery agent proposed edits to close these gaps.',
            )}
            action={
              <Button
                buttonSize="small"
                buttonStyle="primary"
                onClick={() => setSuggestionsOpen(true)}
                data-test="coverage-review-suggestions"
              >
                {t('Review')}
              </Button>
            }
          />
        ) : null}
        {recoveryPreparing ? (
          <Alert
            type="info"
            showIcon
            icon={<Icons.LoadingOutlined spin />}
            message={t('Preparing coverage suggestions…')}
            description={t(
              'The recovery agent is drafting edits to close these gaps. This runs in the background — you can keep working.',
            )}
            data-test="coverage-recovery-preparing"
          />
        ) : null}
        {recoveryFailed ? (
          <Alert
            type="warning"
            showIcon
            message={t('Coverage suggestions unavailable')}
            description={t(
              'The recovery agent could not generate suggestions for this report. It will try again after the next coverage run.',
            )}
            data-test="coverage-recovery-failed"
          />
        ) : null}
      </>
    );
    body = (
      <CoverageReportBody
        report={report}
        loading={loading}
        error={error}
        summaryExtra={summaryExtra}
      />
    );
  } else {
    // status === 'none' (or unknown): no run exists yet.
    body = (
      <Empty
        image={Empty.PRESENTED_IMAGE_SIMPLE}
        description={t(
          'Coverage has not been analysed yet. Run it to see how much of your source documents the MDL captures.',
        )}
      />
    );
  }

  // When the user chooses to review suggestions, the report dialog extends into a
  // second pane instead of stacking a nested modal (recenters via the wider
  // width). Both panes live in one dialog with one set of footer actions.
  const showSuggestions = suggestionsOpen && Boolean(recoveryRunId);

  return (
    <Modal
      title={t('Coverage')}
      show={open}
      onHide={onClose}
      centered
      width={showSuggestions ? '960px' : undefined}
      footer={
        <Flex justify="end" gap={theme.sizeUnit * 2}>
          <Button onClick={onClose} data-test="coverage-panel-close">
            {t('Close')}
          </Button>
          <Button
            buttonStyle="primary"
            icon={<Icons.SyncOutlined />}
            disabled={running}
            onClick={onRerun}
            data-test="coverage-rerun"
          >
            {running
              ? t('Analysing…')
              : hasReport
                ? t('Re-run analysis')
                : t('Run analysis')}
          </Button>
        </Flex>
      }
      data-test="coverage-panel"
    >
      {showSuggestions ? (
        <Flex gap={theme.sizeUnit * 4} align="stretch">
          <div style={{ flex: '1 1 0', minWidth: 0 }}>{body}</div>
          <Divider type="vertical" style={{ height: 'auto' }} />
          <Flex
            vertical
            gap={theme.sizeUnit * 2}
            style={{ flex: '1 1 0', minWidth: 0 }}
            data-test="coverage-suggestions-pane"
          >
            <Flex justify="space-between" align="center">
              <Typography.Title level={5} style={{ margin: 0 }}>
                {t('Coverage suggestions')}
              </Typography.Title>
              <Button
                buttonStyle="link"
                buttonSize="small"
                onClick={() => setSuggestionsOpen(false)}
                data-test="coverage-suggestions-hide"
              >
                {t('Hide')}
              </Button>
            </Flex>
            <RecoverySuggestionsContent
              projectId={projectId}
              runId={recoveryRunId}
              active={showSuggestions}
              canWrite={canWrite}
              onApplied={onRefresh}
            />
          </Flex>
        </Flex>
      ) : (
        body
      )}
    </Modal>
  );
};

export default CoveragePanel;
