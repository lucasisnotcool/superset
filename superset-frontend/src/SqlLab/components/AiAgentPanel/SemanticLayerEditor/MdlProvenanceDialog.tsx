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
import { css, styled } from '@apache-superset/core/theme';
import {
  Button,
  Empty,
  Flex,
  Modal,
  Tag,
  Typography,
} from '@superset-ui/core/components';
import { Icons } from '@superset-ui/core/components/Icons';
import {
  CoverageReport,
  getCoverageRun,
  getMdlProvenance,
  ProvenanceActorType,
  ProvenanceEntry,
  ProvenanceKind,
} from '../api';
import { CopyButton } from '../AgentStepDetail';
import { CoverageReportBody } from './CoverageReportModal';

// Reuses the AI Explain dialog's visual language (vertical, status-dotted
// timeline) — provenance is a linear time series rather than SQL attempts.
const Row = styled.div`
  ${({ theme }) => css`
    display: flex;
    gap: ${theme.sizeUnit * 2}px;
    padding: ${theme.sizeUnit * 2}px 0;
    border-bottom: 1px solid ${theme.colorBorderSecondary};
    &:last-of-type {
      border-bottom: none;
    }
  `}
`;

const Dot = styled.span<{ status: ProvenanceEntry['status'] }>`
  ${({ theme, status }) => {
    const color =
      status === 'error'
        ? theme.colorError
        : status === 'warning'
          ? theme.colorWarning
          : theme.colorSuccess;
    return css`
      flex: 0 0 auto;
      width: ${theme.sizeUnit * 2}px;
      height: ${theme.sizeUnit * 2}px;
      margin-top: ${theme.sizeUnit}px;
      border-radius: 50%;
      background: ${color};
    `;
  }}
`;

const Body = styled.div`
  flex: 1 1 auto;
  min-width: 0;
`;

const Header = styled.div`
  ${({ theme }) => css`
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    gap: ${theme.sizeUnit * 2}px;
  `}
`;

const Stamp = styled(Typography.Text)`
  ${({ theme }) => css`
    color: ${theme.colorTextTertiary};
    font-size: ${theme.fontSizeSM}px;
    white-space: nowrap;
  `}
`;

const KIND_LABELS: Record<ProvenanceKind, string> = {
  onboarding: t('Onboarding'),
  enrichment: t('Enrichment'),
  copilot_edit: t('Agent edit'),
  coverage: t('Coverage'),
  mdl_created: t('Created model'),
  mdl_updated: t('Edited model'),
  mdl_activated: t('Activated model'),
  mdl_deleted: t('Deleted model'),
};

const ACTOR_LABELS: Record<ProvenanceActorType, string> = {
  user: t('You'),
  agent: t('Agent'),
  system: t('System'),
};

const ACTOR_COLORS: Record<ProvenanceActorType, string> = {
  user: 'blue',
  agent: 'purple',
  system: 'default',
};

const formatStamp = (iso: string): string => {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleString();
};

// The time column: a point for a single event, a range for a coalesced run.
const stampLine = (entry: ProvenanceEntry): string => {
  if ((entry.edit_count ?? 1) > 1 && entry.first_at) {
    return t(
      '%s – %s',
      formatStamp(entry.first_at),
      formatStamp(entry.created_at),
    );
  }
  return formatStamp(entry.created_at);
};

type DocumentRef = { id?: string | null; filename?: string | null };

const documentRefs = (entry: ProvenanceEntry): DocumentRef[] => {
  const raw = entry.detail?.documents;
  return Array.isArray(raw) ? (raw as DocumentRef[]) : [];
};

// A short, human secondary line per entry, drawn from the structured detail.
const detailLine = (entry: ProvenanceEntry): string | null => {
  const detail = entry.detail || {};
  if ((entry.edit_count ?? 1) > 1) {
    return t('%s edits', entry.edit_count);
  }
  if (entry.kind === 'onboarding') {
    const count =
      (detail.model_count as number | undefined) ??
      ((detail.dataset_ids as number[] | undefined)?.length || 0);
    const mode = detail.mode === 'selected' ? t('selected') : t('whole schema');
    return t('%s model(s) · %s', count, mode);
  }
  if (entry.kind === 'mdl_activated') {
    return t('%s → %s', detail.status_from ?? '', detail.status_to ?? '');
  }
  if (entry.kind === 'coverage' && typeof detail.score === 'number') {
    return t('Score %s%', Math.round((detail.score as number) * 100));
  }
  if (detail.document_id) {
    return String(detail.filename ?? detail.document_id);
  }
  return (detail.path as string | undefined) ?? null;
};

export interface MdlProvenanceDialogProps {
  open: boolean;
  projectId?: string | null;
  onClose: () => void;
  /** Deep-link from an agent/enrichment entry to its Copilot conversation. */
  onOpenConversation?: (conversationId: string) => void;
}

