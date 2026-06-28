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
import { useTheme } from '@apache-superset/core/theme';
import { Alert } from '@apache-superset/core/components';
import {
  Empty,
  Flex,
  Modal,
  Tag,
  Typography,
} from '@superset-ui/core/components';
import { Icons } from '@superset-ui/core/components/Icons';
import { CoverageReport, CoverageStatus } from '../api';

export interface CoverageReportModalProps {
  open: boolean;
  report: CoverageReport | null;
  loading?: boolean;
  error?: string | null;
  onClose: () => void;
}

const STATUS_COLOR: Record<CoverageStatus, string> = {
  covered: 'success',
  partial: 'warning',
  missing: 'error',
};

/** Presentational body — shared by the standalone modal and the launcher. */
export const CoverageReportBody = ({
  report,
  loading,
  error,
}: {
  report: CoverageReport | null;
  loading?: boolean;
  error?: string | null;
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
          {report.findings.length === 0 ? (
            <Empty
              image={Empty.PRESENTED_IMAGE_SIMPLE}
              description={t('No claims to report.')}
            />
          ) : (
            report.findings.map((finding, index) => (
              <Flex
                vertical
                // eslint-disable-next-line react/no-array-index-key
                key={`finding-${index}`}
                gap={theme.sizeUnit}
                css={{
                  border: `1px solid ${theme.colorBorderSecondary}`,
                  borderRadius: theme.borderRadius,
                  padding: theme.sizeUnit * 2,
                }}
                data-test="coverage-finding"
              >
                <Flex align="center" gap={theme.sizeUnit} wrap="wrap">
                  <Tag color={STATUS_COLOR[finding.status]}>
                    {finding.status}
                  </Tag>
                  <Tag>{finding.claim.kind}</Tag>
                  <Typography.Text strong>
                    {finding.claim.subject}
                  </Typography.Text>
                  {finding.document_filename ? (
                    <Tag
                      icon={<Icons.FileTextOutlined />}
                      data-test="coverage-finding-source"
                    >
                      {finding.document_filename}
                    </Tag>
                  ) : null}
                </Flex>
                <Typography.Text>{finding.claim.statement}</Typography.Text>
                {finding.matched ? (
                  <Typography.Text type="secondary">
                    {t('Matched: %s', finding.matched)}
                  </Typography.Text>
                ) : null}
                {finding.suggestion ? (
                  <Typography.Text type="warning">
                    {t('Fix: %s', finding.suggestion)}
                  </Typography.Text>
                ) : null}
              </Flex>
            ))
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
    data-test="coverage-modal"
  >
    <CoverageReportBody report={report} loading={loading} error={error} />
  </Modal>
);

export default CoverageReportModal;
