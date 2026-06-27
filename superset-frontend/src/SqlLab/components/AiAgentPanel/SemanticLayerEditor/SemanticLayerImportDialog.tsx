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
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import ReactDiffViewer from 'react-diff-viewer-continued';
import { t } from '@apache-superset/core/translation';
import {
  css,
  isThemeDark,
  styled,
  useTheme,
} from '@apache-superset/core/theme';
import { Alert } from '@apache-superset/core/components';
import { Button, Flex, Modal, Typography } from '@superset-ui/core/components';
import { Icons } from '@superset-ui/core/components/Icons';
import {
  createMdlFile,
  createProjectDocumentFromText,
  enrichProjectDocument,
  getAgentHealthCached,
  MdlFile,
  MdlValidationResult,
  updateMdlFile,
  uploadProjectSourceDocument,
} from '../api';
import { DocumentStatusTag, formatBytes } from './documentStatus';

type StagedKind = 'mdl' | 'enrichment' | 'document';
type StagedStatus =
  | 'uploading'
  | 'enriching'
  | 'pending'
  | 'saving'
  | 'draft'
  | 'active'
  | 'uploaded'
  | 'error';

interface StagedItem {
  id: string;
  filename: string;
  path: string;
  content: string;
  kind: StagedKind;
  validation: MdlValidationResult | null;
  status: StagedStatus;
  error?: string;
  warnings?: string[];
  /** For `kind: 'document'`, the persisted source document's lifecycle status. */
  documentStatus?: string;
}

const DropZone = styled.button`
  ${({ theme }) => css`
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: ${theme.sizeUnit}px;
    width: 100%;
    min-height: 120px;
    padding: ${theme.sizeUnit * 4}px;
    border: 2px dashed ${theme.colorBorder};
    border-radius: ${theme.borderRadius}px;
    background: ${theme.colorBgContainer};
    color: ${theme.colorTextSecondary};
    cursor: pointer;
  `}
`;

const StagedList = styled.div`
  ${({ theme }) => css`
    display: flex;
    flex-direction: column;
    gap: ${theme.sizeUnit * 3}px;
    margin-top: ${theme.sizeUnit * 3}px;
    max-height: 50vh;
    overflow: auto;
  `}
`;

const StagedItemRoot = styled.div`
  ${({ theme }) => css`
    border: 1px solid ${theme.colorBorderSecondary};
    border-radius: ${theme.borderRadius}px;
    padding: ${theme.sizeUnit * 3}px;
    display: flex;
    flex-direction: column;
    gap: ${theme.sizeUnit * 2}px;
  `}
`;

const HiddenInput = styled.input`
  display: none;
`;

const StatusRow = styled.span`
  ${({ theme }) => css`
    display: inline-flex;
    align-items: center;
    gap: ${theme.sizeUnit}px;
    color: ${theme.colorTextSecondary};
  `}
`;

