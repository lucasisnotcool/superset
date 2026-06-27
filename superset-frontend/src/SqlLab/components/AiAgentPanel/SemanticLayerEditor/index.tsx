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
import { Splitter } from 'src/components/Splitter';
import {
  ConversationScope,
  createMdlFile,
  deleteMdlFile,
  getProjectReadiness,
  getProjectSemanticLayerState,
  getSemanticJob,
  listMdlFiles,
  listSemanticDocuments,
  MdlFile,
  MdlFileStatus,
  onboardSemanticProject,
  OnboardingSelection,
  resolveSemanticProject,
  runReset,
  SemanticDocument,
  SemanticJob,
  SemanticLayerState,
  SemanticProject,
  SemanticProjectReadinessStatus,
  updateMdlFile,
  validateMdlFile,
} from '../api';
import SemanticLayerStateBadge from '../SemanticLayerStateBadge';
import SemanticLayerImportDialog from './SemanticLayerImportDialog';
import InstructionsPanel from './InstructionsPanel';
import CopilotPanel from './CopilotPanel';
import OnboardingTablePicker from './OnboardingTablePicker';
import MdlProvenanceDialog from './MdlProvenanceDialog';
import DocumentDetailPane from './DocumentDetailPane';
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

// The three panes live in an antd Splitter: the file browser (left) and Copilot
// (right) are collapsible and width-adjustable like SqlLab's database browser and
// AI panel; the Splitter gutters provide the separators (so no pane borders).
//
// antd assigns every panel `flex-grow: 0` + a JS-measured `flex-basis`, so the
// center panel only grows when antd's ResizeObserver recomputes — which is
// unreliable when nested inside Tabs (antd #51106). Forcing the center panel to
// `flex: 1 1 0` makes it fill freed space natively (like the old CSS grid's
// `1fr`) whenever a side panel collapses or the outer SqlLab panels collapse,
// independent of antd's recompute. The side panels keep their measured basis, so
// they stay collapsible and width-adjustable.
const EditorSplitter = styled(Splitter)`
  flex: 1;
  min-height: 0;

  .ant-splitter-panel.semantic-editor-center-panel {
    flex: 1 1 0% !important;
    min-width: 0;
  }
`;

const CopilotRail = styled.div`
  ${({ theme }) => css`
    display: flex;
    height: 100%;
    min-height: 0;
    min-width: 0;
    flex-direction: column;
    padding: ${theme.sizeUnit * 3}px;
  `}
`;

const BrowserPane = styled.div`
  ${({ theme }) => css`
    display: flex;
    height: 100%;
    min-height: 0;
    flex-direction: column;
    gap: ${theme.sizeUnit * 2}px;
    padding: ${theme.sizeUnit * 3}px;
  `}
`;

const EditorPane = styled.div`
  ${({ theme }) => css`
    display: flex;
    height: 100%;
    min-width: 0;
    min-height: 0;
    flex-direction: column;
    gap: ${theme.sizeUnit * 2}px;
    padding: ${theme.sizeUnit * 3}px;
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

