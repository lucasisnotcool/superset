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
  lazy,
  Suspense,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import { useDispatch } from 'react-redux';
import { t } from '@apache-superset/core/translation';
import type { editors } from '@apache-superset/core';
import { css, styled } from '@apache-superset/core/theme';
import { Alert } from '@apache-superset/core/components';
import { useJsonValidation } from '@superset-ui/core/components/AsyncAceEditor';
import {
  Button,
  ConfirmModal,
  Flex,
  Input,
  Switch,
  Tabs,
  Tag,
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
  runReset,
  SemanticLayerState,
  SemanticProject,
  updateMdlFile,
  validateMdlFile,
} from '../api';
import SemanticLayerStateBadge from '../SemanticLayerStateBadge';
import SemanticLayerImportDialog from './SemanticLayerImportDialog';
import InstructionsPanel from './InstructionsPanel';
import CopilotPanel from './CopilotPanel';
import WorkspaceTree, { treeFromFiles } from './WorkspaceTree';

// Lazy-loaded so the graph code + ECharts land in a separate async chunk fetched
// only when the Graph tab is opened — zero cost otherwise (wren_graph_view.md D1).
const SchemaGraph = lazy(() => import('./SchemaGraph/SchemaGraph'));

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

const ContentTabs = styled(Tabs)`
  ${({ theme }) => css`
    display: flex;
    flex: 1;
    min-height: 0;
    flex-direction: column;

    .ant-tabs-nav {
      margin: 0 ${theme.sizeUnit * 3}px;
    }

    .ant-tabs-content-holder {
      display: flex;
      min-height: 0;
      flex: 1;
    }

    .ant-tabs-content {
      height: 100%;
    }

    // Only the active pane flexes to full height; scoping to -active leaves
    // antd's .ant-tabs-tabpane-hidden { display: none } unopposed so the
    // inactive pane stays hidden instead of showing through.
    .ant-tabs-tabpane-active {
      display: flex;
      min-height: 0;
      height: 100%;
      flex-direction: column;
    }
  `}
`;

const EditorBody = styled.div<{ $copilot?: boolean }>`
  ${({ theme, $copilot }) => css`
    display: grid;
    flex: 1;
    min-height: 0;
    grid-template-columns:
      minmax(220px, 280px) minmax(0, 1fr)
      ${$copilot ? 'minmax(320px, 400px)' : ''};
    gap: ${theme.sizeUnit * 3}px;
    padding: ${theme.sizeUnit * 3}px;
    overflow: hidden;
  `}
`;

