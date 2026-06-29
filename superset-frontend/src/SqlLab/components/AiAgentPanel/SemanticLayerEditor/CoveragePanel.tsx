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
import { CoverageReport, CoverageStatusInfo, getLatestCoverage } from '../api';
import { CoverageReportBody } from './CoverageReportModal';
import CoverageProgress from './CoverageProgress';

export interface CoveragePanelProps {
  projectId: string;
  /** Live status that drives which view renders; owned by the badge. */
  info: CoverageStatusInfo | null;
  open: boolean;
  onClose: () => void;
  /** Schedule a fresh run (explicit action, never the badge click itself). */
  onRerun: () => void;
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
}: CoveragePanelProps) => {
  const theme = useTheme();
  const [report, setReport] = useState<CoverageReport | null>(null);
  const [computedAt, setComputedAt] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const status = info?.status;
  const running = status === 'analysing';
  const stale = status === 'stale';
  const hasReport = status === 'ready' || status === 'stale';

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
    }
  }, [open, hasReport, info?.run_id, loadReport]);

  let body;
  if (running) {
    body = <CoverageProgress progress={info?.progress} />;
  } else if (hasReport) {
    body = (
      <Flex vertical gap={theme.sizeUnit * 2}>
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
        <CoverageReportBody report={report} loading={loading} error={error} />
      </Flex>
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

  return (
    <Modal
      title={t('Coverage')}
      show={open}
      onHide={onClose}
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
      {body}
    </Modal>
  );
};

export default CoveragePanel;
