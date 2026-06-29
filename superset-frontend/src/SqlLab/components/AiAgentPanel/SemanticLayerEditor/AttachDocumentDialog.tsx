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
  DragEvent,
  KeyboardEvent,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import { t } from '@apache-superset/core/translation';
import { css, useTheme, SupersetTheme } from '@apache-superset/core/theme';
import { Alert } from '@apache-superset/core/components';
import {
  Button,
  Checkbox,
  Empty,
  Flex,
  Modal,
  Skeleton,
  Typography,
} from '@superset-ui/core/components';
import { Icons } from '@superset-ui/core/components/Icons';
import { listProjectDocuments, SemanticDocument } from '../api';
import useDocumentIngestion from '../useDocumentIngestion';
import {
  DocumentStatusTag,
  formatBytes,
  isPendingDocumentStatus,
} from './documentStatus';

/**
 * Accepted document types. Kept in sync with the backend extractor set
 * (`wren_allowed_document_types`) and used in two places that must agree: the
 * picker's `accept` attribute (filters the OS file browser) and the drop handler
 * (which the browser does NOT filter), so drag-drop and click-to-browse reject the
 * same files — see `isAcceptedFile`.
 */
export const DOCUMENT_ACCEPT =
  '.json,.md,.markdown,.txt,.csv,.html,.pdf,.docx,.xlsx,.pptx';

const ACCEPTED_EXTENSIONS = [
  'json',
  'md',
  'markdown',
  'txt',
  'csv',
  'html',
  'pdf',
  'docx',
  'xlsx',
  'pptx',
];

/**
 * Best-effort client-side size guard. The backend remains authoritative
 * (`wren_max_document_bytes`, default 10 MB); this only spares the user a failed
 * round-trip on an obviously-oversized file and matches the backend default. A
 * file that slips past this is still rejected server-side with a clear 400.
 */
export const DEFAULT_MAX_DOCUMENT_BYTES = 10_000_000;

const extensionOf = (name: string): string => {
  const match = name.toLowerCase().match(/\.([^.]+)$/);
  return match ? match[1] : '';
};

const isAcceptedFile = (file: File): boolean =>
  ACCEPTED_EXTENSIONS.includes(extensionOf(file.name));

const LIST_HEIGHT = 260;

const dropzoneStyles = (
  theme: SupersetTheme,
  isDragging: boolean,
  enabled: boolean,
) => css`
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: ${theme.sizeUnit}px;
  padding: ${theme.sizeUnit * 6}px ${theme.sizeUnit * 4}px;
  text-align: center;
  border: 1px dashed ${isDragging ? theme.colorPrimary : theme.colorBorder};
  border-radius: ${theme.borderRadius}px;
  background: ${isDragging ? theme.colorPrimaryBg : theme.colorBgLayout};
  cursor: ${enabled ? 'pointer' : 'not-allowed'};
  opacity: ${enabled ? 1 : 0.6};
  transition:
    border-color 0.2s ease,
    background 0.2s ease;
  &:hover {
    border-color: ${enabled ? theme.colorPrimary : theme.colorBorder};
  }
  &:focus-visible {
    outline: 2px solid ${theme.colorPrimary};
    outline-offset: 2px;
  }
`;

export interface AttachDocumentDialogProps {
  open: boolean;
  projectId: string;
  /** Documents already staged on the turn; pre-checked when the dialog opens. */
  attachedDocs: SemanticDocument[];
  /** Gates uploading and selection — the same write permission as the composer. */
  canWrite: boolean;
  /**
   * Commit the chosen documents as the turn's attachment set. The dialog is seeded
   * from the current `attachedDocs`, so the chosen set is authoritative
   * (deselecting removes; this replaces, not merges).
   */
  onConfirm: (docs: SemanticDocument[]) => void;
  onClose: () => void;
  /** Fired after an upload adds a new document, so the editor tree can refresh. */
  onDocumentsChanged?: () => void;
}

/**
 * Dialog for attaching documents to the Copilot chat turn. Offers two ingress
 * points over one selection:
 *  - upload new files (click-to-browse or drag-drop), which run through the shared
 *    ingestion pipeline (upload + dedup + vectorize) and are auto-selected;
 *  - pick from the project's existing `raw/` documents.
 * Selecting an already-extracted document grounds the turn from its extracted text
 * with no re-upload (the list payload carries the full text).
 */
