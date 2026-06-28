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
import {
  ChangeEvent,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import { t } from '@apache-superset/core/translation';
import { css, useTheme } from '@apache-superset/core/theme';
import {
  Button,
  Checkbox,
  Empty,
  Flex,
  Modal,
  Typography,
} from '@superset-ui/core/components';
import { Icons } from '@superset-ui/core/components/Icons';
import { SemanticDocument } from '../api';
import {
  DocumentStatusTag,
  formatBytes,
  isPendingDocumentStatus,
} from './documentStatus';

// The same upload surface the Copilot "Attach" button accepts — kept in sync so
// Auto-onboard and Attach ingest identical file types.
const ACCEPT = '.pdf,.docx,.xlsx,.pptx,.md,.json,.csv,.html,.txt';

const LIST_HEIGHT = 280;

export interface AutoOnboardModalProps {
  open: boolean;
  canWrite: boolean;
  /** The project's already-uploaded documents (the selectable corpus). */
  documents: SemanticDocument[];
  /** True while an upload triggered from this modal is in flight. */
  isUploading?: boolean;
  /**
   * Ingest picked files through the shared pipeline and resolve to the persisted
   * documents (deduped server-side). The modal appends + auto-selects the result.
   */
  onUpload: (files: File[]) => Promise<SemanticDocument[]>;
  onCancel: () => void;
  /** Confirm with the selected documents to hand off to the Copilot kickstart. */
  onConfirm: (documents: SemanticDocument[]) => void;
}

/**
 * Auto-onboard entry: pick (or upload) the BI documents the Copilot should read,
 * then confirm to kickstart a doc-driven onboarding conversation. This modal only
 * gathers documents — the actual onboarding is the Copilot turn the parent fires
 * with these documents attached.
 */
export default function AutoOnboardModal({
  open,
  canWrite,
  documents,
  isUploading = false,
  onUpload,
  onCancel,
  onConfirm,
}: AutoOnboardModalProps) {
  const theme = useTheme();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  // Documents uploaded from within this modal this session. Merged with the
  // project's existing documents so a freshly uploaded file is immediately
  // selectable (and auto-selected) without waiting for a parent refresh.
  const [uploaded, setUploaded] = useState<SemanticDocument[]>([]);

  // A fresh open starts from a clean slate.
  useEffect(() => {
    if (open) {
      setSelectedIds(new Set());
      setUploaded([]);
    }
  }, [open]);

  // Existing docs first, then this session's uploads, deduped by id (an upload
  // that dedups to an existing doc must not appear twice).
  const allDocuments = useMemo(() => {
    const byId = new Map<string, SemanticDocument>();
    documents.forEach(doc => byId.set(doc.id, doc));
    uploaded.forEach(doc => byId.set(doc.id, doc));
    return Array.from(byId.values());
  }, [documents, uploaded]);

  const toggle = useCallback((id: string) => {
    setSelectedIds(prev => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  }, []);

  const handleUpload = useCallback(
    async (event: ChangeEvent<HTMLInputElement>) => {
      const files = Array.from(event.target.files ?? []);
      // Reset the input so re-picking the same file fires onChange again.
      event.target.value = '';
      if (files.length === 0) {
        return;
      }
      const ingested = await onUpload(files);
      if (ingested.length === 0) {
        return;
      }
      setUploaded(prev => {
        const seen = new Set(prev.map(doc => doc.id));
        return [...prev, ...ingested.filter(doc => !seen.has(doc.id))];
      });
      // Auto-select what was just uploaded — the user picked it to onboard from.
      setSelectedIds(prev => {
        const next = new Set(prev);
        ingested.forEach(doc => next.add(doc.id));
        return next;
      });
    },
    [onUpload],
  );

  const selectedDocuments = useMemo(
    () => allDocuments.filter(doc => selectedIds.has(doc.id)),
    [allDocuments, selectedIds],
  );

  // A still-extracting selection has no text to ground the turn yet — block until
  // it settles (mirrors the Copilot Send gate).
  const hasPendingSelection = selectedDocuments.some(doc =>
    isPendingDocumentStatus(doc.status),
  );
  const selectedCount = selectedDocuments.length;
  const confirmDisabled =
    !canWrite || selectedCount === 0 || hasPendingSelection || isUploading;

  return (
    <Modal
      show={open}
      onHide={onCancel}
      title={t('Auto-onboard from a document')}
      data-test="auto-onboard-modal"
      footer={
        <Flex justify="space-between" align="center" style={{ width: '100%' }}>
          <Typography.Text type="secondary" data-test="auto-onboard-count">
            {hasPendingSelection
              ? t('Waiting for documents to finish processing…')
              : t('%s selected', selectedCount)}
          </Typography.Text>
          <Flex gap={theme.sizeUnit * 2}>
            <Button buttonStyle="secondary" onClick={onCancel}>
              {t('Cancel')}
            </Button>
            <Button
              buttonStyle="primary"
              disabled={confirmDisabled}
              onClick={() => onConfirm(selectedDocuments)}
              data-test="auto-onboard-confirm"
            >
              {t('Onboard with Copilot')}
            </Button>
          </Flex>
        </Flex>
      }
    >
      <Flex vertical gap={theme.sizeUnit * 2}>
        <Typography.Text type="secondary" data-test="auto-onboard-subtitle">
          {t(
            'Pick the business documents the Copilot should read. It will map the ' +
              'tables they describe to this database, onboard them, and propose a ' +
              'changeset for you to review.',
          )}
        </Typography.Text>
        <div>
          <input
            ref={fileInputRef}
            type="file"
            multiple
            accept={ACCEPT}
            onChange={handleUpload}
            style={{ display: 'none' }}
            data-test="auto-onboard-file-input"
          />
          <Button
            buttonStyle="tertiary"
            buttonSize="small"
            icon={<Icons.UploadOutlined iconSize="s" />}
            loading={isUploading}
            disabled={!canWrite}
            onClick={() => fileInputRef.current?.click()}
            data-test="auto-onboard-upload"
          >
            {t('Upload document')}
          </Button>
        </div>
        <div
          css={css`
            height: ${LIST_HEIGHT}px;
            overflow-y: auto;
            border: 1px solid ${theme.colorBorderSecondary};
            border-radius: ${theme.borderRadius}px;
            padding: ${theme.sizeUnit}px;
          `}
          data-test="auto-onboard-list"
        >
          {allDocuments.length === 0 ? (
            <Empty
              image={Empty.PRESENTED_IMAGE_SIMPLE}
              description={t('No documents yet — upload one to get started.')}
              data-test="auto-onboard-empty"
            />
          ) : (
            allDocuments.map(doc => (
              <Flex
                key={doc.id}
                align="center"
                gap={theme.sizeUnit * 2}
                css={css`
                  padding: ${theme.sizeUnit}px ${theme.sizeUnit}px;
                  border-radius: ${theme.borderRadius}px;
                  &:hover {
                    background-color: ${theme.colorBgTextHover};
                  }
                `}
                data-test="auto-onboard-row"
              >
                <Checkbox
                  checked={selectedIds.has(doc.id)}
                  onChange={() => toggle(doc.id)}
                  data-test="auto-onboard-checkbox"
                />
                <Typography.Text ellipsis style={{ flex: 1, minWidth: 0 }}>
                  {doc.filename}
                </Typography.Text>
                {isPendingDocumentStatus(doc.status) ||
                doc.status === 'error' ||
                doc.status === 'needs_ocr' ? (
                  <DocumentStatusTag status={doc.status} error={doc.error} />
                ) : null}
                <Typography.Text type="secondary">
                  {formatBytes(doc.size_bytes)}
                </Typography.Text>
              </Flex>
            ))
          )}
        </div>
      </Flex>
    </Modal>
  );
}
