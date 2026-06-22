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
import { ChangeEvent, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { t } from '@apache-superset/core/translation';
import { css, styled } from '@apache-superset/core/theme';
import { Alert } from '@apache-superset/core/components';
import { Button, Flex, Input, Typography } from '@superset-ui/core/components';
import { Icons } from '@superset-ui/core/components/Icons';
import { EditorHost } from 'src/core/editors';
import {
  ConversationScope,
  createMdlFile,
  deleteMdlFile,
  enrichProjectDocument,
  getProjectSemanticLayerState,
  listMdlFiles,
  listSemanticDocuments,
  materializeSemanticProject,
  MdlEnrichmentProposal,
  MdlFile,
  MdlFileStatus,
  resolveSemanticProject,
  SemanticDocument,
  SemanticLayerState,
  SemanticProject,
  updateMdlFile,
  uploadMdlFile,
  uploadProjectSourceDocument,
  WrenMaterializationResult,
} from '../api';
import SemanticLayerStateBadge from '../SemanticLayerStateBadge';

const EditorRoot = styled.div`
  ${({ theme }) => css`
    display: flex;
    flex-direction: column;
    height: 100%;
    min-height: 0;
    background: ${theme.colorBgBase};
  `}
`;

const EditorHeader = styled.div`
  ${({ theme }) => css`
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: ${theme.sizeUnit * 2}px;
    padding: ${theme.sizeUnit * 3}px;
    border-bottom: 1px solid ${theme.colorBorderSecondary};
  `}
`;

const EditorBody = styled.div`
  ${({ theme }) => css`
    display: grid;
    flex: 1;
    min-height: 0;
    grid-template-columns: minmax(220px, 280px) minmax(0, 1fr);
    gap: ${theme.sizeUnit * 3}px;
    padding: ${theme.sizeUnit * 3}px;
    overflow: hidden;
  `}
`;

const BrowserPane = styled.div`
  ${({ theme }) => css`
    display: flex;
    min-height: 0;
    flex-direction: column;
    gap: ${theme.sizeUnit * 2}px;
    border-right: 1px solid ${theme.colorBorderSecondary};
    padding-right: ${theme.sizeUnit * 3}px;
  `}
`;

const EditorPane = styled.div`
  ${({ theme }) => css`
    display: flex;
    min-width: 0;
    min-height: 0;
    flex-direction: column;
    gap: ${theme.sizeUnit * 2}px;
  `}
`;

const ScrollList = styled.div`
  ${({ theme }) => css`
    display: flex;
    min-height: 0;
    flex: 1;
    flex-direction: column;
    gap: ${theme.sizeUnit}px;
    overflow: auto;
  `}
`;

const FileButton = styled.button<{ 'data-active': boolean }>`
  ${({ theme, 'data-active': active }) => css`
    display: flex;
    width: 100%;
    min-height: 36px;
    align-items: center;
    justify-content: space-between;
    gap: ${theme.sizeUnit}px;
    padding: ${theme.sizeUnit * 2}px;
    border: 1px solid
      ${active ? theme.colorPrimary : theme.colorBorderSecondary};
    border-radius: ${theme.borderRadius}px;
    background: ${active ? theme.colorPrimaryBg : theme.colorBgContainer};
    color: ${theme.colorText};
    cursor: pointer;
    text-align: left;
  `}
`;

const HiddenFileInput = styled.input`
  display: none;
`;

const StyledEditorHost = styled(EditorHost)`
  &.ace_editor {
    border: 1px solid ${({ theme }) => theme.colorBorder};
    border-radius: ${({ theme }) => theme.borderRadius}px;
  }
`;

const SourceDocumentItem = styled.div`
  ${({ theme }) => css`
    display: flex;
    flex-direction: column;
    gap: ${theme.sizeUnit}px;
    padding: ${theme.sizeUnit * 2}px 0;
    border-top: 1px solid ${theme.colorBorderSecondary};
  `}
`;

const defaultYaml = `models:
  - name: new_model
    description: ''
`;

export interface SemanticLayerEditorProps {
  databaseId: number;
  catalogName: string | null;
  schemaName: string;
}

export default function SemanticLayerEditor({
  databaseId,
  catalogName,
  schemaName,
}: SemanticLayerEditorProps) {
  const scope: ConversationScope = useMemo(
    () => ({
      database_id: databaseId,
      catalog_name: catalogName,
      schema_name: schemaName,
      dataset_ids: [],
    }),
    [databaseId, catalogName, schemaName],
  );

  const [project, setProject] = useState<SemanticProject | null>(null);
  const [mdlFiles, setMdlFiles] = useState<MdlFile[]>([]);
  const [documents, setDocuments] = useState<SemanticDocument[]>([]);
  const [state, setState] = useState<SemanticLayerState | null>(null);
  const [activeFileId, setActiveFileId] = useState<string | null>(null);
  const [editorPath, setEditorPath] = useState('models/new_model.yaml');
  const [editorValue, setEditorValue] = useState(defaultYaml);
  const [proposal, setProposal] = useState<MdlEnrichmentProposal | null>(null);
  const [materialization, setMaterialization] =
    useState<WrenMaterializationResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const mdlInputRef = useRef<HTMLInputElement | null>(null);
  const sourceInputRef = useRef<HTMLInputElement | null>(null);

  const activeFile = mdlFiles.find(file => file.id === activeFileId) || null;
  const canWrite =
    project?.permission === 'write' || project?.permission === 'admin';

  const refresh = useCallback(async () => {
    if (!scope.schema_name) {
      setProject(null);
      setMdlFiles([]);
      setDocuments([]);
      setState(null);
      return;
    }
    const nextProject = await resolveSemanticProject({
      database_id: scope.database_id,
      catalog_name: scope.catalog_name ?? null,
      schema_name: scope.schema_name,
      create_if_missing: true,
    });
    const [nextFiles, nextDocuments, nextState] = await Promise.all([
      listMdlFiles(nextProject.id),
      listSemanticDocuments(scope),
      getProjectSemanticLayerState(nextProject.id),
    ]);
    setProject(nextProject);
    setMdlFiles(nextFiles);
    setDocuments(nextDocuments);
    setState(nextState);
    const selectedFile =
      nextFiles.find(file => file.id === activeFileId) || nextFiles[0] || null;
    if (selectedFile) {
      setActiveFileId(selectedFile.id);
      setEditorPath(selectedFile.path);
      setEditorValue(selectedFile.content);
    }
  }, [activeFileId, scope]);

  useEffect(() => {
    refresh().catch(ex => {
      setError(
        ex instanceof Error ? ex.message : t('Unable to load semantic layer'),
      );
    });
  }, [refresh]);

  const withLoading = async (action: () => Promise<void>, fallback: string) => {
    setIsLoading(true);
    setError(null);
    try {
      await action();
    } catch (ex) {
      setError(ex instanceof Error ? ex.message : fallback);
    } finally {
      setIsLoading(false);
    }
  };

  const selectFile = (file: MdlFile) => {
    setActiveFileId(file.id);
    setEditorPath(file.path);
    setEditorValue(file.content);
    setProposal(null);
  };

  const saveFile = (status?: MdlFileStatus) =>
    withLoading(async () => {
      if (!project) {
        return;
      }
      const payload = {
        path: editorPath,
        content: editorValue,
        status: status ?? activeFile?.status ?? 'draft',
      };
      let savedFile = activeFile
        ? await updateMdlFile(project.id, activeFile.id, payload)
        : await createMdlFile(project.id, {
            path: editorPath,
            content: editorValue,
            source_type: 'manual',
          });
      if (!activeFile && status) {
        savedFile = await updateMdlFile(project.id, savedFile.id, { status });
      }
      setActiveFileId(savedFile.id);
      setEditorPath(savedFile.path);
      setEditorValue(savedFile.content);
      await refresh();
    }, t('Unable to save MDL file'));

  const deleteFile = (file: MdlFile) =>
    withLoading(async () => {
      if (!project) {
        return;
      }
      await deleteMdlFile(project.id, file.id);
      setActiveFileId(null);
      setEditorPath('models/new_model.yaml');
      setEditorValue(defaultYaml);
      await refresh();
    }, t('Unable to delete MDL file'));

  const uploadMdl = (event: ChangeEvent<HTMLInputElement>) =>
    withLoading(async () => {
      const file = event.target.files?.[0];
      if (!file || !project) {
        return;
      }
      const uploaded = await uploadMdlFile(project.id, file);
      setActiveFileId(uploaded.id);
      setEditorPath(uploaded.path);
      setEditorValue(uploaded.content);
      event.target.value = '';
      await refresh();
    }, t('MDL upload failed'));

  const uploadSourceDocument = (event: ChangeEvent<HTMLInputElement>) =>
    withLoading(async () => {
      const file = event.target.files?.[0];
      if (!file || !project) {
        return;
      }
      const document = await uploadProjectSourceDocument(project.id, file);
      if (file.name.toLowerCase().endsWith('.md')) {
        const nextProposal = await enrichProjectDocument(
          project.id,
          document.id,
        );
        setProposal(nextProposal);
        setEditorPath(nextProposal.proposed_path);
        setEditorValue(nextProposal.proposed_yaml);
        setActiveFileId(null);
      }
      event.target.value = '';
      await refresh();
    }, t('Source document upload failed'));

  const materializeProject = () =>
    withLoading(async () => {
      if (!project) {
        return;
      }
      setMaterialization(await materializeSemanticProject(project.id));
      await refresh();
    }, t('Unable to materialize Wren project'));

  return (
    <EditorRoot data-test="semantic-layer-editor">
      <EditorHeader>
        <Flex vertical gap={0}>
          <Typography.Title level={5} style={{ margin: 0 }}>
            {project?.name || t('Semantic layer')}
          </Typography.Title>
          <SemanticLayerStateBadge state={state} />
        </Flex>
      </EditorHeader>
      <EditorBody>
        <BrowserPane>
          {error && <Alert type="warning" message={error} />}
          {!scope.schema_name && (
            <Alert type="warning" message={t('Select a database and schema.')} />
          )}
          <Flex gap="small" wrap="wrap">
            <Button
              buttonStyle="primary"
              disabled={!project || !canWrite || isLoading}
              onClick={() => saveFile()}
              icon={<Icons.SaveOutlined iconSize="m" />}
            >
              {t('Save')}
            </Button>
            <Button
              buttonStyle="tertiary"
              disabled={!project || !canWrite || isLoading}
              onClick={() => {
                setActiveFileId(null);
                setEditorPath('models/new_model.yaml');
                setEditorValue(defaultYaml);
                setProposal(null);
              }}
              icon={<Icons.PlusOutlined iconSize="m" />}
            >
              {t('New')}
            </Button>
          </Flex>
          <ScrollList>
            {mdlFiles.map(file => (
              <FileButton
                key={file.id}
                type="button"
                data-active={file.id === activeFileId}
                onClick={() => selectFile(file)}
              >
                <span>{file.path}</span>
                <Typography.Text type="secondary">{file.status}</Typography.Text>
              </FileButton>
            ))}
          </ScrollList>
          <Flex gap="small" wrap="wrap">
            <Button
              buttonStyle="tertiary"
              disabled={!project || !canWrite || isLoading}
              onClick={() => mdlInputRef.current?.click()}
              icon={<Icons.UploadOutlined iconSize="m" />}
            >
              {t('MDL')}
            </Button>
            <Button
              buttonStyle="tertiary"
              disabled={!project || !canWrite || isLoading}
              onClick={() => sourceInputRef.current?.click()}
              icon={<Icons.FileTextOutlined iconSize="m" />}
            >
              {t('Document')}
            </Button>
            <Button
              buttonStyle="tertiary"
              disabled={!project || isLoading}
              onClick={materializeProject}
              icon={<Icons.SyncOutlined iconSize="m" />}
            >
              {t('Materialize')}
            </Button>
          </Flex>
          <HiddenFileInput
            ref={mdlInputRef}
            type="file"
            accept=".yaml,.yml,text/yaml,application/x-yaml"
            onChange={uploadMdl}
          />
          <HiddenFileInput
            ref={sourceInputRef}
            type="file"
            accept=".md,.txt,text/markdown,text/plain"
            onChange={uploadSourceDocument}
          />
          {documents.map(document => (
            <SourceDocumentItem key={document.id}>
              <Typography.Text strong>{document.filename}</Typography.Text>
              <Typography.Text type="secondary">
                {document.status}
              </Typography.Text>
              {document.summary && (
                <Typography.Paragraph>{document.summary}</Typography.Paragraph>
              )}
              <Button
                buttonStyle="tertiary"
                buttonSize="small"
                disabled={!project || !canWrite || isLoading}
                onClick={() =>
                  withLoading(async () => {
                    if (!project) {
                      return;
                    }
                    const nextProposal = await enrichProjectDocument(
                      project.id,
                      document.id,
                    );
                    setProposal(nextProposal);
                    setActiveFileId(null);
                    setEditorPath(nextProposal.proposed_path);
                    setEditorValue(nextProposal.proposed_yaml);
                  }, t('Unable to enrich document'))
                }
                icon={<Icons.FunctionOutlined iconSize="m" />}
              >
                {t('Enrich')}
              </Button>
            </SourceDocumentItem>
          ))}
        </BrowserPane>
        <EditorPane>
          {proposal && (
            <Alert
              type="info"
              message={t('Review proposed MDL before saving.')}
              description={proposal.warnings.join(' ')}
            />
          )}
          {materialization && (
            <Alert
              type="success"
              message={t('Wren project materialized')}
              description={t('%s active file(s)', materialization.file_count)}
            />
          )}
          <Input
            value={editorPath}
            disabled={!canWrite || isLoading}
            onChange={(event: ChangeEvent<HTMLInputElement>) =>
              setEditorPath(event.target.value)
            }
          />
          <StyledEditorHost
            id={`semantic-mdl-${project?.id || 'empty'}`}
            height="100%"
            language="yaml"
            onChange={setEditorValue}
            readOnly={!canWrite || isLoading}
            value={editorValue}
            width="100%"
          />
          {activeFile?.validation && !activeFile.validation.valid && (
            <Alert
              type="warning"
              message={activeFile.validation.messages
                .map(message => message.message)
                .join('\n')}
            />
          )}
          <Flex justify="space-between" gap="small" wrap="wrap">
            <Flex gap="small" wrap="wrap">
              <Button
                buttonStyle="primary"
                disabled={!project || !canWrite || isLoading}
                onClick={() => saveFile()}
                icon={<Icons.SaveOutlined iconSize="m" />}
              >
                {t('Save draft')}
              </Button>
              <Button
                buttonStyle="tertiary"
                disabled={!project || !canWrite || isLoading}
                onClick={() => saveFile('active')}
                icon={<Icons.CheckCircleOutlined iconSize="m" />}
              >
                {t('Activate')}
              </Button>
            </Flex>
            <Button
              buttonStyle="danger"
              disabled={!activeFile || !project || !canWrite || isLoading}
              onClick={() => activeFile && deleteFile(activeFile)}
              icon={<Icons.DeleteOutlined iconSize="m" />}
            >
              {t('Delete')}
            </Button>
          </Flex>
        </EditorPane>
      </EditorBody>
    </EditorRoot>
  );
}