const AttachDocumentDialog = ({
  open,
  projectId,
  attachedDocs,
  canWrite,
  onConfirm,
  onClose,
  onDocumentsChanged,
}: AttachDocumentDialogProps) => {
  const theme = useTheme();
  const { ingest, isIngesting } = useDocumentIngestion(projectId);
  // The working document set shown in the picker: the project's existing docs,
  // unioned with anything uploaded in this session and the seeded attachments.
  const [docs, setDocs] = useState<SemanticDocument[]>([]);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  // Files rejected by the client-side type/size pre-check (drop/pick parity).
  const [skipped, setSkipped] = useState<string[]>([]);
  const [isDragging, setIsDragging] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const loadDocuments = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      const list = await listProjectDocuments(projectId);
      // The fetched list is authoritative/fresh; keep any seeded attachment or
      // session upload the list has not caught up to yet.
      setDocs(prev => {
        const seen = new Set(list.map(doc => doc.id));
        return [...list, ...prev.filter(doc => !seen.has(doc.id))];
      });
    } catch (caught) {
      setLoadError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  // On open: seed the selection + working set from what is already attached, then
  // load the project's documents. Re-runs only when the dialog (re)opens or the
  // project changes — not on every `attachedDocs` identity change.
  useEffect(() => {
    if (!open) return;
    setSkipped([]);
    setIsDragging(false);
    setSelectedIds(new Set(attachedDocs.map(doc => doc.id)));
    setDocs(attachedDocs);
    loadDocuments();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, projectId]);

  const handleFiles = useCallback(
    async (files: File[]) => {
      if (!canWrite || !files.length) return;
      const accepted: File[] = [];
      const rejected: string[] = [];
      files.forEach(file => {
        if (!isAcceptedFile(file)) {
          rejected.push(t('%s — unsupported type', file.name));
        } else if (file.size > DEFAULT_MAX_DOCUMENT_BYTES) {
          rejected.push(
            t(
              '%s — exceeds the %s limit',
              file.name,
              formatBytes(DEFAULT_MAX_DOCUMENT_BYTES),
            ),
          );
        } else {
          accepted.push(file);
        }
      });
      setSkipped(rejected);
      if (!accepted.length) return;
      // The shared hook persists + dedups + vectorizes and dispatches its own
      // success/reuse/error toasts; here we only fold the results into the picker.
      const results = await ingest(accepted);
      if (!results.length) return;
      const uploaded = results.map(result => result.document);
      setDocs(prev => {
        const seen = new Set(uploaded.map(doc => doc.id));
        return [...uploaded, ...prev.filter(doc => !seen.has(doc.id))];
      });
      setSelectedIds(prev => {
        const next = new Set(prev);
        uploaded.forEach(doc => next.add(doc.id));
        return next;
      });
      // The new documents now live in the workspace; let the editor refresh its
      // tree so they appear there too.
      onDocumentsChanged?.();
    },
    [canWrite, ingest, onDocumentsChanged],
  );

  const openPicker = useCallback(() => {
    if (canWrite) fileInputRef.current?.click();
  }, [canWrite]);

  const onInputChange = useCallback(
    (event: ChangeEvent<HTMLInputElement>) => {
      const files = Array.from(event.target.files ?? []);
      // Reset so selecting the same file again re-fires change.
      event.target.value = '';
      handleFiles(files);
    },
    [handleFiles],
  );

  const onDrop = useCallback(
    (event: DragEvent<HTMLDivElement>) => {
      event.preventDefault();
      setIsDragging(false);
      handleFiles(Array.from(event.dataTransfer?.files ?? []));
    },
    [handleFiles],
  );

  const onDragOver = useCallback(
    (event: DragEvent<HTMLDivElement>) => {
      event.preventDefault();
      if (canWrite) setIsDragging(true);
    },
    [canWrite],
  );

  const onDropzoneKeyDown = useCallback(
    (event: KeyboardEvent<HTMLDivElement>) => {
      if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault();
        openPicker();
      }
    },
    [openPicker],
  );

  const toggle = useCallback((id: string) => {
    setSelectedIds(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  const selectedDocs = useMemo(
    () => docs.filter(doc => selectedIds.has(doc.id)),
    [docs, selectedIds],
  );

  const handleConfirm = useCallback(() => {
    onConfirm(selectedDocs);
    onClose();
  }, [onConfirm, onClose, selectedDocs]);

  const primaryButtonName =
    selectedIds.size > 0 ? t('Attach (%s)', selectedIds.size) : t('Attach');

  return (
    <Modal
      show={open}
      onHide={onClose}
      title={t('Attach documents')}
      width="640px"
      primaryButtonName={primaryButtonName}
      onHandledPrimaryAction={handleConfirm}
      disablePrimaryButton={!canWrite || isIngesting}
      data-test="copilot-attach-dialog"
      destroyOnHidden
    >
      <Flex vertical gap={theme.sizeUnit * 3}>
        {/* The file input is a SIBLING of the dropzone (not a child): a click on
            it from `openPicker` must not bubble back into the dropzone's onClick
            and re-trigger the picker. */}
        <input
          ref={fileInputRef}
          type="file"
          multiple
          accept={DOCUMENT_ACCEPT}
          disabled={!canWrite}
          css={css`
            display: none;
          `}
          onChange={onInputChange}
          data-test="copilot-attach-input"
        />
        <div
          role="button"
          tabIndex={canWrite ? 0 : -1}
          aria-label={t(
            'Upload a document — click to browse or drop files here',
          )}
          aria-disabled={!canWrite}
          onClick={openPicker}
          onKeyDown={onDropzoneKeyDown}
          onDragOver={onDragOver}
          onDragLeave={() => setIsDragging(false)}
          onDrop={onDrop}
          data-test="copilot-attach-dropzone"
          css={dropzoneStyles(theme, isDragging, canWrite)}
        >
          <Icons.UploadOutlined
            iconSize="xl"
            iconColor={theme.colorTextTertiary}
          />
          <Typography.Text strong>
            {isDragging
              ? t('Drop files to upload')
              : t('Click to browse or drag files here')}
          </Typography.Text>
          <Typography.Text type="secondary">
            {t(
              'PDF, Word, Excel, PowerPoint, CSV, HTML, Markdown, or JSON · up to %s',
              formatBytes(DEFAULT_MAX_DOCUMENT_BYTES),
            )}
          </Typography.Text>
          {isIngesting ? (
            <Typography.Text
              type="secondary"
              data-test="copilot-attach-uploading"
            >
              {t('Uploading…')}
            </Typography.Text>
          ) : null}
        </div>

        {skipped.length ? (
          <Alert
            type="warning"
            showIcon
            closable
            onClose={() => setSkipped([])}
            message={t('Skipped %s file(s)', skipped.length)}
            description={skipped.join('; ')}
            data-test="copilot-attach-skipped"
          />
        ) : null}

        <Flex vertical gap={theme.sizeUnit}>
          <Typography.Text strong>
            {t('Documents in this project')}
          </Typography.Text>
          {loading ? (
            <Skeleton active paragraph={{ rows: 3 }} title={false} />
          ) : loadError ? (
            <Alert
              type="error"
              showIcon
              message={t('Could not load documents')}
              description={loadError}
              data-test="copilot-attach-load-error"
              action={
                <Button
                  buttonSize="small"
                  onClick={loadDocuments}
                  data-test="copilot-attach-retry"
                >
                  {t('Retry')}
                </Button>
              }
            />
          ) : docs.length === 0 ? (
            <Empty
              image={Empty.PRESENTED_IMAGE_SIMPLE}
              description={t('No documents yet — upload one above.')}
              data-test="copilot-attach-empty"
            />
          ) : (
            <div
              data-test="copilot-attach-doc-list"
              css={css`
                height: ${LIST_HEIGHT}px;
                overflow-y: auto;
                border: 1px solid ${theme.colorBorderSecondary};
                border-radius: ${theme.borderRadius}px;
                padding: ${theme.sizeUnit}px;
              `}
            >
              {docs.map(doc => (
                <Checkbox
                  key={doc.id}
                  checked={selectedIds.has(doc.id)}
                  onChange={() => toggle(doc.id)}
                  disabled={!canWrite}
                  data-test={`attach-doc-${doc.id}`}
                  css={css`
                    display: flex;
                    width: 100%;
                    margin: 0;
                    padding: ${theme.sizeUnit}px;
                    border-radius: ${theme.borderRadius}px;
                    &:hover {
                      background-color: ${theme.colorBgTextHover};
                    }
                    /* Stretch the label so the filename row fills the width. */
                    & > span:last-of-type {
                      flex: 1;
                      min-width: 0;
                    }
                  `}
                >
                  <Flex align="center" gap={theme.sizeUnit * 2}>
                    <Icons.FileOutlined iconColor={theme.colorTextTertiary} />
                    <Typography.Text
                      ellipsis
                      css={css`
                        flex: 1;
                        min-width: 0;
                      `}
                    >
                      {doc.filename}
                    </Typography.Text>
                    {isPendingDocumentStatus(doc.status) ||
                    doc.status === 'error' ||
                    doc.status === 'needs_ocr' ? (
                      <DocumentStatusTag
                        status={doc.status}
                        error={doc.error}
                      />
                    ) : null}
                    <Typography.Text type="secondary">
                      {formatBytes(doc.size_bytes)}
                    </Typography.Text>
                  </Flex>
                </Checkbox>
              ))}
            </div>
          )}
        </Flex>
      </Flex>
    </Modal>
  );
};

export default AttachDocumentDialog;
