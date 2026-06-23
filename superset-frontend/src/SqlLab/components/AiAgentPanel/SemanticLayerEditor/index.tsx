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
import { useDispatch } from 'react-redux';
import { t } from '@apache-superset/core/translation';
import { css, styled } from '@apache-superset/core/theme';
import { Alert } from '@apache-superset/core/components';
import {
  Button,
  Flex,
  Input,
  Switch,
  Tooltip,
  Typography,
} from '@superset-ui/core/components';
import { Icons } from '@superset-ui/core/components/Icons';
import {
  addDangerToast,
  addSuccessToast,
  addWarningToast,
} from 'src/components/MessageToasts/actions';
import { EditorHost } from 'src/core/editors';
import {
  ConversationScope,
  createMdlFile,
  deleteMdlFile,
  getProjectSemanticLayerState,
  listMdlFiles,
  MdlFile,
  MdlFileStatus,
  resolveSemanticProject,
  runOnboarding,
  SemanticLayerState,
  SemanticProject,
  updateMdlFile,
} from '../api';
import SemanticLayerStateBadge from '../SemanticLayerStateBadge';
import SemanticLayerImportDialog from './SemanticLayerImportDialog';

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

const FileButton = styled.div<{ 'data-active': boolean }>`
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

    &:focus-visible {
      outline: 2px solid ${theme.colorPrimaryBorder};
      outline-offset: -1px;
    }
  `}
`;

const FilePath = styled.span`
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
`;

// The status toggle keeps a constant width across every row: antd sizes the
// Switch to the wider of its two labels, and the cell never flexes, so the file
// name (which does flex) is what absorbs the remaining space.
const ToggleCell = styled.div`
  flex: 0 0 auto;
  display: flex;
  justify-content: flex-end;
`;

const STATUS_TOGGLE_HELP = t(
  'Toggle on to activate this MDL file so it is included in the semantic ' +
    'layer; toggle off to keep it as a draft.',
);

const StyledEditorHost = styled(EditorHost)`
  &.ace_editor {
    border: 1px solid ${({ theme }) => theme.colorBorder};
    border-radius: ${({ theme }) => theme.borderRadius}px;
  }
