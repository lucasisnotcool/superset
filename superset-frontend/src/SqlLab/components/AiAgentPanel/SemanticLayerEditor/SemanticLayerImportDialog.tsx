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
import { ChangeEvent, DragEvent, useRef, useState } from 'react';
import ReactDiffViewer from 'react-diff-viewer-continued';
import { t } from '@apache-superset/core/translation';
import { css, styled } from '@apache-superset/core/theme';
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
type StagedStatus = 'pending' | 'draft' | 'active' | 'error';

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

const newId = () => `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;

const isMarkdown = (filename: string) => /\.(md|markdown|txt)$/i.test(filename);

const isYaml = (filename: string) => /\.(ya?ml)$/i.test(filename);

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

  const existingByPath = (path: string) =>
    existingFiles.find(file => file.path === path) || null;

  const stageFiles = async (files: FileList | File[]) => {
    if (!projectId) {
      return;
    }
    setError(null);
    setIsBusy(true);
    try {
      const staged: StagedItem[] = [];
      for (const file of Array.from(files)) {
        // eslint-disable-next-line no-await-in-loop
        const text = await file.text();
        if (isYaml(file.name)) {
          // YAML is treated as a new/updated MDL file directly.
          staged.push({
            id: newId(),
            filename: file.name,
            path: `models/${file.name.replace(/\.(ya?ml)$/i, '')}.yaml`,
            content: text,
            kind: 'mdl',
            validation: null,
            status: 'pending',
          });
        } else if (isMarkdown(file.name)) {
          // Markdown goes through the enrichment pipeline.
          // eslint-disable-next-line no-await-in-loop
          const document = await createProjectDocumentFromText(
            projectId,
            text,
            file.name,
          );
          // eslint-disable-next-line no-await-in-loop
          const proposal = await enrichProjectDocument(projectId, document.id);
          staged.push({
            id: newId(),
            filename: file.name,
            path: proposal.proposed_path,
            content: proposal.proposed_yaml,
            kind: 'enrichment',
            validation: proposal.validation,
            status: 'pending',
          });
        } else {
          staged.push({
            id: newId(),
            filename: file.name,
            path: file.name,
            content: '',
            kind: 'mdl',
            validation: null,
            status: 'error',
            error: t('Unsupported file type. Drop a .yaml, .yml or .md file.'),
          });
        }
      }
      setItems(current => [...current, ...staged]);
    } catch (ex) {
      setError(ex instanceof Error ? ex.message : t('Unable to read files'));
    } finally {
      setIsBusy(false);
    }
  };

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

  const persistItem = async (item: StagedItem, activate: boolean) => {
    if (!projectId) {
      return;
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
      setItems(current =>
        current.map(staged =>
          staged.id === item.id
            ? {
                ...staged,
                status: activate ? 'active' : 'draft',
                error: undefined,
              }
            : staged,
        ),
      );
      await onApplied();
    } catch (ex) {
      const message = ex instanceof Error ? ex.message : t('Save failed');
      setItems(current =>
        current.map(staged =>
          staged.id === item.id
            ? { ...staged, status: 'error', error: message }
            : staged,
        ),
      );
    }
  };

  const persistAll = async (activate: boolean) => {
    setIsBusy(true);
    try {
      const pending = items.filter(
        item => item.status === 'pending' || item.status === 'draft',
      );
      for (const item of pending) {
        // eslint-disable-next-line no-await-in-loop
        await persistItem(item, activate);
      }
    } finally {
      setIsBusy(false);
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
              <Typography.Text type="secondary">{item.status}</Typography.Text>
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