const MdlProvenanceDialog = ({
  open,
  projectId,
  onClose,
  onOpenConversation,
}: MdlProvenanceDialogProps) => {
  const [entries, setEntries] = useState<ProvenanceEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Coverage drill-in: a stored report opened from a `coverage` timeline entry.
  const [report, setReport] = useState<CoverageReport | null>(null);
  const [reportLoading, setReportLoading] = useState(false);
  const [reportError, setReportError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!projectId) return;
    setLoading(true);
    setError(null);
    try {
      setEntries(await getMdlProvenance(projectId));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  const openCoverage = useCallback(
    async (runId: string) => {
      if (!projectId) return;
      setReportLoading(true);
      setReportError(null);
      setReport(null);
      try {
        const run = await getCoverageRun(projectId, runId);
        setReport(run.report ?? null);
      } catch (caught) {
        setReportError(
          caught instanceof Error ? caught.message : String(caught),
        );
      } finally {
        setReportLoading(false);
      }
    },
    [projectId],
  );

  useEffect(() => {
    if (open) {
      load();
      setReport(null);
      setReportError(null);
    }
  }, [open, load]);

  const showingReport =
    report !== null || reportLoading || reportError !== null;

  if (showingReport) {
    return (
      <Modal
        show={open}
        onHide={onClose}
        title={t('Coverage report')}
        footer={null}
      >
        <Button
          type="link"
          size="small"
          onClick={() => {
            setReport(null);
            setReportError(null);
          }}
          data-test="provenance-coverage-back"
        >
          {t('← Back to history')}
        </Button>
        <CoverageReportBody
          report={report}
          loading={reportLoading}
          error={reportError}
        />
      </Modal>
    );
  }

  return (
    <Modal
      show={open}
      onHide={onClose}
      title={t('MDL provenance')}
      footer={null}
    >
      <Header>
        <Typography.Text type="secondary">
          {loading
            ? t('Loading…')
            : t('%s operation(s) on this semantic layer', entries.length)}
        </Typography.Text>
        {entries.length > 0 ? (
          <CopyButton text={JSON.stringify(entries, null, 2)} />
        ) : null}
      </Header>

      {error ? <Typography.Text type="danger">{error}</Typography.Text> : null}

      {!loading && entries.length === 0 && !error ? (
        <Empty
          image={Empty.PRESENTED_IMAGE_SIMPLE}
          description={t('No history yet — onboard a schema to begin.')}
        />
      ) : null}

      <div data-test="provenance-timeline">
        {entries.map(entry => {
          const secondary = detailLine(entry);
          const actorType = entry.actor_type ?? 'system';
          const documents = documentRefs(entry);
          const conversationId = entry.detail?.conversation_id as
            | string
            | undefined;
          const runId = entry.detail?.run_id as string | undefined;
          return (
            <Row key={entry.id} data-test="provenance-entry">
              <Dot status={entry.status} />
              <Body>
                <Header>
                  <Flex align="center" gap="small">
                    <Typography.Text strong>
                      {KIND_LABELS[entry.kind] ?? entry.kind}
                    </Typography.Text>
                    <Tag
                      color={ACTOR_COLORS[actorType]}
                      data-test="provenance-actor"
                    >
                      {ACTOR_LABELS[actorType]}
                    </Tag>
                  </Flex>
                  <Stamp>{stampLine(entry)}</Stamp>
                </Header>
                <Flex vertical>
                  <Typography.Text>{entry.summary}</Typography.Text>
                  {secondary ? (
                    <Typography.Text type="secondary">
                      {secondary}
                      {entry.actor ? ` · ${entry.actor}` : ''}
                    </Typography.Text>
                  ) : null}
                  {documents.length > 0 ? (
                    <Flex wrap gap="small" data-test="provenance-documents">
                      {documents.map(doc => (
                        <Tag
                          key={doc.id ?? doc.filename}
                          icon={<Icons.FileTextOutlined />}
                        >
                          {doc.filename ?? doc.id}
                        </Tag>
                      ))}
                    </Flex>
                  ) : null}
                  <Flex gap="small">
                    {conversationId && onOpenConversation ? (
                      <Button
                        type="link"
                        size="small"
                        onClick={() => onOpenConversation(conversationId)}
                        data-test="provenance-open-conversation"
                      >
                        {t('View conversation')}
                      </Button>
                    ) : null}
                    {entry.kind === 'coverage' && runId ? (
                      <Button
                        type="link"
                        size="small"
                        onClick={() => openCoverage(runId)}
                        data-test="provenance-open-coverage"
                      >
                        {t('View report')}
                      </Button>
                    ) : null}
                  </Flex>
                </Flex>
              </Body>
            </Row>
          );
        })}
      </div>
    </Modal>
  );
};

export const ProvenanceIcon = Icons.HistoryOutlined;

export default MdlProvenanceDialog;