const newId = () => `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;

const isMarkdown = (filename: string) => /\.(md|markdown|txt)$/i.test(filename);

const isJson = (filename: string) => /\.json$/i.test(filename);

// Binary / tabular source documents uploaded for the MDL Copilot to read and for
// the document viewer (not MDL files, not inline markdown enrichment). These are
// sent to the multipart source-document endpoint and land in the `raw/` folder.
const isSourceDocument = (filename: string) =>
  /\.(csv|html?|pdf|docx|xlsx|pptx)$/i.test(filename);

// Single source of truth for "can this dialog handle the file", shared by the
// file-picker `accept` filter, the drop handler, and the staging classifier so the
// two input paths (pick vs drop) stay consistent.
const isAcceptedFile = (filename: string) =>
  isJson(filename) || isMarkdown(filename) || isSourceDocument(filename);

const ACCEPT_EXTENSIONS =
  '.json,.md,.markdown,.txt,.csv,.html,.pdf,.docx,.xlsx,.pptx';

// Pre-upload size guard. The backend remains the source of truth (it rejects
// oversized uploads with HTTP 400); this constant is a UX hint so the user is not
// made to wait for a round-trip. It mirrors the WREN_MAX_DOCUMENT_BYTES default
// and is superseded by the server-reported limit once Phase 2 exposes it via
// /health (plan_document_upload_ux_gaps.md G2).
const DEFAULT_MAX_DOCUMENT_BYTES = 10_000_000;

const isProcessing = (status: StagedStatus) =>
  status === 'uploading' || status === 'enriching' || status === 'saving';

const STATUS_LABELS: Record<StagedStatus, string> = {
  uploading: t('Uploading…'),
  enriching: t('Enriching…'),
  pending: t('Ready'),
  saving: t('Saving…'),
  draft: t('Draft'),
  active: t('Active'),
  uploaded: t('Uploaded'),
  error: t('Error'),
};

const KIND_LABELS: Record<StagedKind, string> = {
  mdl: t('MDL'),
  enrichment: t('enriched'),
  document: t('document'),
};

// Assign a collision-free path for a genuinely new file: if `base` is taken,
// append the next free numeric suffix (`name_1.json`, `name_2.json`, …) before the
// extension. Used only for new JSON uploads; re-enrichment keeps its path (updates).
const uniqueMdlPath = (base: string, taken: Set<string>): string => {
  if (!taken.has(base)) {
    return base;
  }
  const slash = base.lastIndexOf('/');
  const dot = base.lastIndexOf('.');
  const hasExt = dot > slash;
  const stem = hasExt ? base.slice(0, dot) : base;
  const ext = hasExt ? base.slice(dot) : '';
  let suffix = 1;
  let candidate = `${stem}_${suffix}${ext}`;
  while (taken.has(candidate)) {
    suffix += 1;
    candidate = `${stem}_${suffix}${ext}`;
  }
  return candidate;
};

export interface SemanticLayerImportDialogProps {
  show: boolean;
  onHide: () => void;
  projectId: string | null;
  existingFiles: MdlFile[];
  canWrite: boolean;
  onApplied: () => Promise<void> | void;
}

export default function SemanticLayerImportDialog({
  show,
  onHide,
  projectId,
  existingFiles,
  canWrite,
  onApplied,
}: SemanticLayerImportDialogProps) {
  const [items, setItems] = useState<StagedItem[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [isBusy, setIsBusy] = useState(false);
  // Effective upload cap. Starts at the documented default and is replaced by the
  // server-reported limit (WREN_MAX_DOCUMENT_BYTES) so the client-side guard never
  // drifts from an operator-tuned backend (plan_document_upload_ux_gaps.md G2).
  const [maxDocumentBytes, setMaxDocumentBytes] = useState(
    DEFAULT_MAX_DOCUMENT_BYTES,
  );
  const inputRef = useRef<HTMLInputElement | null>(null);
  // Synchronous re-entry guard: a rapid double-click can fire two handlers before
  // React re-renders the disabled button, so block by id at call time.
  const savingIdsRef = useRef<Set<string>>(new Set());
  // path -> fileId for files created in this session, so a repeat save routes to an
  // update instead of a second create before the refreshed `existingFiles` prop
  // arrives (the source of the "MDL file already exists" race).
  const sessionFilesRef = useRef<Map<string, string>>(new Map());
  const theme = useTheme();

  // Pull the real cap when the dialog opens; cached so repeat opens don't refetch
  // (RG3). Best-effort — `getAgentHealthCached` returns null (never rejects) on
  // failure, so the guard keeps its default limit.
  useEffect(() => {
    if (!show) {
      return undefined;
    }
    let active = true;
    getAgentHealthCached().then(health => {
      if (active && typeof health?.max_document_bytes === 'number') {
        setMaxDocumentBytes(health.max_document_bytes);
      }
    });
    return () => {
      active = false;
    };
  }, [show]);

  const existingByPath = (path: string) =>
    existingFiles.find(file => file.path === path) || null;

  // Resolve the id of an already-persisted file at `path`, consulting both the
  // server-provided list and files created earlier in this session.
  const resolveExistingId = (path: string): string | null =>
    existingByPath(path)?.id ?? sessionFilesRef.current.get(path) ?? null;

  const patchItem = (id: string, patch: Partial<StagedItem>) =>
    setItems(current =>
      current.map(item => (item.id === id ? { ...item, ...patch } : item)),
    );

  const stageFiles = async (files: FileList | File[]) => {
    if (!projectId) {
      return;
    }
    setIsBusy(true);
    // Stage a placeholder for every dropped file up front so the user gets
    // immediate "Uploading…"/"Enriching…" feedback while each file is read and
    // (for Markdown) sent through the enrichment pipeline.
    const entries = Array.from(files).map(file => ({ file, id: newId() }));
    setItems(current => [
      ...current,
      ...entries.map(({ file, id }) => {
        const document = isSourceDocument(file.name);
        const typeSupported = isAcceptedFile(file.name);
        // Pre-upload size guard (G2): reject oversized files before the upload
        // round-trip; the backend still enforces the real cap.
        const oversized = file.size > maxDocumentBytes;
        const supported = typeSupported && !oversized;
        let kind: StagedKind = 'mdl';
        if (document) {
          kind = 'document';
        } else if (isMarkdown(file.name)) {
          kind = 'enrichment';
        }
        let error: string | undefined;
        if (oversized) {
          error = t('File is too large (%(size)s). The limit is %(max)s.', {
            size: formatBytes(file.size),
            max: formatBytes(maxDocumentBytes),
          });
        } else if (!typeSupported) {
          error = t(
            'Unsupported file type. Add JSON/Markdown, or a ' +
              'CSV, HTML, PDF, Word, Excel, or PowerPoint document.',
          );
        }
        return {
          id,
          filename: file.name,
          path: file.name,
          content: '',
          kind,
          validation: null,
          status: (supported ? 'uploading' : 'error') as StagedStatus,
          error,
        };
      }),
    ]);
    // Track paths already claimed (server-side files + items staged in this batch)
    // so a new JSON upload that collides gets a numeric suffix instead of clobbering
    // or 409-ing. Re-enrichment is excluded — it intentionally updates its own path.
    const takenPaths = new Set<string>([
      ...existingFiles.map(file => file.path),
      ...items.map(item => item.path),
    ]);
    let uploadedDocument = false;
    try {
      for (const { file, id } of entries) {
        // Skip files the placeholder already rejected (oversized / unsupported)
        // so the guard holds for both the picker and drop paths.
        if (file.size > maxDocumentBytes || !isAcceptedFile(file.name)) {
          continue; // eslint-disable-line no-continue
        }
        if (isSourceDocument(file.name)) {
          // Binary / tabular documents are uploaded as source documents for the
          // MDL Copilot and the viewer (multipart). They are persisted on upload
          // — there is no MDL content to review/apply — so they reach a terminal
          // staged state showing the extraction status (extracted / extracting /
          // needs_ocr / error).
          // eslint-disable-next-line no-await-in-loop
          const document = await uploadProjectSourceDocument(projectId, file);
          uploadedDocument = true;
          patchItem(id, {
            kind: 'document',
            path: document.filename,
            status: 'uploaded',
            documentStatus: document.status,
            error:
              document.status === 'error'
                ? (document.error ?? undefined)
                : undefined,
          });
        } else if (isJson(file.name)) {
          // JSON is treated as a new MDL file. Give a genuinely-new file a unique
          // path so it does not collide with an unrelated existing file.
          // eslint-disable-next-line no-await-in-loop
          const text = await file.text();
          const path = uniqueMdlPath(
            `models/${file.name.replace(/\.json$/i, '')}.json`,
            takenPaths,
          );
          takenPaths.add(path);
          patchItem(id, { path, content: text, status: 'pending' });
        } else if (isMarkdown(file.name)) {
          // Markdown goes through the enrichment pipeline.
          // eslint-disable-next-line no-await-in-loop
          const text = await file.text();
          // eslint-disable-next-line no-await-in-loop
          const document = await createProjectDocumentFromText(
            projectId,
            text,
            file.name,
          );
          patchItem(id, { status: 'enriching' });
          // eslint-disable-next-line no-await-in-loop
          const proposal = await enrichProjectDocument(projectId, document.id);
          patchItem(id, {
            path: proposal.proposed_path,
            content: proposal.proposed_content,
            kind: 'enrichment',
            validation: proposal.validation,
            warnings: proposal.warnings,
            status: 'pending',
          });
        }
      }
      if (uploadedDocument) {
        // Surface the new source document(s) in the workspace `raw/` folder
        // (and to the Copilot) right away. One refresh after the batch keeps the
        // project re-resolve cost down.
        await onApplied();
      }
    } catch (ex) {
      setError(ex instanceof Error ? ex.message : t('Unable to read files'));
    } finally {
      setIsBusy(false);
    }
  };

  // Theme the diff viewer so it matches the MDL editor (monospace font,
  // themed surfaces and text) instead of the library's hard-coded light theme.
  const diffStyles = useMemo(() => {
    const variables = {
      diffViewerBackground: theme.colorBgContainer,
      diffViewerColor: theme.colorText,
      addedBackground: theme.colorSuccessBg,
      addedColor: theme.colorText,
      removedBackground: theme.colorErrorBg,
      removedColor: theme.colorText,
      wordAddedBackground: theme.colorSuccessBgHover,
      wordRemovedBackground: theme.colorErrorBgHover,
      addedGutterBackground: theme.colorSuccessBg,
      removedGutterBackground: theme.colorErrorBg,
      gutterBackground: theme.colorBgLayout,
      gutterColor: theme.colorTextTertiary,
      addedGutterColor: theme.colorText,
      removedGutterColor: theme.colorText,
      codeFoldBackground: theme.colorBgLayout,
      codeFoldGutterBackground: theme.colorBgLayout,
      emptyLineBackground: theme.colorBgContainer,
      diffViewerTitleBackground: theme.colorBgLayout,
      diffViewerTitleColor: theme.colorText,
      diffViewerTitleBorderColor: theme.colorBorder,
    };
    return {
      variables: { dark: variables, light: variables },
      diffContainer: {
        borderRadius: `${theme.borderRadius}px`,
        border: `1px solid ${theme.colorBorder}`,
      },
      // Reset the user-agent <pre> margin so the fixed-height, overflow-hidden
      // title block does not clip the column headers ("Current"/"Proposed").
      titleBlock: { height: 'auto', overflow: 'visible' },
      contentText: { fontFamily: theme.fontFamilyCode, margin: 0 },
      gutter: { fontFamily: theme.fontFamilyCode },
      lineNumber: { fontFamily: theme.fontFamilyCode },
    };
  }, [theme]);

  const onDrop = (event: DragEvent<HTMLButtonElement>) => {
    event.preventDefault();
    const dropped = Array.from(event.dataTransfer?.files ?? []);
    if (!dropped.length) {
      return;
    }
    // Mirror the file picker's `accept` filter so drop and pick behave the same:
    // unsupported drops are rejected with a single message instead of becoming a
    // row of per-file errors.
    const accepted = dropped.filter(file => isAcceptedFile(file.name));
    const skipped = dropped.length - accepted.length;
    // Set once here (stageFiles no longer clears `error`) so the skip note
    // survives staging; clears any stale error when nothing was skipped.
    setError(
      skipped > 0
        ? t(
            'Skipped %(n)s unsupported file(s). Accepted: documents (CSV, HTML, ' +
              'PDF, Word, Excel, PowerPoint), MDL JSON, or Markdown.',
            { n: skipped },
          )
        : null,
    );
    if (accepted.length) {
      stageFiles(accepted);
    }
  };

  const onPick = (event: ChangeEvent<HTMLInputElement>) => {
    setError(null);
    if (event.target.files?.length) {
      stageFiles(event.target.files);
    }
    event.target.value = '';
  };

  const persistItem = async (
    item: StagedItem,
    { refresh = true }: { refresh?: boolean } = {},
  ): Promise<boolean> => {
    if (!projectId) {
      return false;
    }
    // Block a concurrent submit of the same item (the repeat-press race).
    if (savingIdsRef.current.has(item.id)) {
      return false;
    }
    savingIdsRef.current.add(item.id);
    patchItem(item.id, { status: 'saving', error: undefined });
    const existingId = resolveExistingId(item.path);
    try {
      if (existingId) {
        // Same logical file (re-enrichment / repeat save) -> update in place.
        await updateMdlFile(projectId, existingId, { content: item.content });
      } else {
        const created = await createMdlFile(projectId, {
          path: item.path,
          content: item.content,
          source_type:
            item.kind === 'enrichment' ? 'enriched_markdown' : 'uploaded_mdl',
        });
        // Remember it so an immediate re-save updates instead of re-creating,
        // even before the refreshed `existingFiles` prop arrives.
        sessionFilesRef.current.set(item.path, created.id);
      }
      patchItem(item.id, { status: 'draft', error: undefined });
      if (refresh) {
        await onApplied();
      }
      return true;
    } catch (ex) {
      const message = ex instanceof Error ? ex.message : t('Save failed');
      patchItem(item.id, { status: 'error', error: message });
      return false;
    } finally {
      savingIdsRef.current.delete(item.id);
    }
  };

  const persistAll = async () => {
    if (isBusy) {
      return;
    }
    setIsBusy(true);
    let allSucceeded = true;
    try {
      const pending = items.filter(
        item => item.status === 'pending' || item.status === 'draft',
      );
      for (const item of pending) {
        // Defer the browser refresh until every file is persisted: refreshing
        // per file re-resolves the project (a backend database lookup) once per
        // file, which is the source of the GET /api/v1/database burst.
        // eslint-disable-next-line no-await-in-loop
        const ok = await persistItem(item, { refresh: false });
        allSucceeded = allSucceeded && ok;
      }
      // Single refresh so the main MDL browser reflects every new/updated file.
      await onApplied();
    } finally {
      setIsBusy(false);
    }
    // Dismiss the dialog once everything has been applied cleanly.
    if (allSucceeded) {
      close();
    }
  };

  const close = () => {
    setItems([]);
    setError(null);
    savingIdsRef.current.clear();
    sessionFilesRef.current.clear();
    onHide();
  };

  // Whether anything in the batch still needs an explicit apply. Uploaded source
  // documents are already persisted (terminal), so a documents-only batch has
  // nothing to "Save" — the primary action becomes a plain "Done" (G4). Markdown
  // enrichment is deprecated; surface its notice only when one is staged (G1a).
  const hasApplyable = items.some(
    item => item.status === 'pending' || item.status === 'draft',
  );
  const hasEnrichment = items.some(item => item.kind === 'enrichment');

  return (
    <Modal
      show={show}
      onHide={close}
      title={t('Upload documents & MDL')}
      width="80vw"
      maxWidth="1100px"
      footer={[
        // An array footer (not a function-component element) so the Modal does not
        // inject a `closeModal` prop that antd would forward onto a DOM node.
        <Button
          key="save-all"
          buttonStyle="primary"
          loading={isBusy}
          disabled={!canWrite || isBusy || items.length === 0}
          onClick={() => (hasApplyable ? persistAll() : close())}
        >
          {hasApplyable ? t('Save all') : t('Done')}
        </Button>,
      ]}
    >
      {error && <Alert type="error" message={error} />}
      {hasEnrichment && (
        <Alert
          type="info"
          showIcon
          data-test="enrichment-deprecation-notice"
          message={t('Enriching from a document? Use the MDL Copilot.')}
          description={t(
            'Markdown enrichment here is deprecated. The MDL Copilot reads your ' +
              'uploaded documents, proposes reviewable edits, and preserves ' +
              'governance metadata. Upload documents (below) for the Copilot to ' +
              'read, or JSON for raw MDL files.',
          )}
        />
      )}
      <DropZone
        type="button"
        data-test="semantic-import-dropzone"
        onClick={() => inputRef.current?.click()}
        onDragOver={(event: DragEvent<HTMLButtonElement>) =>
          event.preventDefault()
        }
        onDrop={onDrop}
        disabled={!canWrite || isBusy}
      >
        <Icons.UploadOutlined iconSize="l" />
        <Typography.Text strong>
          {t(
            'Drop documents (PDF, Word, Excel, PowerPoint, CSV, HTML), MDL ' +
              'JSON, or Markdown — or click to browse',
          )}
        </Typography.Text>
        <Typography.Text type="secondary">
          {t(
            'Documents are uploaded for the Copilot and viewer; JSON is added as ' +
              'a new MDL file; Markdown enrichment is deprecated.',
          )}
        </Typography.Text>
      </DropZone>
      <HiddenInput
        ref={inputRef}
        type="file"
        multiple
        accept={ACCEPT_EXTENSIONS}
        onChange={onPick}
      />
      <StagedList>
        {items.map(item => (
          <StagedItemRoot key={item.id} data-test="semantic-import-item">
            <Flex justify="space-between" align="center">
              <Typography.Text strong>
                {item.path}{' '}
                <Typography.Text type="secondary">
                  ({KIND_LABELS[item.kind]})
                </Typography.Text>
              </Typography.Text>
              <StatusRow data-test="semantic-import-item-status">
                {isProcessing(item.status) && (
                  <Icons.LoadingOutlined iconSize="m" spin />
                )}
                {item.kind === 'document' && item.documentStatus ? (
                  <DocumentStatusTag
                    status={item.documentStatus}
                    error={item.error}
                  />
                ) : (
                  <Typography.Text type="secondary">
                    {STATUS_LABELS[item.status]}
                  </Typography.Text>
                )}
              </StatusRow>
            </Flex>
            {item.error && <Alert type="error" message={item.error} />}
            {item.warnings && item.warnings.length > 0 && (
              <Alert
                type="info"
                data-test="semantic-import-warnings"
                message={item.warnings.join('\n')}
              />
            )}
            {item.validation && !item.validation.valid && (
              <Alert
                type="warning"
                message={item.validation.messages
                  .map(message => message.message)
                  .join('\n')}
              />
            )}
            {item.content && (
              <div data-test="semantic-import-diff">
                <ReactDiffViewer
                  oldValue={existingByPath(item.path)?.content ?? ''}
                  newValue={item.content}
                  splitView
                  useDarkTheme={isThemeDark(theme)}
                  styles={diffStyles}
                  leftTitle={t('Current')}
                  rightTitle={t('Proposed')}
                />
              </div>
            )}
            {/* Source documents are persisted on upload — there is no MDL
                content to review or apply — so they show no per-item Save. */}
            {item.kind !== 'document' && (
              <Flex gap="small" justify="flex-end">
                <Button
                  buttonStyle="primary"
                  buttonSize="small"
                  loading={item.status === 'saving'}
                  disabled={
                    !canWrite ||
                    isBusy ||
                    !item.content ||
                    isProcessing(item.status)
                  }
                  onClick={() => persistItem(item)}
                >
                  {t('Save')}
                </Button>
              </Flex>
            )}
          </StagedItemRoot>
        ))}
      </StagedList>
    </Modal>
  );
}
