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
import { ChangeEvent, DragEvent, useMemo, useRef, useState } from 'react';
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
  MdlFile,
  MdlValidationResult,
  updateMdlFile,
} from '../api';

type StagedKind = 'mdl' | 'enrichment';
type StagedStatus =
  | 'uploading'
  | 'enriching'
  | 'pending'
  | 'saving'
  | 'draft'
  | 'active'
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

const isProcessing = (status: StagedStatus) =>
  status === 'uploading' || status === 'enriching' || status === 'saving';

const STATUS_LABELS: Record<StagedStatus, string> = {
  uploading: t('Uploading…'),
  enriching: t('Enriching…'),
  pending: t('Ready'),
  saving: t('Saving…'),
  draft: t('Draft'),
  active: t('Active'),
  error: t('Error'),
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
  const inputRef = useRef<HTMLInputElement | null>(null);
  // Synchronous re-entry guard: a rapid double-click can fire two handlers before
  // React re-renders the disabled button, so block by id at call time.
  const savingIdsRef = useRef<Set<string>>(new Set());
  // path -> fileId for files created in this session, so a repeat save routes to an
  // update instead of a second create before the refreshed `existingFiles` prop
  // arrives (the source of the "MDL file already exists" race).
  const sessionFilesRef = useRef<Map<string, string>>(new Map());
  const theme = useTheme();

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
    setError(null);
    setIsBusy(true);
    // Stage a placeholder for every dropped file up front so the user gets
    // immediate "Uploading…"/"Enriching…" feedback while each file is read and
    // (for Markdown) sent through the enrichment pipeline.
    const entries = Array.from(files).map(file => ({ file, id: newId() }));
    setItems(current => [
      ...current,
      ...entries.map(({ file, id }) => {
        const supported = isJson(file.name) || isMarkdown(file.name);
        return {
          id,
          filename: file.name,
          path: file.name,
          content: '',
          kind: (isMarkdown(file.name) ? 'enrichment' : 'mdl') as StagedKind,
          validation: null,
          status: (supported ? 'uploading' : 'error') as StagedStatus,
          error: supported
            ? undefined
            : t('Unsupported file type. Drop a .json or .md file.'),
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
    try {
      for (const { file, id } of entries) {
        if (isJson(file.name)) {
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
    if (event.dataTransfer?.files?.length) {
      stageFiles(event.dataTransfer.files);
    }
  };

  const onPick = (event: ChangeEvent<HTMLInputElement>) => {
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

  return (
    <Modal
      show={show}
      onHide={close}
      title={t('Add to semantic layer')}
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
          onClick={() => persistAll()}
        >
          {t('Save all')}
        </Button>,
      ]}
    >
      {error && <Alert type="error" message={error} />}
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
          {t('Drop MDL JSON or Markdown files, or click to browse')}
        </Typography.Text>
        <Typography.Text type="secondary">
          {t('JSON is added as a new MDL file; Markdown is enriched.')}
        </Typography.Text>
      </DropZone>
      <HiddenInput
        ref={inputRef}
        type="file"
        multiple
        accept=".json,.md,.markdown,.txt"
        onChange={onPick}
      />
      <StagedList>
        {items.map(item => (
          <StagedItemRoot key={item.id} data-test="semantic-import-item">
            <Flex justify="space-between" align="center">
              <Typography.Text strong>
                {item.path}{' '}
                <Typography.Text type="secondary">
                  ({item.kind === 'enrichment' ? t('enriched') : t('MDL')})
                </Typography.Text>
              </Typography.Text>
              <StatusRow data-test="semantic-import-item-status">
                {isProcessing(item.status) && (
                  <Icons.LoadingOutlined iconSize="m" spin />
                )}
                <Typography.Text type="secondary">
                  {STATUS_LABELS[item.status]}
                </Typography.Text>
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
          </StagedItemRoot>
        ))}
      </StagedList>
    </Modal>
  );
}
