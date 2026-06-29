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
import { type ReactNode } from 'react';
import { t } from '@apache-superset/core/translation';
import { useTheme } from '@apache-superset/core/theme';
import { Alert } from '@apache-superset/core/components';
import {
  Empty,
  Flex,
  Modal,
  Tag,
  Typography,
} from '@superset-ui/core/components';
import { CoverageReport } from '../api';
import { CoverageFindingsList } from './CoverageFindingsList';

export interface CoverageReportModalProps {
  open: boolean;
  report: CoverageReport | null;
  loading?: boolean;
  error?: string | null;
  onClose: () => void;
}

/**
 * Presentational body — shared by the standalone modal and the launchers.
 *
 * Layout: the score, coverage-type badges, document name and warnings sit in a
 * pinned summary that stays in view while the (potentially large) list of
 * claims scrolls beneath it. The claims list is virtualized and height-capped
 * to the viewport so the surrounding dialog never grows past the screen.
 *
 * ``summaryExtra`` lets a caller inject extra rows into the pinned summary
 * (e.g. the "Computed …" timestamp and freshness alerts of the viewer).
 */
export const CoverageReportBody = ({
  report,
  loading,
  error,
  summaryExtra,
}: {
  report: CoverageReport | null;
  loading?: boolean;
  error?: string | null;
  summaryExtra?: ReactNode;
}) => {
  const theme = useTheme();
  const pct = report ? Math.round(report.score * 100) : 0;

  return (
    <>
      {error ? <Alert type="error" showIcon message={error} /> : null}
      {loading ? (
        <Typography.Text type="secondary" data-test="coverage-loading">
          {t('Auditing document against the MDL…')}
        </Typography.Text>
      ) : null}
      {!loading && !error && report ? (
        <Flex vertical gap={theme.sizeUnit * 2} data-test="coverage-report">
          <Flex
            vertical
            gap={theme.sizeUnit * 2}
            css={{
              position: 'sticky',
              top: 0,
              zIndex: 1,
              background: theme.colorBgContainer,
              paddingBottom: theme.sizeUnit,
            }}
            data-test="coverage-summary"
          >
            {summaryExtra}
            <Flex align="center" gap={theme.sizeUnit * 2} wrap="wrap">
              <Typography.Title level={3} style={{ margin: 0 }}>
                {pct}% {t('covered')}
              </Typography.Title>
              <Tag color="success">{t('%s covered', report.covered)}</Tag>
              <Tag color="warning">{t('%s partial', report.partial)}</Tag>
              <Tag color="error">{t('%s missing', report.missing)}</Tag>
            </Flex>
            {report.document_filename ? (
              <Typography.Text type="secondary">
                {report.document_filename}
              </Typography.Text>
            ) : null}
            {report.warnings.map(warning => (
              <Alert key={warning} type="warning" showIcon message={warning} />
            ))}
          </Flex>
          {report.findings.length === 0 ? (
            <Empty
              image={Empty.PRESENTED_IMAGE_SIMPLE}
              description={t('No claims to report.')}
            />
          ) : (
            <CoverageFindingsList findings={report.findings} />
          )}
          {(report.overreach ?? []).length > 0 ? (
            <Flex vertical gap={theme.sizeUnit} data-test="coverage-overreach">
              <Typography.Text strong>
                {t(
                  '%s MDL fact(s) unsupported by the document',
                  report.unsupported,
                )}
              </Typography.Text>
              {(report.overreach ?? []).map(item => (
                <Typography.Text key={item.fact_ref} type="secondary">
                  {item.fact_ref}
                  {item.rationale ? ` — ${item.rationale}` : ''}
                </Typography.Text>
              ))}
            </Flex>
          ) : null}
        </Flex>
      ) : null}
    </>
  );
};

const CoverageReportModal = ({
  open,
  report,
  loading,
  error,
  onClose,
}: CoverageReportModalProps) => (
  <Modal
    title={t('Coverage audit')}
    show={open}
    onHide={onClose}
    footer={null}
    centered
    data-test="coverage-modal"
  >
    <CoverageReportBody report={report} loading={loading} error={error} />
  </Modal>
);

export default CoverageReportModal;
