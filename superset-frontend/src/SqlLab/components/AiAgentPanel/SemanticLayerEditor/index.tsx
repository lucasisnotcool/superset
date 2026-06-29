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
  Skeleton,
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
  createSemanticProject,
  deleteMdlFile,
  deleteSemanticProject,
  duplicateSemanticProject,
  getProjectReadiness,
  getProjectSemanticLayerState,
  getSemanticJob,
  getSemanticProject,
  listMdlFiles,
  listProjectDocuments,
  listSemanticDocuments,
  listSemanticProjects,
  MdlFile,
  MdlFileStatus,
  onboardSemanticProject,
  OnboardingSelection,
  renameSemanticProject,
  resolveSemanticProject,
  runReset,
  SemanticDocument,
  SemanticJob,
  SemanticLayerState,
  SemanticProject,
  SemanticProjectReadinessStatus,
  setMdlFilesStatus,
  updateMdlFile,
  validateMdlFile,
} from '../api';
import SemanticLayerStateBadge from '../SemanticLayerStateBadge';
import useDocumentIngestion from '../useDocumentIngestion';
import InstructionsPanel from './InstructionsPanel';
import NewProjectModal from './NewProjectModal';
import CopilotPanel, { type CopilotKickstart } from './CopilotPanel';
import OnboardingTablePicker from './OnboardingTablePicker';
import AutoOnboardModal from './AutoOnboardModal';
import MdlProvenanceDialog from './MdlProvenanceDialog';
import CoverageBadge from './CoverageBadge';
import SchemaSetControl from './SchemaSetControl';
import DocumentDetailPane from './DocumentDetailPane';
import ProjectBrowser, { ProjectBrowserProject } from './ProjectBrowser';
import WorkspaceTree, { treeFromFiles } from './WorkspaceTree';
import { isPendingDocumentStatus } from './documentStatus';

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

// The selected project's workspace fills the Lab's detail (right) panel.
const WorkspacePane = styled.div`
  display: flex;
  flex-direction: column;
  flex: 1;
  min-height: 0;
  height: 100%;
`;

// A thin, project-scoped action strip — the few project-level controls relocated
// from the removed global header (schema set, provenance, Copilot toggle). It is a
// toolbar, not a title bar: the project's identity is implied by the browser.
const WorkspaceStrip = styled.div`
  ${({ theme }) => css`
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: ${theme.sizeUnit * 2}px;
    padding: ${theme.sizeUnit * 2}px ${theme.sizeUnit * 3}px;
    border-bottom: 1px solid ${theme.colorBorderSecondary};
    flex-wrap: wrap;
  `}
`;

// Fills the workspace detail pane while a project is opening (G2): a skeleton in
// the content area so the open reads as "loading", not as "nothing selected".
const SkeletonBody = styled.div`
  ${({ theme }) => css`
    flex: 1;
    min-height: 0;
    padding: ${theme.sizeUnit * 4}px;
  `}
`;

const EmptyWorkspace = styled.div`
  ${({ theme }) => css`
    display: flex;
    flex: 1;
    align-items: center;
    justify-content: center;
    padding: ${theme.sizeUnit * 6}px;
    color: ${theme.colorTextSecondary};
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

// While any uploaded document is still extracting (large files extract on a
// background thread), poll the document list so the workspace tree shows live
// status instead of a stale snapshot. Bounded like onboarding (~4 min) so a stuck
// extraction doesn't poll forever.
const DOCUMENT_POLL_INTERVAL_MS = 2000;
const DOCUMENT_POLL_MAX_ATTEMPTS = 120;

/** Stable status signature so an unchanged poll doesn't churn the tree. */
const documentStatusSignature = (documents: SemanticDocument[]): string =>
  documents
    .map(document => `${document.id}:${document.status}`)
    .sort()
    .join('|');

export interface SemanticLayerEditorProps {
  databaseId: number;
  catalogName: string | null;
  schemaName: string;
  /** Additional schemas to seed the project's set with (primary stays
   * `schemaName`). Optional; the editor also lets a user add schemas in-place. */
  schemaNames?: string[];
  /**
   * First-class entry (F1/DP4): open this project by id instead of resolving the
   * default project for `(databaseId, catalogName, schemaName)`. When set, the
   * editor is project-keyed (the Lab/deep-link entry); when absent it keeps the
   * legacy schema-tree resolve-or-create behavior. The scope props still bound the
   * database for the project browser.
   */
  projectId?: string;
}

// The templated first turn the Auto-onboard flow sends on the user's behalf. It
// names the doc-driven onboarding steps (read → map → onboard → relate → review)
// so the Copilot follows the onboarding skill; the message is visible in the
// transcript (not hidden) so the user sees and can steer the conversation.
const AUTO_ONBOARD_MESSAGE =
  'Read the attached document(s) and onboard the tables they describe from ' +
  'this database, then add the relationships and enrich the models with the ' +
  'definitions, synonyms, and metrics the document specifies. Show me one ' +
  'changeset to review.';