// Onboarding runs asynchronously on the agent (threaded backend): the start call
// returns the job in `running`, and the editor polls it to completion. Onboarding
// can take minutes (it scales with table count + LLM latency), so the poll has a
// generous wall-clock cap rather than the short fixed budget that previously made
// the UI report a still-running job as done and then stop polling.
const ONBOARDING_POLL_INTERVAL_MS = 2000;
const ONBOARDING_POLL_MAX_ATTEMPTS = 450; // ~15 minutes at the interval above

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
  const [documents, setDocuments] = useState<SemanticDocument[]>([]);
  const [state, setState] = useState<SemanticLayerState | null>(null);
  // Backend-derived readiness (empty | indexing | ready | failed). The rail uses
  // this for the truthful `failed`/`empty` states; `failed` in particular cannot
  // be derived client-side (it lives in the onboarding job history).
  const [readinessStatus, setReadinessStatus] =
    useState<SemanticProjectReadinessStatus | null>(null);
  const [readinessDetail, setReadinessDetail] = useState<string | null>(null);
  // Id of the onboarding job the backend reports as in-flight (readiness =
  // `indexing`). This lets the background poll resume after a remount/reload —
  // when component state (`pendingJobId`) is gone but onboarding is still running.
  const [readinessJobId, setReadinessJobId] = useState<string | null>(null);
  const [activeFileId, setActiveFileId] = useState<string | null>(null);
  const [selectedDocumentId, setSelectedDocumentId] = useState<string | null>(
    null,
  );
  const [editorPath, setEditorPath] = useState('models/new_model.json');
  const [editorValue, setEditorValue] = useState(defaultMdl);
  const [showImportDialog, setShowImportDialog] = useState(false);
  const [showOnboardPicker, setShowOnboardPicker] = useState(false);
  const [showProvenance, setShowProvenance] = useState(false);
  const [isOnboarding, setIsOnboarding] = useState(false);
  // Id of an onboarding job that is still running after the start call returned
  // (the threaded backend). A background effect polls it to completion; until
  // then the Copilot rail stays in the `indexing` (spinner) state.
  const [pendingJobId, setPendingJobId] = useState<string | null>(null);
  const [showResetConfirm, setShowResetConfirm] = useState(false);
  const [isResetting, setIsResetting] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [showCopilot, setShowCopilot] = useState(true);
  // Mirror the active file id in a ref so `refresh` can read the current
  // selection without depending on it (which would re-create the callback and
  // re-trigger the load effect — the cause of the autoscroll + dataset GET
  // storm). Synced on every render so it always holds the latest value.
  const activeFileIdRef = useRef<string | null>(null);
  activeFileIdRef.current = activeFileId;

  const activeFile = mdlFiles.find(file => file.id === activeFileId) || null;
  const selectedDocument =
    documents.find(document => document.id === selectedDocumentId) || null;
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
  const workspaceRoot = useMemo(
    () => treeFromFiles(mdlFiles, documents),
    [mdlFiles, documents],
  );

  const refresh = useCallback(async () => {
    if (!scope.schema_name) {
      setProject(null);
      setMdlFiles([]);
      setDocuments([]);
      setState(null);
      setReadinessStatus(null);
      setReadinessDetail(null);
      setReadinessJobId(null);
      return;
    }
    const nextProject = await resolveSemanticProject({
      database_id: scope.database_id,
      catalog_name: scope.catalog_name ?? null,
      schema_name: scope.schema_name,
      create_if_missing: true,
    });
    const [nextFiles, nextState, nextDocuments, nextReadiness] =
      await Promise.all([
        listMdlFiles(nextProject.id),
        getProjectSemanticLayerState(nextProject.id),
        // Documents are scope-governed; an empty list is fine when none uploaded.
        listSemanticDocuments(scope).catch(() => [] as SemanticDocument[]),
        // Readiness drives the Copilot rail's bootstrap-vs-chat split; tolerate a
        // failure (rail falls back to the local active-models heuristic).
        getProjectReadiness(nextProject.id).catch(() => null),
      ]);
    setProject(nextProject);
    setMdlFiles(nextFiles);
    setDocuments(nextDocuments);
    setState(nextState);
    setReadinessStatus(nextReadiness?.status ?? null);
    setReadinessDetail(nextReadiness?.detail ?? null);
    setReadinessJobId(nextReadiness?.running_job_id ?? null);
    // Drop a stale document selection if it no longer exists (functional update
    // so `refresh` need not depend on the selection — see the activeFileId note).
    setSelectedDocumentId(prev =>
      prev && nextDocuments.some(document => document.id === prev)
        ? prev
        : null,
    );
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
    setSelectedDocumentId(null);
    activeFileIdRef.current = file.id;
    setActiveFileId(file.id);
    setEditorPath(file.path);
    setEditorValue(file.content);
  };

  const selectDocument = (documentId: string) => {
    activeFileIdRef.current = null;
    setActiveFileId(null);
    setSelectedDocumentId(documentId);
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

  // Bulk delete (context menu / multi-select) — parity with a file browser.
  const deleteFiles = (fileIds: string[]) =>
    withLoading(async () => {
      if (!project || fileIds.length === 0) {
        return;
      }
      await Promise.all(fileIds.map(id => deleteMdlFile(project.id, id)));
      startNewFile();
      await refresh();
    }, t('Unable to delete MDL file(s)'));

  // Duplicate an MDL file as a new draft, with a unique "… copy" path.
  const duplicateFile = (fileId: string) =>
    withLoading(async () => {
      if (!project) {
        return;
      }
      const file = mdlFiles.find(item => item.id === fileId);
      if (!file) {
        return;
      }
      const dot = file.path.lastIndexOf('.');
      const base = dot === -1 ? file.path : file.path.slice(0, dot);
      const ext = dot === -1 ? '' : file.path.slice(dot);
      const taken = new Set(mdlFiles.map(item => item.path));
      let candidate = `${base} copy${ext}`;
      let suffix = 2;
      while (taken.has(candidate)) {
        candidate = `${base} copy ${suffix}${ext}`;
        suffix += 1;
      }
      const created = await createMdlFile(project.id, {
        path: candidate,
        content: file.content,
        source_type: 'manual',
      });
      setSelectedDocumentId(null);
      activeFileIdRef.current = created.id;
      setActiveFileId(created.id);
      setEditorPath(created.path);
      setEditorValue(created.content);
      await refresh();
    }, t('Unable to duplicate MDL file'));

  // Announce the result of a *completed* onboarding job. Only a terminal
  // `completed` job reaches here — a `running` job (start returned before the
  // threaded backend finished) is handed to the background poller instead, so we
  // never report success while onboarding is still in flight.
  const announceOnboardingComplete = useCallback(
    (job: SemanticJob) => {
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
    },
    [dispatch],
  );

  const runOnboard = useCallback(
    async (targetProjectId: string, selection?: OnboardingSelection) => {
      setIsOnboarding(true);
      try {
        // Start the job. An inline backend returns it already `completed`; the
        // threaded prod backend returns it `running` for us to poll.
        const job = await onboardSemanticProject(targetProjectId, selection);
        if (job.status === 'failed') {
          throw new Error(job.error || t('Onboarding failed'));
        }
        if (job.status === 'running') {
          // Hand off to the background poller (keeps the rail in `indexing`)
          // rather than reporting a premature success. The success/warning toast
          // and the file refresh fire when the job actually finishes.
          setPendingJobId(job.id);
          await refresh();
          return;
        }
        announceOnboardingComplete(job);
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
    [refresh, dispatch, announceOnboardingComplete],
  );

  // The onboarding job to poll: one we started this session (`pendingJobId`) or,
  // after a remount/reload, the in-flight job the backend still reports via
  // readiness. The latter is what lets the rail recover when component state was
  // lost but onboarding is genuinely still running.
  const pollJobId =
    pendingJobId ?? (readinessStatus === 'indexing' ? readinessJobId : null);

  // Background poll for an in-flight onboarding job. This makes the rail
  // self-heal: it polls until the job is terminal (regardless of how long
  // onboarding runs), then announces the outcome and refreshes so the spinner
  // clears and the freshly onboarded models appear — no manual page reload needed.
  useEffect(() => {
    if (!pollJobId || !project) {
      return undefined;
    }
    const projectId = project.id;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    let attemptsLeft = ONBOARDING_POLL_MAX_ATTEMPTS;

    const poll = async () => {
      let job: SemanticJob | null = null;
      try {
        job = await getSemanticJob(projectId, pollJobId);
      } catch {
        // Transient failure (e.g. agent restart): keep polling while the budget
        // lasts rather than abandoning a job that may still be running.
        job = null;
      }
      if (cancelled) {
        return;
      }
      if (job && job.status !== 'running') {
        setPendingJobId(null);
        if (job.status === 'failed') {
          dispatch(addDangerToast(job.error || t('Onboarding failed')));
        } else {
          announceOnboardingComplete(job);
        }
        await refresh();
        return;
      }
      attemptsLeft -= 1;
      if (attemptsLeft <= 0) {
        // Give up the foreground spinner after the cap, but surface the truth and
        // re-sync from the backend (which is the source of truth for readiness).
        setPendingJobId(null);
        dispatch(
          addWarningToast(
            t(
              'Onboarding is taking longer than expected; it may still be ' +
                'running. Refresh to check its status.',
            ),
          ),
        );
        await refresh();
        return;
      }
      timer = setTimeout(poll, ONBOARDING_POLL_INTERVAL_MS);
    };

    timer = setTimeout(poll, ONBOARDING_POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [pollJobId, project, dispatch, announceOnboardingComplete, refresh]);

  // Destructive "start over": delete all MDL so the project returns to the empty
  // (un-onboarded) state. Does NOT re-onboard — the rail re-gates the Copilot
  // behind an explicit Onboard. Gated behind a confirmation dialog. Documents
  // are kept so the user can re-enrich after re-onboarding.
  const resetProject = useCallback(async () => {
    if (!project) {
      return;
    }
    setShowResetConfirm(false);
    setIsResetting(true);
    try {
      await runReset(project.id);
      dispatch(
        addSuccessToast(
          t('Semantic layer reset. Onboard the schema to rebuild it.'),
        ),
      );
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

  // The Copilot may only edit once the MDL base layer exists and is stable. This
  // mirrors the backend readiness gate (which 409s premature edits). The rail
  // renders one of four states; onboarding is shown as a separate bootstrap
  // process (never as synthetic chat), and a Copilot turn is only possible when
  // `ready`. Starting an onboard (`isOnboarding`) or a job still being polled in
  // the background (`pendingJobId`) is the local `indexing` signal. Otherwise
  // trust the backend status (it is the only source of truth for `failed`), with
  // a local active-models fast-path for `ready` while readiness is still loading.
  const onboardingInFlight = isOnboarding || pollJobId !== null;
  const hasActiveModels = mdlFiles.some(file => file.status === 'active');
  const railStatus: SemanticProjectReadinessStatus = onboardingInFlight
    ? 'indexing'
    : (readinessStatus ?? (hasActiveModels ? 'ready' : 'empty'));

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

  return (
    <EditorRoot data-test="semantic-layer-editor">
      <EditorHeader>
        <Flex align="center" gap="small">
          <Flex vertical gap={0}>
            <Flex align="center" gap="small">
              <Typography.Title level={5} style={{ margin: 0 }}>
                {project?.name || t('Semantic layer')}
              </Typography.Title>
              {/* Provenance lives beside the schema name — the history of THIS MDL
                  directory, where a user expects to find it. */}
              <Tooltip title={t('Provenance')}>
                <Button
                  buttonStyle="link"
                  buttonSize="small"
                  disabled={!project}
                  icon={<Icons.HistoryOutlined iconSize="m" />}
                  onClick={() => setShowProvenance(true)}
                  aria-label={t('Provenance')}
                  data-test="open-provenance"
                />
              </Tooltip>
            </Flex>
            <SemanticLayerStateBadge state={state} />
          </Flex>
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
              <EditorSplitter>
                <Splitter.Panel
                  collapsible={{
                    start: true,
                    end: true,
                    showCollapsibleIcon: true,
                  }}
                  defaultSize={260}
                  min={180}
                >
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
                        activeDocumentId={selectedDocumentId}
                        onSelectFile={fileId => {
                          const file = mdlFiles.find(
                            item => item.id === fileId,
                          );
                          if (file) {
                            selectFile(file);
                          }
                        }}
                        onSelectDocument={selectDocument}
                        onDuplicateFile={duplicateFile}
                        onDeleteFiles={deleteFiles}
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
                      <Tooltip
                        title={t(
                          'Upload a document (PDF, Word, Excel, PowerPoint, CSV, ' +
                            'HTML) for the Copilot and viewer — or add MDL JSON / ' +
                            'Markdown.',
                        )}
                      >
                        <Button
                          buttonStyle="tertiary"
                          disabled={!project || !canWrite || isLoading}
                          onClick={() => setShowImportDialog(true)}
                          icon={<Icons.UploadOutlined iconSize="m" />}
                        >
                          {t('Upload document')}
                        </Button>
                      </Tooltip>
                      <Button
                        buttonStyle="tertiary"
                        loading={isResetting}
                        disabled={
                          !project ||
                          !canWrite ||
                          isLoading ||
                          onboardingInFlight ||
                          isResetting
                        }
                        onClick={() => setShowResetConfirm(true)}
                        icon={<Icons.ReloadOutlined iconSize="m" />}
                      >
                        {isResetting ? t('Resetting…') : t('Reset')}
                      </Button>
                    </Flex>
                  </BrowserPane>
                </Splitter.Panel>
                <Splitter.Panel className="semantic-editor-center-panel">
                  <EditorPane>
                    {selectedDocument ? (
                      <DocumentDetailPane
                        document={selectedDocument}
                        canWrite={canWrite}
                        onDeleted={() => {
                          setSelectedDocumentId(null);
                          refresh();
                        }}
                        onChanged={refresh}
                      />
                    ) : (
                      <>
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
                              <Tag
                                color="warning"
                                data-test="mdl-dirty-indicator"
                              >
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
                        {activeFile?.validation &&
                          !activeFile.validation.valid && (
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
                              disabled={
                                !activeFile || isLoading || isValidating
                              }
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
                      </>
                    )}
                  </EditorPane>
                </Splitter.Panel>
                {showCopilot && project ? (
                  <Splitter.Panel
                    collapsible={{
                      start: true,
                      end: true,
                      showCollapsibleIcon: true,
                    }}
                    defaultSize={360}
                    min={280}
                  >
                    <CopilotRail data-test="copilot-rail">
                      {/* The panel stays mounted in every state so its chat
                          transcript survives empty↔ready transitions (e.g. after
                          reset) within a session. When not `ready` it renders a
                          bootstrap view (help text + Onboard/Retry) instead of the
                          chat — onboarding is shown as a separate process. */}
                      <CopilotPanel
                        projectId={project.id}
                        canWrite={canWrite}
                        onApplied={refresh}
                        readinessStatus={railStatus}
                        readinessDetail={readinessDetail}
                        onOnboard={() => setShowOnboardPicker(true)}
                        onUpload={() => setShowImportDialog(true)}
                      />
                    </CopilotRail>
                  </Splitter.Panel>
                ) : null}
              </EditorSplitter>
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
      <OnboardingTablePicker
        open={showOnboardPicker}
        databaseId={databaseId}
        catalogName={catalogName}
        schema={schemaName}
        canWrite={canWrite}
        onCancel={() => setShowOnboardPicker(false)}
        onConfirm={selection => {
          setShowOnboardPicker(false);
          if (project) {
            runOnboard(project.id, selection);
          }
        }}
      />
      <MdlProvenanceDialog
        open={showProvenance}
        projectId={project?.id ?? null}
        onClose={() => setShowProvenance(false)}
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
            'enrichments and any hand-edited files — returning it to the ' +
            'un-onboarded state. Onboarding does not run automatically: you ' +
            'choose when to rebuild the base models by clicking Onboard. ' +
            'Uploaded documents are kept, so you can re-enrich afterward. This ' +
            'cannot be undone.',
        )}
      />
    </EditorRoot>
  );
}