`;

const defaultMdl = `{
  "models": [
    {
      "name": "new_model",
      "description": "",
      "tableReference": { "schema": "", "table": "" },
      "columns": []
    }
  ]
}
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
  const dispatch = useDispatch();
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
  const [state, setState] = useState<SemanticLayerState | null>(null);
  const [activeFileId, setActiveFileId] = useState<string | null>(null);
  const [editorPath, setEditorPath] = useState('models/new_model.json');
  const [editorValue, setEditorValue] = useState(defaultMdl);
  const [showImportDialog, setShowImportDialog] = useState(false);
  const [isOnboarding, setIsOnboarding] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const onboardedProjectsRef = useRef<Set<string>>(new Set());
  // Mirror the active file id in a ref so `refresh` can read the current
  // selection without depending on it (which would re-create the callback and
  // re-trigger the load effect — the cause of the autoscroll + dataset GET
  // storm). Synced on every render so it always holds the latest value.
  const activeFileIdRef = useRef<string | null>(null);
  activeFileIdRef.current = activeFileId;

  const activeFile = mdlFiles.find(file => file.id === activeFileId) || null;
  const canWrite =
    project?.permission === 'write' || project?.permission === 'admin';

  const refresh = useCallback(async () => {
    if (!scope.schema_name) {
      setProject(null);
      setMdlFiles([]);
      setState(null);
      return;
    }
    const nextProject = await resolveSemanticProject({
      database_id: scope.database_id,
      catalog_name: scope.catalog_name ?? null,
      schema_name: scope.schema_name,
      create_if_missing: true,
    });
    const [nextFiles, nextState] = await Promise.all([
      listMdlFiles(nextProject.id),
      getProjectSemanticLayerState(nextProject.id),
    ]);
    setProject(nextProject);
    setMdlFiles(nextFiles);
    setState(nextState);
    // Initialize the selection only when nothing valid is selected yet; never
    // override the user's current file/edits on a background refresh.
    const current = activeFileIdRef.current;
    const stillSelected =
      current && nextFiles.some(file => file.id === current);
    if (!stillSelected) {
      const firstFile = nextFiles[0] || null;
      activeFileIdRef.current = firstFile?.id ?? null;
      setActiveFileId(firstFile?.id ?? null);
      if (firstFile) {
        setEditorPath(firstFile.path);
        setEditorValue(firstFile.content);
      }
    }
  }, [scope]);

  useEffect(() => {
    refresh().catch(ex => {
      dispatch(
        addDangerToast(
          ex instanceof Error ? ex.message : t('Unable to load semantic layer'),
        ),
      );
    });
  }, [refresh, dispatch]);

  const withLoading = async (action: () => Promise<void>, fallback: string) => {
    setIsLoading(true);
    try {
      await action();
    } catch (ex) {
      dispatch(addDangerToast(ex instanceof Error ? ex.message : fallback));
    } finally {
      setIsLoading(false);
    }
  };

  const selectFile = (file: MdlFile) => {
    activeFileIdRef.current = file.id;
    setActiveFileId(file.id);
    setEditorPath(file.path);
    setEditorValue(file.content);
  };

  const startNewFile = () => {
    activeFileIdRef.current = null;
    setActiveFileId(null);
    setEditorPath('models/new_model.json');
    setEditorValue(defaultMdl);
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
      activeFileIdRef.current = savedFile.id;
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
      startNewFile();
      await refresh();
    }, t('Unable to delete MDL file'));

  const runOnboard = useCallback(
    async (targetProjectId: string) => {
      setIsOnboarding(true);
      try {
        const job = await runOnboarding(targetProjectId);
        if (job.status === 'failed') {
          throw new Error(job.error || t('Onboarding failed'));
        }
        const warnings = job.result?.warnings ?? [];
        if (warnings.length > 0) {
          dispatch(
            addWarningToast(
              t('Onboarding completed with warnings: %s', warnings.join('; ')),
            ),
          );
        } else {
          dispatch(
            addSuccessToast(t('Schema onboarded into the semantic layer.')),
          );
        }
        await refresh();
      } catch (ex) {
        dispatch(
          addDangerToast(
            ex instanceof Error ? ex.message : t('Unable to onboard schema'),
          ),
        );
      } finally {
        setIsOnboarding(false);
      }
    },
    [refresh, dispatch],
  );

  const onboardProject = () => {
    if (project) {
      runOnboard(project.id);
    }
  };

  // Toggle a single file between active (in the semantic layer) and draft.
  const toggleFileStatus = (file: MdlFile, activate: boolean) =>
    withLoading(async () => {
      if (!project) {
        return;
      }
      await updateMdlFile(project.id, file.id, {
        status: activate ? 'active' : 'draft',
      });
      await refresh();
    }, t('Unable to update MDL file'));

  const allActive =
    mdlFiles.length > 0 && mdlFiles.every(file => file.status === 'active');

  // Activate (or deactivate) every MDL file in the library in one pass, then
  // refresh once so the browser reflects the new statuses.
  const setAllStatuses = (activate: boolean) =>
    withLoading(async () => {
      if (!project) {
        return;
      }
      const nextStatus: MdlFileStatus = activate ? 'active' : 'draft';
      await Promise.all(
        mdlFiles
          .filter(file => file.status !== nextStatus)
          .map(file =>
            updateMdlFile(project.id, file.id, { status: nextStatus }),
          ),
      );
      await refresh();
    }, t('Unable to update MDL files'));

  // Eagerly onboard a schema that has no MDL yet so the user lands on a
  // populated semantic layer. Fires at most once per project.
  useEffect(() => {
    if (
      project &&
      canWrite &&
      mdlFiles.length === 0 &&
      !isOnboarding &&
      !onboardedProjectsRef.current.has(project.id)
    ) {
      onboardedProjectsRef.current.add(project.id);
      runOnboard(project.id);
    }
  }, [project, canWrite, mdlFiles.length, isOnboarding, runOnboard]);

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
          {!scope.schema_name && (
            <Alert
              type="warning"
              message={t('Select a database and schema.')}
            />
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
              onClick={startNewFile}
              icon={<Icons.PlusOutlined iconSize="m" />}
            >
              {t('New')}
            </Button>
          </Flex>
          <ScrollList>
            {mdlFiles.map(file => (
              <FileButton
                key={file.id}
                role="button"
                tabIndex={0}
                data-active={file.id === activeFileId}
                onClick={() => selectFile(file)}
                onKeyDown={event => {
                  if (event.key === 'Enter' || event.key === ' ') {
                    event.preventDefault();
                    selectFile(file);
                  }
                }}
              >
                <FilePath>{file.path}</FilePath>
                <ToggleCell
                  // Keep clicks/keys on the toggle from selecting the row.
                  onClick={event => event.stopPropagation()}
                  onKeyDown={event => event.stopPropagation()}
                  role="presentation"
                >
                  <Tooltip title={STATUS_TOGGLE_HELP}>
                    <Switch
                      size="small"
                      checked={file.status === 'active'}
                      disabled={!canWrite || isLoading}
                      checkedChildren={t('Active')}
                      unCheckedChildren={t('Draft')}
                      onChange={checked => toggleFileStatus(file, checked)}
                    />
                  </Tooltip>
                </ToggleCell>
              </FileButton>
            ))}
          </ScrollList>
          <Button
            block
            buttonStyle="tertiary"
            disabled={
              !project || !canWrite || isLoading || mdlFiles.length === 0
            }
            onClick={() => setAllStatuses(!allActive)}
            icon={
              allActive ? (
                <Icons.MinusCircleOutlined iconSize="m" />
              ) : (
                <Icons.CheckCircleOutlined iconSize="m" />
              )
            }
          >
            {allActive ? t('Deactivate all') : t('Activate all')}
          </Button>
          <Flex gap="small" wrap="wrap">
            <Button
              buttonStyle="tertiary"
              disabled={!project || !canWrite || isLoading}
              onClick={() => setShowImportDialog(true)}
              icon={<Icons.UploadOutlined iconSize="m" />}
            >
              {t('Add…')}
            </Button>
            <Button
              buttonStyle="tertiary"
              loading={isOnboarding}
              disabled={!project || !canWrite || isLoading || isOnboarding}
              onClick={onboardProject}
              icon={<Icons.DatabaseOutlined iconSize="m" />}
            >
              {isOnboarding ? t('Onboarding…') : t('Onboard')}
            </Button>
          </Flex>
        </BrowserPane>
        <EditorPane>
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
            language="json"
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
      <SemanticLayerImportDialog
        show={showImportDialog}
        onHide={() => setShowImportDialog(false)}
        projectId={project?.id ?? null}
        existingFiles={mdlFiles}
        canWrite={canWrite}
        onApplied={refresh}
      />
    </EditorRoot>
  );
}
