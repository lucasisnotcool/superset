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
import {
  Button,
  Checkbox,
  Empty,
  Flex,
  Modal,
  Select,
} from '@superset-ui/core/components';
import {
  CoverageReport,
  listProjectDocuments,
  runCoverage,
  SemanticDocument,
} from '../api';
import { CoverageReportBody } from './CoverageReportModal';

export interface CoverageDialogProps {
  projectId: string;
  open: boolean;
  onClose: () => void;
}

const CoverageDialog = ({ projectId, open, onClose }: CoverageDialogProps) => {
  const theme = useTheme();
  const [documents, setDocuments] = useState<SemanticDocument[]>([]);
  const [selectedId, setSelectedId] = useState<string | undefined>();
  const [report, setReport] = useState<CoverageReport | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [includeOverreach, setIncludeOverreach] = useState(false);

  useEffect(() => {
    if (!open) {
      return;
    }
    setReport(null);
    setError(null);
    listProjectDocuments(projectId)
      .then(docs => {
        setDocuments(docs);
        setSelectedId(docs[0]?.id);
      })
      .catch(caught =>
        setError(caught instanceof Error ? caught.message : String(caught)),
      );
  }, [open, projectId]);

  const handleRun = useCallback(async () => {
    if (!selectedId) {
      return;
    }
    setLoading(true);
    setError(null);
    setReport(null);
    try {
      setReport(await runCoverage(projectId, selectedId, includeOverreach));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setLoading(false);
    }
  }, [projectId, selectedId, includeOverreach]);

  return (
    <Modal
      title={t('Coverage audit')}
      show={open}
      onHide={onClose}
      footer={null}
      data-test="coverage-dialog"
    >
      <Flex vertical gap={theme.sizeUnit * 2}>
        {documents.length === 0 && !error ? (
          <Empty
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            description={t(
              'Upload a document to audit coverage against the MDL.',
            )}
          />
        ) : (
          <Flex gap={theme.sizeUnit * 2} align="center">
            <Select
              value={selectedId}
              onChange={value => setSelectedId(value as string)}
              options={documents.map(doc => ({
                value: doc.id,
                label: doc.filename,
              }))}
              placeholder={t('Select a document')}
              css={{ flex: 1 }}
              data-test="coverage-document-select"
            />
            <Button
              buttonStyle="primary"
              disabled={!selectedId || loading}
              loading={loading}
              onClick={handleRun}
              data-test="coverage-run"
            >
              {t('Run audit')}
            </Button>
          </Flex>
        )}
        {documents.length > 0 ? (
          <Checkbox
            checked={includeOverreach}
            onChange={event => setIncludeOverreach(event.target.checked)}
            data-test="coverage-overreach-toggle"
          >
            {t('Also flag MDL not supported by the document (over-reach)')}
          </Checkbox>
        ) : null}
        <CoverageReportBody report={report} loading={loading} error={error} />
      </Flex>
    </Modal>
  );
};

export default CoverageDialog;