export default function SemanticLayerEditor({
  databaseId,
  catalogName,
  schemaName,
  schemaNames,
  projectId,
}: SemanticLayerEditorProps) {
  const dispatch = useDispatch();
  // Ordered, de-duplicated requested schema set with the primary (`schemaName`)
  // first. Mirrors the backend `normalize_schema_names` contract.
  const requestedSchemaNames = useMemo(() => {
    const ordered: string[] = [];
    [schemaName, ...(schemaNames ?? [])].forEach(name => {
      if (name && !ordered.includes(name)) {
        ordered.push(name);
      }
    });
    return ordered;
  }, [schemaName, schemaNames]);
  const scope: ConversationScope = useMemo(
    () => ({
      database_id: databaseId,
      catalog_name: catalogName,
      schema_name: schemaName,
      schema_names: requestedSchemaNames,
      dataset_ids: [],
    }),
    [databaseId, catalogName, schemaName, requestedSchemaNames],
  );

  const [project, setProject] = useState<SemanticProject | null>(null);
  // MDL Lab project browser (F1/F2): the database's projects + which one is open.
  // ``selectedProjectIdRef`` makes the scope-resolve refresh prefer an explicitly
  // opened project without re-arming on every selection change.
  const [projects, setProjects] = useState<SemanticProject[]>([]);
  // The project-list fetch runs on its own effect (not `withLoading`), so it
  // needs its own flag — `isLoading` reflects per-project mutations, not the
  // list. Starts `true` so the first paint is a skeleton, never a false empty.
  const [isListLoading, setIsListLoading] = useState(true);
  const [projectsReloadSignal, setProjectsReloadSignal] = useState(0);
  const selectedProjectIdRef = useRef<string | null>(null);
  const [renameTarget, setRenameTarget] = useState<SemanticProject | null>(
    null,
  );
  const [renameValue, setRenameValue] = useState('');
  const [duplicateTarget, setDuplicateTarget] =
    useState<SemanticProject | null>(null);
  const [duplicateIncludeDocs, setDuplicateIncludeDocs] = useState(false);
  const [isDuplicating, setIsDuplicating] = useState(false);
  const [isRenaming, setIsRenaming] = useState(false);
  const [showNewProject, setShowNewProject] = useState(false);
  const [isCreatingProject, setIsCreatingProject] = useState(false);
  // Delete-project confirmation (parity with Reset): a dialog gates the
  // destructive delete, and the spinner shows while it is in flight.
  const [deleteTarget, setDeleteTarget] = useState<SemanticProject | null>(
    null,
  );
  const [isDeletingProject, setIsDeletingProject] = useState(false);
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
  const [showOnboardPicker, setShowOnboardPicker] = useState(false);
  const [showAutoOnboard, setShowAutoOnboard] = useState(false);
  // The kickstart handed to the Copilot when the user confirms Auto-onboard. A
  // fresh `token` (monotonic counter, not a wall-clock — kept deterministic for
  // tests) fires exactly one document-grounded onboarding turn.
  const [kickstart, setKickstart] = useState<CopilotKickstart | null>(null);
  const kickstartTokenRef = useRef(0);
  const [showProvenance, setShowProvenance] = useState(false);
  const [isOnboarding, setIsOnboarding] = useState(false);
  // Id of an onboarding job that is still running after the start call returned
  // (the threaded backend). A background effect polls it to completion; until
  // then the Copilot rail stays in the `indexing` (spinner) state.
  const [pendingJobId, setPendingJobId] = useState<string | null>(null);
  const [showResetConfirm, setShowResetConfirm] = useState(false);
  const [isResetting, setIsResetting] = useState(false);
  const [isAddingSchema, setIsAddingSchema] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  // True while a project is being opened but none is shown yet — drives the
  // workspace skeleton so the deep-link / row-click open shows "loading" rather
  // than the `mdl-empty` "Select a project" state (which misreads an in-flight
  // open as "nothing selected"). Seeded from `projectId` so the very first paint
  // of a deep-linked Lab tab is a skeleton, covering the gap before the project
  // GET resolves and `openProject` runs.
  const [isOpening, setIsOpening] = useState(!!projectId);
  const [showCopilot, setShowCopilot] = useState(true);
  // Mirror the active file id in a ref so `refresh` can read the current
  // selection without depending on it (which would re-create the callback and
  // re-trigger the load effect — the cause of the autoscroll + dataset GET
  // storm). Synced on every render so it always holds the latest value.
  const activeFileIdRef = useRef<string | null>(null);
  activeFileIdRef.current = activeFileId;
  // Hidden file input backing the "Upload document" button. Upload runs the SAME
  // shared ingestion pipeline as the Copilot "Attach" (persist + dedup + vectorize
  // + show in the tree); the only difference is it does not attach to a chat.
  const uploadInputRef = useRef<HTMLInputElement>(null);
  const { ingest, isIngesting } = useDocumentIngestion(project?.id ?? null);

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

  // The scope that drives schema-aware sub-views (instructions, graph, onboarding)
  // is derived from the OPENED PROJECT, not the entry tab — the Lab is browse-first
  // and may carry no schema, so the project (its primary schema + full set) is the
  // source of truth once one is open. Falls back to the tab scope before any open.
  const projectScope: ConversationScope = useMemo(() => {
    if (!project) {
      return scope;
    }
    return {
      database_id: project.default_database_id ?? databaseId,
      catalog_name: project.catalog_name ?? catalogName,
      schema_name: project.schema_name,
      schema_names: project.schema_names ?? [project.schema_name],
      dataset_ids: [],
    };
  }, [project, scope, databaseId, catalogName]);

  // Apply a freshly-loaded project's contents to state without changing which
  // project is open. Shared by both refresh paths so the "open project" and
  // "resolve by scope" flows stay byte-identical in how they hydrate the editor.
  const applyProjectData = useCallback(
    (
      nextProject: SemanticProject,
      nextFiles: MdlFile[],
      nextState: SemanticLayerState | null,
      nextDocuments: SemanticDocument[],
      nextReadiness: {
        status?: SemanticProjectReadinessStatus | null;
        detail?: string | null;
        running_job_id?: string | null;
      } | null,
    ) => {
      setProject(nextProject);
      setMdlFiles(nextFiles);
      setState(nextState);
      setDocuments(nextDocuments);
      setReadinessStatus(nextReadiness?.status ?? null);
      setReadinessDetail(nextReadiness?.detail ?? null);
      setReadinessJobId(nextReadiness?.running_job_id ?? null);
      // Drop a stale document selection if it no longer exists (functional update
      // so callers need not depend on the selection — see the activeFileId note).
      setSelectedDocumentId(prev =>
        prev && nextDocuments.some(document => document.id === prev)
          ? prev
          : null,
      );
      // Initialize the file selection only when nothing valid is selected yet;
      // never override the user's current file/edits on a background refresh.
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
    },
    [],
  );

  // Single source of truth for "which project is loaded". When a project is
  // explicitly open (``selectedProjectIdRef``), reload it BY ID — never re-derive
  // it from ambient scope. The Lab entry carries no schema, so the scope
  // resolve-or-create path would otherwise ``setProject(null)`` and deselect the
  // project on EVERY mutation refresh (onboard start, changeset apply, file save,
  // upload…). Scope resolve-or-create runs only when nothing is selected yet —
  // the legacy schema-tree entry's initial load. The id path NEVER nulls the
  // selection.
  const refresh = useCallback(async () => {
    const selectedId = selectedProjectIdRef.current;
    if (selectedId) {
      const [target, files, projectState, docs, readiness] = await Promise.all([
        getSemanticProject(selectedId),
        listMdlFiles(selectedId),
        getProjectSemanticLayerState(selectedId),
        listProjectDocuments(selectedId).catch(() => [] as SemanticDocument[]),
        getProjectReadiness(selectedId).catch(() => null),
      ]);
      applyProjectData(target, files, projectState, docs, readiness);
      return;
    }
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
      schema_names: scope.schema_names ?? undefined,
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
    applyProjectData(
      nextProject,
      nextFiles,
      nextState,
      nextDocuments,
      nextReadiness,
    );
  }, [scope, applyProjectData]);

  useEffect(() => {
    // Project-keyed entry (F1) owns loading; skip the scope resolve-or-create so
    // the two paths never race. Legacy schema-tree entry (no `projectId`) is
    // unchanged — identical to before, keeping the existing editor tests stable.
    if (projectId) {
      return;
    }
    refresh().catch(ex => {
      dispatch(
        addDangerToast(
          ex instanceof Error ? ex.message : t('Unable to load semantic layer'),
        ),
      );
    });
  }, [refresh, dispatch, projectId]);

  // F1/F2: load the database's projects for the browser. Tolerant — a failure (or
  // an unscoped database) just yields an empty list; never blocks the editor.
  useEffect(() => {
    let cancelled = false;
    setIsListLoading(true);
    listSemanticProjects(databaseId, catalogName ?? null, null)
      .then(list => {
        if (!cancelled) setProjects(list);
      })
      .catch(() => {
        if (!cancelled) setProjects([]);
      })
      .finally(() => {
        if (!cancelled) setIsListLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [databaseId, catalogName, projectsReloadSignal]);

  const reloadProjects = useCallback(
    () => setProjectsReloadSignal(signal => signal + 1),
    [],
  );

  // Open a project by id (F2): loads its files/state/documents/readiness and marks
  // it as the explicitly-selected project so a scope refresh won't clobber it.
  const openProject = useCallback(
    async (target: SemanticProject) => {
      selectedProjectIdRef.current = target.id;
      setIsLoading(true);
      setIsOpening(true);
      try {
        const [files, projectState, docs, readiness] = await Promise.all([
          listMdlFiles(target.id),
          getProjectSemanticLayerState(target.id),
          listProjectDocuments(target.id).catch(() => [] as SemanticDocument[]),
          getProjectReadiness(target.id).catch(() => null),
        ]);
        setProject(target);
        setMdlFiles(files);
        setState(projectState);
        setDocuments(docs);
        setReadinessStatus(readiness?.status ?? null);
        setReadinessDetail(readiness?.detail ?? null);
        setReadinessJobId(readiness?.running_job_id ?? null);
        const firstFile = files[0] || null;
        activeFileIdRef.current = firstFile?.id ?? null;
        setActiveFileId(firstFile?.id ?? null);
        if (firstFile) {
          setEditorPath(firstFile.path);
          setEditorValue(firstFile.content);
        } else {
          setEditorPath('models/new_model.json');
          setEditorValue(defaultMdl);
        }
      } catch (ex) {
        dispatch(
          addDangerToast(
            ex instanceof Error ? ex.message : t('Unable to open project'),
          ),
        );
      } finally {
        setIsLoading(false);
        setIsOpening(false);
      }
    },
    [dispatch],
  );

  // ``refresh`` is now id-aware (it reloads the open project by id and never
  // deselects), so this is just an alias kept for the onboarding/poller call
  // sites that document intent ("refresh the open project").
  const refreshOpenProject = refresh;

  // First-class entry (F1/DP4): when launched with a `projectId`, open that project
  // by id (reusing the browser's tested open path) instead of resolving by schema.
  // Additive — inert when no `projectId` is provided, so legacy entry is unchanged.
  useEffect(() => {
    if (!projectId) {
      return undefined;
    }
    let cancelled = false;
    getSemanticProject(projectId)
      .then(target => (cancelled ? undefined : openProject(target)))
      .catch(ex => {
        if (!cancelled) {
          // The seeded `isOpening` would otherwise pin the skeleton forever when
          // the project GET fails before `openProject` (which clears it) runs.
          setIsOpening(false);
          dispatch(
            addDangerToast(
              ex instanceof Error ? ex.message : t('Unable to open project'),
            ),
          );
        }
      });
    return () => {
      cancelled = true;
    };
  }, [projectId, openProject, dispatch]);

  // "New project" opens a dialog to name the project and choose its schema set
  // (a project covers one database but may span several schemas); the first chosen
  // schema is the primary. Creation proves DB access to every chosen schema.
  const handleCreateSubmit = useCallback(
    async ({
      name,
      schemaNames: chosen,
    }: {
      name: string;
      schemaNames: string[];
    }) => {
      if (chosen.length === 0) {
        return;
      }
      // Keep the dialog open with a spinner until the project is created (the
      // server proves access to every chosen schema, which can take a moment);
      // close only on success so a failure leaves the form up to retry.
      setIsCreatingProject(true);
      try {
        const created = await createSemanticProject({
          database_id: databaseId,
          catalog_name: catalogName ?? null,
          schema_name: chosen[0],
          schema_names: chosen,
          name: name || undefined,
        });
        reloadProjects();
        setShowNewProject(false);
        await openProject(created);
        dispatch(addSuccessToast(t('Project created.')));
      } catch (ex) {
        dispatch(
          addDangerToast(
            ex instanceof Error ? ex.message : t('Unable to create project'),
          ),
        );
      } finally {
        setIsCreatingProject(false);
      }
    },
    [databaseId, catalogName, openProject, reloadProjects, dispatch],
  );

  // Duplicate opens a confirm dialog so the user can choose whether to also copy
  // the BI documents (DP6 opt-in); the default clone is structure-only.
  const handleDuplicateProject = useCallback(
    (projectId: string) => {
      const target = projects.find(p => p.id === projectId);
      if (target) {
        setDuplicateTarget(target);
        setDuplicateIncludeDocs(false);
      }
    },
    [projects],
  );

  const handleDuplicateConfirm = useCallback(async () => {
    if (!duplicateTarget) return;
    const target = duplicateTarget;
    const includeDocuments = duplicateIncludeDocs;
    // Keep the dialog open (with a spinner) until the clone resolves; close only
    // on success so a failure leaves the dialog up to retry.
    setIsDuplicating(true);
    try {
      const clone = await duplicateSemanticProject(
        target.id,
        null,
        includeDocuments,
      );
      reloadProjects();
      await openProject(clone);
      dispatch(addSuccessToast(t('Project duplicated.')));
      setDuplicateTarget(null);
    } catch (ex) {
      dispatch(
        addDangerToast(
          ex instanceof Error ? ex.message : t('Unable to duplicate project'),
        ),
      );
    } finally {
      setIsDuplicating(false);
    }
  }, [
    duplicateTarget,
    duplicateIncludeDocs,
    openProject,
    reloadProjects,
    dispatch,
  ]);

  // Delete is destructive (it removes the project and all its MDL), so it is
  // gated behind a confirmation dialog — parity with Reset. The menu action only
  // opens the dialog; the dialog's confirm performs the delete with a spinner.
  const handleDeleteProject = useCallback(
    (projectId: string) => {
      const target = projects.find(p => p.id === projectId);
      if (target) {
        setDeleteTarget(target);
      }
    },
    [projects],
  );

  const handleDeleteConfirm = useCallback(async () => {
    if (!deleteTarget) return;
    const target = deleteTarget;
    setIsDeletingProject(true);
    try {
      await deleteSemanticProject(target.id);
      reloadProjects();
      if (selectedProjectIdRef.current === target.id) {
        selectedProjectIdRef.current = null;
        await refresh();
      }
      dispatch(addSuccessToast(t('Project deleted.')));
      setDeleteTarget(null);
    } catch (ex) {
      dispatch(
        addDangerToast(
          ex instanceof Error ? ex.message : t('Unable to delete project'),
        ),
      );
    } finally {
      setIsDeletingProject(false);
    }
  }, [deleteTarget, refresh, reloadProjects, dispatch]);

  const handleRenameSubmit = useCallback(async () => {
    if (!renameTarget) return;
    const name = renameValue.trim();
    if (!name) return;
    setIsRenaming(true);
    try {
      const renamed = await renameSemanticProject(renameTarget.id, name);
      reloadProjects();
      if (selectedProjectIdRef.current === renamed.id) setProject(renamed);
      dispatch(addSuccessToast(t('Project renamed.')));
      setRenameTarget(null);
    } catch (ex) {
      dispatch(
        addDangerToast(
          ex instanceof Error ? ex.message : t('Unable to rename project'),
        ),
      );
    } finally {
      setIsRenaming(false);
    }
  }, [renameTarget, renameValue, reloadProjects, dispatch]);

  const browserProjects: ProjectBrowserProject[] = useMemo(
    () =>
      projects.map(item => ({
        id: item.id,
        name: item.name,
        slug: item.slug ?? '',
        primarySchema: item.schema_name,
        schemaCount: item.schema_names?.length || 1,
        databaseLabel: item.database_label ?? null,
        permission: item.permission === 'read' ? 'read' : 'write',
        updatedAt: item.updated_at,
        coverageScore: item.coverage_score ?? null,
      })),
    [projects],
  );

  // Widen the project to cover another schema. Re-resolves with the expanded set
  // (the backend proves access to the new schema before associating it), then
  // refreshes so the schema chip appears and the new schema's tables become
  // onboardable. Models pointing at a schema outside the set are rejected by
  // validation (R1), so this is the only sanctioned way to grow coverage.
  const addSchema = useCallback(
    async (schema: string) => {
      if (!project || project.schema_names?.includes(schema)) {
        return;
      }
      setIsAddingSchema(true);
      try {
        await resolveSemanticProject({
          database_id: scope.database_id,
          catalog_name: scope.catalog_name ?? null,
          schema_name: project.schema_name,
          schema_names: [
            ...(project.schema_names ?? [project.schema_name]),
            schema,
          ],
          create_if_missing: false,
        });
        await refresh();
        dispatch(addSuccessToast(t('Added schema "%(schema)s".', { schema })));
      } catch (ex) {
        dispatch(
          addDangerToast(
            ex instanceof Error ? ex.message : t('Unable to add schema'),
          ),
        );
      } finally {
        setIsAddingSchema(false);
      }
    },
    [project, scope, refresh, dispatch],
  );

  // Upload document(s) through the shared ingestion pipeline, then refresh so the
  // new files appear in the workspace tree. No chat involvement — this is the
  // "Attach minus the conversation" ingress.
  const handleUpload = useCallback(
    async (event: ChangeEvent<HTMLInputElement>) => {
      const files = Array.from(event.target.files ?? []);
      event.target.value = '';
      if (!files.length) return;
      const results = await ingest(files);
      if (results.length) {
        await refresh();
      }
    },
    [ingest, refresh],
  );

  // Identifies the specific mutation in flight so each control can show its own
  // spinner (G3–G6) without a separate boolean per action. `isLoading` still
  // disables the whole toolbar to prevent overlapping writes; `pendingAction`
  // only drives which control spins. Per-file toggles key on the file id.
  const [pendingAction, setPendingAction] = useState<string | null>(null);
  const withLoading = async (
    action: () => Promise<void>,
    fallback: string,
    key?: string,
  ) => {
    setIsLoading(true);
    if (key) {
      setPendingAction(key);
    }
    try {
      await action();
    } catch (ex) {
      dispatch(addDangerToast(ex instanceof Error ? ex.message : fallback));
    } finally {
      setIsLoading(false);
      setPendingAction(null);
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
    withLoading(
      async () => {
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
      },
      t('Unable to save MDL file'),
      status === 'active' ? 'save:active' : 'save',
    );

  const deleteFile = (file: MdlFile) =>
    withLoading(
      async () => {
        if (!project) {
          return;
        }
        await deleteMdlFile(project.id, file.id);
        startNewFile();
        await refresh();
      },
      t('Unable to delete MDL file'),
      'delete',
    );

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
          await refreshOpenProject();
          return;
        }
        announceOnboardingComplete(job);
        await refreshOpenProject();
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
    [refreshOpenProject, dispatch, announceOnboardingComplete],
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
        await refreshOpenProject();
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
        await refreshOpenProject();
        return;
      }
      timer = setTimeout(poll, ONBOARDING_POLL_INTERVAL_MS);
    };

    timer = setTimeout(poll, ONBOARDING_POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [
    pollJobId,
    project,
    dispatch,
    announceOnboardingComplete,
    refreshOpenProject,
  ]);

  // Live workspace tree: while any document is still extracting, re-fetch the
  // document list so its status badge advances to terminal without a manual
  // refresh — for files added via the Upload button OR Copilot Attach (both land
  // in `documents`). Bounded + cancel-safe; change-guarded so an unchanged poll
  // keeps the array identity stable (no tree churn, no effect re-arm).
  useEffect(() => {
    if (
      !scope.schema_name ||
      !documents.some(d => isPendingDocumentStatus(d.status))
    ) {
      return undefined;
    }
    let cancelled = false;
    let attemptsLeft = DOCUMENT_POLL_MAX_ATTEMPTS;
    let timer: ReturnType<typeof setTimeout>;

    const poll = async () => {
      let next: SemanticDocument[] | null = null;
      try {
        next = await listSemanticDocuments(scope);
      } catch {
        next = null; // transient; keep polling within budget
      }
      if (cancelled) {
        return;
      }
      if (next) {
        const fetched = next;
        setDocuments(prev =>
          documentStatusSignature(prev) === documentStatusSignature(fetched)
            ? prev
            : fetched,
        );
      }
      attemptsLeft -= 1;
      const stillPending =
        next?.some(d => isPendingDocumentStatus(d.status)) ?? true;
      if (!stillPending || attemptsLeft <= 0) {
        return;
      }
      timer = setTimeout(poll, DOCUMENT_POLL_INTERVAL_MS);
    };

    timer = setTimeout(poll, DOCUMENT_POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [documents, scope]);

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
    withLoading(
      async () => {
        if (!project) {
          return;
        }
        await updateMdlFile(project.id, file.id, {
          status: activate ? 'active' : 'draft',
        });
        await refresh();
      },
      t('Unable to update MDL file'),
      `toggle:${file.id}`,
    );

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

  // Activate (or deactivate) every MDL file in the library in one atomic call.
  // The server validates the whole projected active manifest once, so dependent
  // files (a metric and the model its baseObject references) activate together
  // regardless of order — the old per-file Promise.all raced and rejected a
  // metric activated before its model. Activation is all-or-nothing.
  const setAllStatuses = (activate: boolean) =>
    withLoading(
      async () => {
        if (!project) {
          return;
        }
        const nextStatus: MdlFileStatus = activate ? 'active' : 'draft';
        await setMdlFilesStatus(project.id, nextStatus);
        await refresh();
      },
      t('Unable to update MDL files'),
      'bulk',
    );

  return (
    <EditorRoot data-test="semantic-layer-editor">
      {/* MDL Lab master–detail: the project browser is the master (left); the
          selected project's workspace (Models / Instructions / Graph + Copilot) is
          the detail (right). There is no global header — project-level controls live
          in the workspace strip, and per-project status in the browser rows. */}
      <EditorSplitter>
        <Splitter.Panel
          collapsible={{ start: true, end: true, showCollapsibleIcon: true }}
          defaultSize={300}
          min={220}
        >
          <BrowserPane>
            <ProjectBrowser
              projects={browserProjects}
              loading={isListLoading}
              activeProjectId={project?.id ?? null}
              onOpen={projectId => {
                const target = projects.find(p => p.id === projectId);
                if (target) openProject(target);
              }}
              onCreate={() => setShowNewProject(true)}
              onDuplicate={handleDuplicateProject}
              onRename={projectId => {
                const target = projects.find(p => p.id === projectId);
                if (target) {
                  setRenameTarget(target);
                  setRenameValue(target.name);
                }
              }}
              onDelete={handleDeleteProject}
            />
          </BrowserPane>
        </Splitter.Panel>
        <Splitter.Panel className="semantic-editor-center-panel">
          {project ? (
            <WorkspacePane data-test="mdl-workspace">
              <WorkspaceStrip>
                <SchemaSetControl
                  schemaNames={project.schema_names ?? [project.schema_name]}
                  primarySchema={project.schema_name}
                  databaseId={databaseId}
                  catalogName={catalogName}
                  canEdit={canWrite}
                  adding={isAddingSchema}
                  onAddSchema={addSchema}
                />
                <Flex align="center" gap="small" wrap="wrap">
                  <SemanticLayerStateBadge state={state} />
                  <CoverageBadge
                    projectId={project.id}
                    refreshSignal={mdlFiles
                      .filter(file => file.status === 'active')
                      .map(file => `${file.id}:${file.checksum}`)
                      .join(',')}
                  />
                  <Tooltip title={t('Provenance')}>
                    <Button
                      buttonStyle="link"
                      buttonSize="small"
                      icon={<Icons.HistoryOutlined iconSize="m" />}
                      onClick={() => setShowProvenance(true)}
                      aria-label={t('Provenance')}
                      data-test="open-provenance"
                    />
                  </Tooltip>
                  <Button
                    buttonStyle={showCopilot ? 'primary' : 'tertiary'}
                    buttonSize="small"
                    icon={<Icons.CommentOutlined iconSize="m" />}
                    onClick={() => setShowCopilot(value => !value)}
                    data-test="toggle-copilot"
                  >
                    {t('Copilot')}
                  </Button>
                </Flex>
              </WorkspaceStrip>
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
                            <Flex gap="small" wrap="wrap">
                              <Button
                                buttonStyle="primary"
                                loading={pendingAction === 'save'}
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
                                        loading={
                                          pendingAction === `toggle:${file.id}`
                                        }
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
                              loading={pendingAction === 'bulk'}
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
                              {allActive
                                ? t('Deactivate all')
                                : t('Activate all')}
                            </Button>
                            {/* One action per row, each full-width — matching the
                                block "Activate/Deactivate all" above, so a button
                                that is alone on its row fills it rather than
                                hugging the left edge. */}
                            <Flex vertical gap="small">
                              <input
                                ref={uploadInputRef}
                                type="file"
                                multiple
                                accept=".json,.md,.markdown,.txt,.csv,.html,.pdf,.docx,.xlsx,.pptx"
                                css={css`
                                  display: none;
                                `}
                                onChange={handleUpload}
                                data-test="semantic-upload-input"
                              />
                              <Tooltip
                                title={t(
                                  'Upload a document (PDF, Word, Excel, PowerPoint, CSV, ' +
                                    'HTML, Markdown, JSON). It is added to the workspace ' +
                                    'and vectorized for the Copilot and viewer.',
                                )}
                              >
                                <Button
                                  block
                                  buttonStyle="tertiary"
                                  disabled={
                                    !project ||
                                    !canWrite ||
                                    isLoading ||
                                    isIngesting
                                  }
                                  loading={isIngesting}
                                  onClick={() =>
                                    uploadInputRef.current?.click()
                                  }
                                  icon={<Icons.UploadOutlined iconSize="m" />}
                                  data-test="semantic-upload-document"
                                >
                                  {t('Upload document')}
                                </Button>
                              </Tooltip>
                              <Button
                                block
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
                                    onChange={(
                                      event: ChangeEvent<HTMLInputElement>,
                                    ) => setEditorPath(event.target.value)}
                                  />
                                  {isDirty && (
                                    <Tooltip
                                      title={t('You have unsaved changes')}
                                    >
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
                                <Flex
                                  justify="space-between"
                                  gap="small"
                                  wrap="wrap"
                                >
                                  <Flex gap="small" wrap="wrap">
                                    <Button
                                      buttonStyle="primary"
                                      loading={pendingAction === 'save'}
                                      disabled={
                                        !project || !canWrite || isLoading
                                      }
                                      onClick={() => saveFile()}
                                      icon={<Icons.SaveOutlined iconSize="m" />}
                                    >
                                      {t('Save draft')}
                                    </Button>
                                    <Button
                                      buttonStyle="tertiary"
                                      loading={pendingAction === 'save:active'}
                                      disabled={
                                        !project || !canWrite || isLoading
                                      }
                                      onClick={() => saveFile('active')}
                                      icon={
                                        <Icons.CheckCircleOutlined iconSize="m" />
                                      }
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
                                      icon={
                                        <Icons.CheckCircleOutlined iconSize="m" />
                                      }
                                      data-test="mdl-validate"
                                    >
                                      {t('Validate')}
                                    </Button>
                                  </Flex>
                                  <Button
                                    buttonStyle="danger"
                                    loading={pendingAction === 'delete'}
                                    disabled={
                                      !activeFile ||
                                      !project ||
                                      !canWrite ||
                                      isLoading
                                    }
                                    onClick={() =>
                                      activeFile && deleteFile(activeFile)
                                    }
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
                                // Key by project id so the panel's thread state is
                                // fully isolated per project — switching projects
                                // never carries a foreign conversation/changeset
                                // into the newly opened one.
                                key={project.id}
                                projectId={project.id}
                                canWrite={canWrite}
                                onApplied={refresh}
                                readinessStatus={railStatus}
                                readinessDetail={readinessDetail}
                                onOnboard={() => setShowOnboardPicker(true)}
                                onAutoOnboard={() => setShowAutoOnboard(true)}
                                kickstart={kickstart ?? undefined}
                                // Clear once consumed so an Apply→refresh remount
                                // cannot re-fire the same auto-onboard turn.
                                onKickstartHandled={() => setKickstart(null)}
                                onDocumentsChanged={refresh}
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
                    children: (
                      <InstructionsPanel
                        scope={projectScope}
                        canWrite={canWrite}
                      />
                    ),
                  },
                  {
                    key: 'graph',
                    label: t('Graph'),
                    children: (
                      <Suspense
                        fallback={
                          <SkeletonBody data-test="graph-loading">
                            <Skeleton active paragraph={{ rows: 6 }} />
                          </SkeletonBody>
                        }
                      >
                        <SchemaGraph
                          mdlFiles={mdlFiles}
                          databaseId={databaseId}
                          catalogName={catalogName}
                          schemaName={projectScope.schema_name ?? schemaName}
                        />
                      </Suspense>
                    ),
                  },
                ]}
              />
            </WorkspacePane>
          ) : isOpening ? (
            <WorkspacePane data-test="mdl-loading">
              <WorkspaceStrip>
                <Skeleton.Button active size="small" />
              </WorkspaceStrip>
              <SkeletonBody>
                <Skeleton active paragraph={{ rows: 8 }} />
              </SkeletonBody>
            </WorkspacePane>
          ) : isListLoading ? (
            // The project list is still loading, so nothing can be selected yet:
            // show a patience state here too rather than the "Select a project"
            // hint (which would wrongly imply the list is ready and empty).
            <EmptyWorkspace data-test="mdl-list-loading">
              <Flex vertical align="center" gap="small">
                <Icons.LoadingOutlined
                  iconSize="xl"
                  aria-label={t('Loading projects')}
                />
                <Typography.Text type="secondary">
                  {t('Loading projects…')}
                </Typography.Text>
              </Flex>
            </EmptyWorkspace>
          ) : (
            <EmptyWorkspace data-test="mdl-empty">
              {t('Select a project to open, or create one.')}
            </EmptyWorkspace>
          )}
        </Splitter.Panel>
      </EditorSplitter>
      <OnboardingTablePicker
        open={showOnboardPicker}
        databaseId={databaseId}
        catalogName={catalogName}
        schemas={project?.schema_names ?? [schemaName]}
        primarySchema={project?.schema_name ?? schemaName}
        canWrite={canWrite}
        onCancel={() => setShowOnboardPicker(false)}
        onConfirm={selection => {
          setShowOnboardPicker(false);
          if (project) {
            runOnboard(project.id, selection);
          }
        }}
      />
      <AutoOnboardModal
        open={showAutoOnboard}
        canWrite={canWrite}
        documents={documents}
        isUploading={isIngesting}
        onUpload={async files => (await ingest(files)).map(r => r.document)}
        onCancel={() => setShowAutoOnboard(false)}
        onConfirm={selected => {
          setShowAutoOnboard(false);
          if (!selected.length) return;
          // Make sure the rail is visible, then fire one kickstart turn.
          setShowCopilot(true);
          kickstartTokenRef.current += 1;
          setKickstart({
            token: kickstartTokenRef.current,
            message: AUTO_ONBOARD_MESSAGE,
            documents: selected,
          });
          // The uploaded docs now live in the workspace; refresh the tree.
          refresh();
        }}
      />
      <MdlProvenanceDialog
        open={showProvenance}
        projectId={project?.id ?? null}
        onClose={() => setShowProvenance(false)}
      />
      <ConfirmModal
        show={!!deleteTarget}
        onHide={() => setDeleteTarget(null)}
        onConfirm={handleDeleteConfirm}
        loading={isDeletingProject}
        confirmText={t('Delete')}
        confirmButtonStyle="danger"
        title={t('Delete project?')}
        body={t(
          'This permanently deletes “%(name)s” — its models, uploaded ' +
            'documents, and history. This cannot be undone.',
          { name: deleteTarget?.name ?? '' },
        )}
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
      <ConfirmModal
        show={!!renameTarget}
        onHide={() => setRenameTarget(null)}
        onConfirm={handleRenameSubmit}
        loading={isRenaming}
        confirmText={t('Rename')}
        title={t('Rename project')}
        body={
          <Input
            data-test="project-rename-input"
            value={renameValue}
            onChange={event => setRenameValue(event.target.value)}
            onPressEnter={handleRenameSubmit}
            placeholder={t('Project name')}
          />
        }
      />
      <NewProjectModal
        open={showNewProject}
        databaseId={databaseId}
        catalogName={catalogName}
        onSubmit={handleCreateSubmit}
        onCancel={() => setShowNewProject(false)}
        creating={isCreatingProject}
      />
      <ConfirmModal
        show={!!duplicateTarget}
        onHide={() => setDuplicateTarget(null)}
        onConfirm={handleDuplicateConfirm}
        loading={isDuplicating}
        confirmText={t('Duplicate')}
        title={t('Duplicate project')}
        body={
          <Flex vertical gap="small">
            <Typography.Text>
              {t(
                'Creates a copy of this project’s models and schema set with a ' +
                  'fresh history.',
              )}
            </Typography.Text>
            <Flex align="center" gap="small">
              <Switch
                size="small"
                checked={duplicateIncludeDocs}
                onChange={setDuplicateIncludeDocs}
                data-test="duplicate-include-documents"
                aria-label={t('Also copy uploaded documents')}
              />
              <Typography.Text>
                {t('Also copy uploaded documents')}
              </Typography.Text>
            </Flex>
          </Flex>
        }
      />
    </EditorRoot>
  );
}