const CopilotRail = styled.div`
  ${({ theme }) => css`
    display: flex;
    min-height: 0;
    min-width: 0;
    flex-direction: column;
    border-left: 1px solid ${theme.colorBorderSecondary};
    padding-left: ${theme.sizeUnit * 3}px;
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
  const [showResetConfirm, setShowResetConfirm] = useState(false);
  const [isResetting, setIsResetting] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [showCopilot, setShowCopilot] = useState(true);
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
  const [isValidating, setIsValidating] = useState(false);
  // Unsaved-changes tracking: compare the editor buffer to the loaded file
  // (or the new-file template when nothing is selected).
  const isDirty = activeFile
    ? editorValue !== activeFile.content
    : editorValue.trim() !== defaultMdl.trim();

  // Inline gutter diagnostics: live JSON-syntax errors from the buffer, plus the
  // stored file's structural/physical/engine validation messages (which carry
  // 1-based line/column) when the buffer matches the saved file.
  const jsonAnnotations = useJsonValidation(editorValue, {
    errorPrefix: t('Invalid MDL JSON'),
  });
  const editorAnnotations = useMemo<editors.EditorAnnotation[]>(() => {
    const fromJson = jsonAnnotations.map(annotation => ({
      severity: annotation.type as editors.EditorAnnotation['severity'],
      line: annotation.row,
      column: annotation.column,
      message: annotation.text,
    }));
    const fromValidation =
      !isDirty && activeFile?.validation
        ? activeFile.validation.messages
            .filter(message => typeof message.line === 'number')
            .map(message => ({
              severity:
                message.severity as editors.EditorAnnotation['severity'],
              line: Math.max(0, (message.line ?? 1) - 1),
              column: Math.max(0, (message.column ?? 1) - 1),
              message: message.message,
            }))
        : [];
    return [...fromJson, ...fromValidation];
  }, [jsonAnnotations, isDirty, activeFile]);

  // The file browser renders a folder tree built client-side from the project's
  // MDL files (path prefixes → folders), so it works regardless of the copilot
  // flag. The backend GET /workspace returns the same shape for other consumers.
  const workspaceRoot = useMemo(() => treeFromFiles(mdlFiles), [mdlFiles]);

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

  // On-demand validation of the *stored* file (re-runs structural + physical +
  // engine checks and persists the result for the editor's annotations). A dirty
  // buffer should be saved first — surfaced via the unsaved indicator.
  const validateActiveFile = async () => {
    if (!project || !activeFile) {
      return;
    }
    setIsValidating(true);
    try {
      await validateMdlFile(project.id, activeFile.id);
      await refresh();
    } catch (ex) {
      dispatch(
        addDangerToast(
          ex instanceof Error ? ex.message : t('Validation failed.'),
        ),
      );
    } finally {
      setIsValidating(false);
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

  // Destructive "start over": delete all MDL and re-onboard (auto-activated) from
  // the live schema. Gated behind a confirmation dialog. Documents are kept.
  const resetProject = useCallback(async () => {
    if (!project) {
      return;
    }
    setShowResetConfirm(false);
    setIsResetting(true);
    try {
      const job = await runReset(project.id);
      if (job.status === 'failed') {
        throw new Error(job.error || t('Reset failed'));
      }
      const warnings = job.result?.warnings ?? [];
      if (warnings.length > 0) {
        dispatch(
          addWarningToast(
            t('Reset completed with warnings: %s', warnings.join('; ')),
          ),
        );
      } else {
        dispatch(addSuccessToast(t('Semantic layer reset and re-onboarded.')));
      }
      await refresh();
    } catch (ex) {
      dispatch(
        addDangerToast(
          ex instanceof Error
            ? ex.message
            : t('Unable to reset semantic layer'),
        ),
      );
    } finally {
      setIsResetting(false);
    }
  }, [project, refresh, dispatch]);

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
        <Button
          buttonStyle={showCopilot ? 'primary' : 'tertiary'}
          buttonSize="small"
          icon={<Icons.CommentOutlined iconSize="m" />}
          onClick={() => setShowCopilot(value => !value)}
          data-test="toggle-copilot"
        >
          {t('Copilot')}
        </Button>
      </EditorHeader>
      <ContentTabs
        defaultActiveKey="models"
        items={[
          {
            key: 'models',
            label: t('Models'),
            children: (
              <EditorBody $copilot={showCopilot && !!project}>
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
                    <WorkspaceTree
                      root={workspaceRoot}
                      activeFileId={activeFileId}
                      onSelectFile={fileId => {
                        const file = mdlFiles.find(item => item.id === fileId);
                        if (file) {
                          selectFile(file);
                        }
                      }}
                      renderActions={node => {
                        const file = mdlFiles.find(
                          item => item.id === node.file_id,
                        );
                        if (!file) {
                          return null;
                        }
                        return (
                          <Tooltip title={STATUS_TOGGLE_HELP}>
                            <Switch
                              size="small"
                              checked={file.status === 'active'}
                              disabled={!canWrite || isLoading}
                              checkedChildren={t('Active')}
                              unCheckedChildren={t('Draft')}
                              onChange={checked =>
                                toggleFileStatus(file, checked)
                              }
                            />
                          </Tooltip>
                        );
                      }}
                    />
                  </ScrollList>
                  <Button
                    block
                    buttonStyle="tertiary"
                    disabled={
                      !project ||
                      !canWrite ||
                      isLoading ||
                      mdlFiles.length === 0
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
                      loading={isOnboarding || isResetting}
                      disabled={
                        !project ||
                        !canWrite ||
                        isLoading ||
                        isOnboarding ||
                        isResetting
                      }
                      onClick={() => setShowResetConfirm(true)}
                      icon={<Icons.ReloadOutlined iconSize="m" />}
                    >
                      {isOnboarding || isResetting
                        ? t('Resetting…')
                        : t('Reset')}
                    </Button>
                  </Flex>
                </BrowserPane>
                <EditorPane>
                  <Flex align="center" gap="small">
                    <Input
                      value={editorPath}
                      disabled={!canWrite || isLoading}
                      onChange={(event: ChangeEvent<HTMLInputElement>) =>
                        setEditorPath(event.target.value)
                      }
                    />
                    {isDirty && (
                      <Tooltip title={t('You have unsaved changes')}>
                        <Tag color="warning" data-test="mdl-dirty-indicator">
                          {t('Unsaved')}
                        </Tag>
                      </Tooltip>
                    )}
                  </Flex>
                  <StyledEditorHost
                    id={`semantic-mdl-${project?.id || 'empty'}`}
                    height="100%"
                    language="json"
                    onChange={setEditorValue}
                    readOnly={!canWrite || isLoading}
                    value={editorValue}
                    width="100%"
                    annotations={editorAnnotations}
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
                      <Button
                        buttonStyle="tertiary"
                        disabled={!activeFile || isLoading || isValidating}
                        loading={isValidating}
                        onClick={validateActiveFile}
                        icon={<Icons.CheckCircleOutlined iconSize="m" />}
                        data-test="mdl-validate"
                      >
                        {t('Validate')}
                      </Button>
                    </Flex>
                    <Button
                      buttonStyle="danger"
                      disabled={
                        !activeFile || !project || !canWrite || isLoading
                      }
                      onClick={() => activeFile && deleteFile(activeFile)}
                      icon={<Icons.DeleteOutlined iconSize="m" />}
                    >
                      {t('Delete')}
                    </Button>
                  </Flex>
                </EditorPane>
                {showCopilot && project ? (
                  <CopilotRail data-test="copilot-rail">
                    <CopilotPanel
                      projectId={project.id}
                      canWrite={canWrite}
                      onApplied={refresh}
                    />
                  </CopilotRail>
                ) : null}
              </EditorBody>
            ),
          },
          {
            key: 'instructions',
            label: t('Instructions'),
            children: <InstructionsPanel scope={scope} canWrite={canWrite} />,
          },
          {
            key: 'graph',
            label: t('Graph'),
            children: (
              <Suspense fallback={null}>
                <SchemaGraph
                  mdlFiles={mdlFiles}
                  databaseId={databaseId}
                  catalogName={catalogName}
                  schemaName={schemaName}
                />
              </Suspense>
            ),
          },
        ]}
      />
      <SemanticLayerImportDialog
        show={showImportDialog}
        onHide={() => setShowImportDialog(false)}
        projectId={project?.id ?? null}
        existingFiles={mdlFiles}
        canWrite={canWrite}
        onApplied={refresh}
      />
      <ConfirmModal
        show={showResetConfirm}
        onHide={() => setShowResetConfirm(false)}
        onConfirm={resetProject}
        loading={isResetting}
        confirmText={t('Reset')}
        confirmButtonStyle="danger"
        title={t('Reset semantic layer?')}
        body={t(
          'This deletes every model in this project — including document ' +
            'enrichments and any hand-edited files — and rebuilds the base models ' +
            'from the current schema (auto-activated). Uploaded documents are kept, ' +
            'so you can re-enrich afterward. This cannot be undone.',
        )}
      />
    </EditorRoot>
  );
}
