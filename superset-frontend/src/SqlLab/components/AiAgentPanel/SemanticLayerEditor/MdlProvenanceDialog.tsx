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
import { Empty, Flex, Modal, Typography } from '@superset-ui/core/components';
import { Icons } from '@superset-ui/core/components/Icons';
import { getMdlProvenance, ProvenanceEntry, ProvenanceKind } from '../api';
import { CopyButton } from '../AgentStepDetail';

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
  mdl_created: t('Created model'),
  mdl_updated: t('Edited model'),
  mdl_activated: t('Activated model'),
  mdl_deleted: t('Deleted model'),
};

const formatStamp = (iso: string): string => {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleString();
};

// A short, human secondary line per entry, drawn from the structured detail.
const detailLine = (entry: ProvenanceEntry): string | null => {
  const detail = entry.detail || {};
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
  if (detail.document_id) {
    return String(detail.filename ?? detail.document_id);
  }
  return (detail.path as string | undefined) ?? null;
};

export interface MdlProvenanceDialogProps {
  open: boolean;
  projectId?: string | null;
  onClose: () => void;
}

const MdlProvenanceDialog = ({
  open,
  projectId,
  onClose,
}: MdlProvenanceDialogProps) => {
  const [entries, setEntries] = useState<ProvenanceEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

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

  useEffect(() => {
    if (open) load();
  }, [open, load]);

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
          return (
            <Row key={entry.id} data-test="provenance-entry">
              <Dot status={entry.status} />
              <Body>
                <Header>
                  <Typography.Text strong>
                    {KIND_LABELS[entry.kind] ?? entry.kind}
                  </Typography.Text>
                  <Stamp>{formatStamp(entry.created_at)}</Stamp>
                </Header>
                <Flex vertical>
                  <Typography.Text>{entry.summary}</Typography.Text>
                  {secondary ? (
                    <Typography.Text type="secondary">
                      {secondary}
                      {entry.actor ? ` · ${entry.actor}` : ''}
                    </Typography.Text>
                  ) : null}
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
