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

const isYaml = (filename: string) => /\.(ya?ml)$/i.test(filename);

const isProcessing = (status: StagedStatus) =>
  status === 'uploading' || status === 'enriching';

const STATUS_LABELS: Record<StagedStatus, string> = {
  uploading: t('Uploading…'),
  enriching: t('Enriching…'),
  pending: t('Ready'),
  draft: t('Draft'),
  active: t('Active'),
  error: t('Error'),
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
  const theme = useTheme();

  const existingByPath = (path: string) =>
    existingFiles.find(file => file.path === path) || null;

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
        const supported = isYaml(file.name) || isMarkdown(file.name);
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
            : t('Unsupported file type. Drop a .yaml, .yml or .md file.'),
        };
      }),
    ]);
    try {
      for (const { file, id } of entries) {
        if (isYaml(file.name)) {
          // YAML is treated as a new/updated MDL file directly.
          // eslint-disable-next-line no-await-in-loop
          const text = await file.text();
          patchItem(id, {
            path: `models/${file.name.replace(/\.(ya?ml)$/i, '')}.yaml`,
            content: text,
            status: 'pending',
          });
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
            content: proposal.proposed_yaml,
            kind: 'enrichment',
            validation: proposal.validation,
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
      contentText: { fontFamily: theme.fontFamilyCode },
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
    activate: boolean,
    { refresh = true }: { refresh?: boolean } = {},
  ): Promise<boolean> => {
    if (!projectId) {
      return false;
    }
    const existing = existingByPath(item.path);
    try {
      let fileId: string;
      if (existing) {
        const updated = await updateMdlFile(projectId, existing.id, {
          content: item.content,
        });
        fileId = updated.id;
      } else {
        const created = await createMdlFile(projectId, {
          path: item.path,
          content: item.content,
          source_type:
            item.kind === 'enrichment' ? 'enriched_markdown' : 'uploaded_mdl',
        });
        fileId = created.id;
      }
      if (activate) {
        await updateMdlFile(projectId, fileId, { status: 'active' });
      }
      patchItem(item.id, {
        status: activate ? 'active' : 'draft',
        error: undefined,
      });
      if (refresh) {
        await onApplied();
      }
      return true;
    } catch (ex) {
      const message = ex instanceof Error ? ex.message : t('Save failed');
      patchItem(item.id, { status: 'error', error: message });
      return false;
    }
  };

  const persistAll = async (activate: boolean) => {
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
        const ok = await persistItem(item, activate, { refresh: false });
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
    onHide();
  };

  return (
    <Modal
      show={show}
      onHide={close}
      title={t('Add to semantic layer')}
      width="80vw"
      maxWidth="1100px"
      footer={
        <Flex justify="flex-end" gap="small">
          <Button
            buttonStyle="tertiary"
            disabled={!canWrite || isBusy || items.length === 0}
            onClick={() => persistAll(false)}
          >
            {t('Save all as draft')}
          </Button>
          <Button
            buttonStyle="primary"
            disabled={!canWrite || isBusy || items.length === 0}
            onClick={() => persistAll(true)}
          >
            {t('Activate all')}
          </Button>
        </Flex>
      }
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
          {t('Drop MDL YAML or Markdown files, or click to browse')}
        </Typography.Text>
        <Typography.Text type="secondary">
          {t('YAML is added as a new MDL file; Markdown is enriched.')}
        </Typography.Text>
      </DropZone>
      <HiddenInput
        ref={inputRef}
        type="file"
        multiple
        accept=".yaml,.yml,.md,.markdown,.txt"
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
                buttonStyle="tertiary"
                buttonSize="small"
                disabled={!canWrite || isBusy || !item.content}
                onClick={() => persistItem(item, false)}
              >
                {t('Save draft')}
              </Button>
              <Button
                buttonStyle="primary"
                buttonSize="small"
                disabled={!canWrite || isBusy || !item.content}
                onClick={() => persistItem(item, true)}
              >
                {t('Activate')}
              </Button>
            </Flex>
          </StagedItemRoot>
        ))}
      </StagedList>
    </Modal>
  );
}
