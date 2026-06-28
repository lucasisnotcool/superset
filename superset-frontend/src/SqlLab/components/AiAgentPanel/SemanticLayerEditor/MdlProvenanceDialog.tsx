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
  getCoverageStatus,
  getMdlProvenance,
  ProvenanceActorType,
  ProvenanceEntry,
  ProvenanceKind,
  ToolActionKind,
  ToolCallRecord,
} from '../api';
import { CopyButton } from '../AgentStepDetail';
import { CoverageReportBody } from './CoverageReportModal';
import { COVERAGE_EVENT_TYPES, useProjectEvents } from './useProjectEvents';

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
  project_created: t('Created project'),
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

// How many member files to show before collapsing behind a "+N more" toggle —
// keeps the timeline scannable (the aggregator pattern) while one click reveals
// the full per-file breakdown the literal ask wants ("agent wrote to a, b, …").
const MEMBER_PREVIEW = 3;

// Per-verb labels for the rollup line. Zero-count verbs are omitted upstream.
const ACTION_VERBS: Record<ToolActionKind, (n: number) => string> = {
  onboard: n => t('Onboarded %s table(s)', n),
  write: n => t('Wrote %s file(s)', n),
  relate: n => t('Added %s relationship(s)', n),
  delete: n => t('Deleted %s file(s)', n),
};
const ACTION_ORDER: ToolActionKind[] = ['onboard', 'write', 'relate', 'delete'];

const toolCalls = (entry: ProvenanceEntry): ToolCallRecord[] => {
  const raw = entry.detail?.tool_calls;
  return Array.isArray(raw) ? (raw as ToolCallRecord[]) : [];
};

// The aggregator rollup: "Onboarded 3 tables · Wrote 4 files". Reads the
// server-derived action_summary (counts the full ledger, even when capped).
const actionRollup = (entry: ProvenanceEntry): string | null => {
  const summary = entry.detail?.action_summary as
    | Record<string, number>
    | undefined;
  if (!summary) return null;
  const parts = ACTION_ORDER.filter(action => (summary[action] ?? 0) > 0).map(
    action => ACTION_VERBS[action](summary[action]),
  );
  return parts.length > 0 ? parts.join(' · ') : null;
};

// Ordered, de-duplicated file paths the agent touched this turn (ledger first,
// falling back to the changeset-level paths for legacy/hand entries).
const memberPaths = (entry: ProvenanceEntry): string[] => {
  const seen = new Set<string>();
  const ordered: string[] = [];
  const push = (path: unknown) => {
    if (typeof path === 'string' && !seen.has(path)) {
      seen.add(path);
      ordered.push(path);
    }
  };
  toolCalls(entry).forEach(call => (call.paths || []).forEach(push));
  if (ordered.length === 0) {
    const paths = entry.detail?.paths;
    if (Array.isArray(paths)) paths.forEach(push);
  }
  return ordered;
};

// path → source document id (R-B6): the doc a written file was derived from.
const sourceDocByPath = (entry: ProvenanceEntry): Map<string, string> => {
  const map = new Map<string, string>();
  toolCalls(entry).forEach(call => {
    const docId = call.source_document_ids?.[0];
    if (docId) (call.paths || []).forEach(path => map.set(path, docId));
  });
  return map;
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
  // True while a background coverage run is in flight for the current version —
  // rendered as a synthetic "analysing" row at the top of the timeline.
  const [coverageRunning, setCoverageRunning] = useState(false);
  // Entry ids whose full per-file member list is expanded (else previewed).
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
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
    // Coverage runs in the background under the latest/supersede policy; surface an
    // in-flight run as a synthetic top row (best-effort, never breaks the timeline).
    // The same COVERAGE_EVENT_TYPES subscription re-runs `load`, so this clears to a
    // `coverage_completed` entry when the run finishes.
    try {
      const status = await getCoverageStatus(projectId);
      setCoverageRunning(status.running === true);
    } catch {
      setCoverageRunning(false);
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

  // Live-refresh the timeline while the dialog is open (a closed dialog holds no
  // connection). Skip while a coverage report is being viewed so the drill-in
  // is not yanked out from under the user.
  const viewingReport =
    report !== null || reportLoading || reportError !== null;
  useProjectEvents(
    projectId,
    COVERAGE_EVENT_TYPES,
    load,
    open && !viewingReport,
  );

  if (viewingReport) {
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
        {coverageRunning ? (
          <Row data-test="provenance-coverage-running">
            <Icons.LoadingOutlined spin css={css({ marginTop: 4 })} />
            <Body>
              <Typography.Text strong>{t('Coverage')}</Typography.Text>
              <Typography.Text type="secondary">
                {t('Analysing the active model against the project documents…')}
              </Typography.Text>
            </Body>
          </Row>
        ) : null}
        {entries.map(entry => {
          const secondary = detailLine(entry);
          const actorType = entry.actor_type ?? 'system';
          // DP10: in a shared project a teammate's edit must not read as "You".
          // Show "You" only when the viewer is the actor; otherwise the author's
          // captured name (username/email), falling back to the actor id then a
          // generic "Teammate" label for historical/unnamed entries.
          const actorLabel =
            actorType === 'user' && entry.is_self === false
              ? (entry.actor_name ?? entry.actor ?? t('Teammate'))
              : ACTOR_LABELS[actorType];
          const documents = documentRefs(entry);
          const docNameById = new Map(
            documents
              .filter(doc => doc.id)
              .map(doc => [doc.id as string, doc.filename ?? doc.id]),
          );
          const rollup = actionRollup(entry);
          const files = memberPaths(entry);
          const docByPath = sourceDocByPath(entry);
          const isExpanded = expanded.has(entry.id);
          const shownFiles = isExpanded
            ? files
            : files.slice(0, MEMBER_PREVIEW);
          const hiddenCount = files.length - shownFiles.length;
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
                      {actorLabel}
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
                  {rollup ? (
                    <Typography.Text
                      type="secondary"
                      strong
                      data-test="provenance-rollup"
                    >
                      {rollup}
                    </Typography.Text>
                  ) : null}
                  {files.length > 0 ? (
                    <Flex wrap gap="small" data-test="provenance-files">
                      {shownFiles.map(path => {
                        const docId = docByPath.get(path);
                        const docName = docId
                          ? (docNameById.get(docId) ?? docId)
                          : null;
                        return (
                          <Tag
                            key={path}
                            icon={<Icons.FileOutlined />}
                            data-test="provenance-file"
                          >
                            {path}
                            {docName ? ` ← ${docName}` : ''}
                          </Tag>
                        );
                      })}
                      {hiddenCount > 0 ? (
                        <Button
                          type="link"
                          size="small"
                          onClick={() =>
                            setExpanded(prev => {
                              const next = new Set(prev);
                              next.add(entry.id);
                              return next;
                            })
                          }
                          data-test="provenance-files-expand"
                        >
                          {t('+%s more', hiddenCount)}
                        </Button>
                      ) : null}
                      {isExpanded && files.length > MEMBER_PREVIEW ? (
                        <Button
                          type="link"
                          size="small"
                          onClick={() =>
                            setExpanded(prev => {
                              const next = new Set(prev);
                              next.delete(entry.id);
                              return next;
                            })
                          }
                          data-test="provenance-files-collapse"
                        >
                          {t('Show less')}
                        </Button>
                      ) : null}
                    </Flex>
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
