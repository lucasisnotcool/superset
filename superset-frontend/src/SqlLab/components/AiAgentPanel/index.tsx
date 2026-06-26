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
  KeyboardEvent,
  type ReactNode,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import { useSelector } from 'react-redux';
import { t } from '@apache-superset/core/translation';
import { Alert } from '@apache-superset/core/components';
import { css, keyframes, styled } from '@apache-superset/core/theme';
import {
  Button,
  Flex,
  Input,
  SafeMarkdown,
  Select,
  Tooltip,
  Typography,
  type SelectValue,
} from '@superset-ui/core/components';
import { Icons } from '@superset-ui/core/components/Icons';
import { extendedDayjs } from '@superset-ui/core/utils/dates';
import { useAppDispatch } from 'src/SqlLab/hooks/useAppDispatch';
import type { QueryEditor, SqlLabRootState } from 'src/SqlLab/types';
import { queryEditorSetSql } from 'src/SqlLab/actions/sqlLab';
import {
  addDangerToast,
  addInfoToast,
  addSuccessToast,
} from 'src/components/MessageToasts/actions';
import copyTextToClipboard from 'src/utils/copy';
import {
  AgentHealthResponse,
  Conversation,
  ConversationArtifact,
  ConversationScope,
  ConversationSummary,
  createConversation,
  deleteConversation,
  executeConversationSql,
  getAgentBaseUrl,
  getAgentHealth,
  getConversation,
  getProjectSemanticLayerState,
  listConversations,
  listSemanticProjects,
  sendConversationMessage,
  streamConversationMessage,
  streamExecuteConversationSql,
  StreamInterruptedError,
  StreamUnavailableError,
  updateConversationTitle,
  type AgentStep,
  type ConversationProgressEvent,
  type ConversationTurnResponse,
  type ExecutionMode,
  type SemanticLayerState,
} from './api';
import AiChartPreview from './AiChartPreview';
import AuditInfoPanel from './AuditInfoPanel';
import ExplainDialog from './ExplainDialog';
import DatasetSelect from './DatasetSelect';
import DataPreviewToggle from './DataPreviewToggle';
import MarkdownCodeBlock from './MarkdownCodeBlock';
import FollowupQuestions from './FollowupQuestions';
import InsightCards from './InsightCards';
import SemanticLayerStateBadge from './SemanticLayerStateBadge';

const Panel = styled.div`
  ${({ theme }) => css`
    display: flex;
    position: relative;
    flex-direction: column;
    height: 100%;
    min-height: 0;
    background: ${theme.colorBgBase};
    color: ${theme.colorText};
  `}
`;

const Header = styled.div`
  ${({ theme }) => css`
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: ${theme.sizeUnit * 2}px;
    padding: ${theme.sizeUnit * 3}px ${theme.sizeUnit * 3}px
      ${theme.sizeUnit * 2}px;
    border-bottom: 1px solid ${theme.colorBorderSecondary};
  `}
`;

const HeaderActions = styled.div`
  ${({ theme }) => css`
    display: flex;
    align-items: center;
    gap: ${theme.sizeUnit}px;
  `}
`;

const ContextBar = styled.div`
  ${({ theme }) => css`
    display: flex;
    flex-direction: column;
    gap: ${theme.sizeUnit * 2}px;
    padding: ${theme.sizeUnit * 2}px ${theme.sizeUnit * 3}px;
    border-bottom: 1px solid ${theme.colorBorderSecondary};
  `}
`;

const ContextChips = styled.div`
  ${({ theme }) => css`
    display: flex;
    flex-wrap: wrap;
    gap: ${theme.sizeUnit}px;
  `}
`;

const Chip = styled.span`
  ${({ theme }) => css`
    display: inline-flex;
    align-items: center;
    max-width: 100%;
    height: 24px;
    padding: 0 ${theme.sizeUnit * 2}px;
    border: 1px solid ${theme.colorBorder};
    border-radius: ${theme.borderRadius}px;
    color: ${theme.colorTextSecondary};
    background: ${theme.colorBgContainer};
    font-size: ${theme.fontSizeSM}px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  `}
`;

const HistoryPanel = styled.div`
  ${({ theme }) => css`
    display: flex;
    flex-direction: column;
    gap: ${theme.sizeUnit}px;
    max-height: 180px;
    overflow: auto;
  `}
`;

const HistoryButton = styled.button`
  ${({ theme }) => css`
    width: 100%;
    padding: ${theme.sizeUnit * 2}px;
    border: 1px solid ${theme.colorBorderSecondary};
    border-radius: ${theme.borderRadius}px;
    background: ${theme.colorBgContainer};
    color: ${theme.colorText};
    cursor: pointer;
    text-align: left;

    &:hover {
      border-color: ${theme.colorPrimary};
    }
  `}
`;

const TranscriptWrapper = styled.div`
  position: relative;
  display: flex;
  flex: 1;
  min-height: 0;
  flex-direction: column;
`;

const Transcript = styled.div`
  ${({ theme }) => css`
    display: flex;
    flex: 1;
    min-height: 0;
    flex-direction: column;
    gap: ${theme.sizeUnit * 3}px;
    padding: ${theme.sizeUnit * 3}px;
    overflow: auto;
  `}
`;

const JumpToLatestButton = styled.button`
  ${({ theme }) => css`
    position: absolute;
    left: 50%;
    bottom: ${theme.sizeUnit * 3}px;
    transform: translateX(-50%);
    z-index: 1;
    display: inline-flex;
    align-items: center;
    gap: ${theme.sizeUnit}px;
    padding: ${theme.sizeUnit}px ${theme.sizeUnit * 3}px;
    border: 1px solid ${theme.colorBorder};
    border-radius: ${theme.borderRadius * 4}px;
    background: ${theme.colorBgElevated};
    color: ${theme.colorText};
    box-shadow: ${theme.boxShadowSecondary};
    cursor: pointer;
    font-size: ${theme.fontSizeSM}px;

    &:hover {
      border-color: ${theme.colorPrimary};
      color: ${theme.colorPrimary};
    }
  `}
`;

const MessageBlock = styled.div<{ 'data-message-role': 'user' | 'assistant' }>`
  ${({ theme, 'data-message-role': messageRole }) => css`
    display: flex;
    flex-direction: column;
    gap: ${theme.sizeUnit * 2}px;
    align-self: ${messageRole === 'user' ? 'flex-end' : 'stretch'};
    max-width: ${messageRole === 'user' ? '92%' : '100%'};
  `}
`;

const MessageBubble = styled.div<{ 'data-message-role': 'user' | 'assistant' }>`
  ${({ theme, 'data-message-role': messageRole }) => css`
    padding: ${theme.sizeUnit * 2}px ${theme.sizeUnit * 3}px;
    border: 1px solid
      ${messageRole === 'user'
        ? theme.colorPrimaryBorder
        : theme.colorBorderSecondary};
    border-radius: ${theme.borderRadius}px;
    background: ${messageRole === 'user'
      ? theme.colorPrimaryBg
      : theme.colorBgContainer};
    white-space: pre-wrap;
    overflow-wrap: anywhere;
    line-height: 1.5;
  `}
`;

const MessageMeta = styled.div<{ 'data-message-role': 'user' | 'assistant' }>`
  ${({ theme, 'data-message-role': messageRole }) => css`
    display: flex;
    align-items: center;
    gap: ${theme.sizeUnit}px;
    justify-content: ${messageRole === 'user' ? 'flex-end' : 'flex-start'};
    color: ${theme.colorTextTertiary};
    font-size: ${theme.fontSizeSM}px;
    /* Reveal the action buttons on hover/focus to keep the transcript calm. */
    .message-actions {
      opacity: 0;
      transition: opacity 0.15s ease;
    }
    &:hover .message-actions,
    &:focus-within .message-actions {
      opacity: 1;
    }
  `}
`;

const MarkdownContent = styled.div`
  ${({ theme }) => css`
    white-space: normal;
    overflow-wrap: anywhere;

    p,
    ul,
    ol,
    pre,
    blockquote,
    table {
      margin: 0 0 ${theme.sizeUnit * 2}px;
    }
    > :last-child {
      margin-bottom: 0;
    }
    ul,
    ol {
      padding-left: ${theme.sizeUnit * 4}px;
    }
    code {
      padding: 0 ${theme.sizeUnit}px;
      border-radius: ${theme.borderRadiusSM}px;
      background: ${theme.colorBgElevated};
      font-size: ${theme.fontSizeSM}px;
    }
    pre {
      padding: ${theme.sizeUnit * 2}px;
      overflow: auto;
      background: ${theme.colorBgElevated};
      border-radius: ${theme.borderRadius}px;
    }
    pre code {
      padding: 0;
      background: transparent;
    }
  `}
`;

const ArtifactBlock = styled.div`
  ${({ theme }) => css`
    display: flex;
    flex-direction: column;
    gap: ${theme.sizeUnit * 2}px;
    padding: ${theme.sizeUnit * 2}px;
    border: 1px solid ${theme.colorBorder};
    border-radius: ${theme.borderRadius}px;
    background: ${theme.colorBgContainer};
  `}
`;

const SqlBlockRow = styled.div`
  ${({ theme }) => css`
    display: flex;
    align-items: flex-start;
    gap: ${theme.sizeUnit * 2}px;
    min-width: 0;
  `}
`;

const SqlBlock = styled.pre`
  ${({ theme }) => css`
    flex: 1;
    min-width: 0;
    margin: 0;
    padding: ${theme.sizeUnit * 2}px;
    max-height: 240px;
    overflow: auto;
    white-space: pre-wrap;
    word-break: break-word;
    background: ${theme.colorBgElevated};
    border: 1px solid ${theme.colorBorderSecondary};
    border-radius: ${theme.borderRadius}px;
    font-size: ${theme.fontSizeSM}px;
    line-height: 1.5;
  `}
`;

const ValidationStatus = styled.span<{
  'data-validation-status': 'valid' | 'invalid' | 'unknown';
}>`
  ${({ theme, 'data-validation-status': validationStatus }) => css`
    display: inline-flex;
    align-items: center;
    justify-content: center;
    flex: 0 0 auto;
    width: ${theme.sizeUnit * 6}px;
    height: ${theme.sizeUnit * 6}px;
    margin-top: ${theme.sizeUnit}px;
    color: ${validationStatus === 'valid'
      ? theme.colorSuccess
      : validationStatus === 'invalid'
        ? theme.colorError
        : theme.colorTextSecondary};
  `}
`;

const Composer = styled.div`
  ${({ theme }) => css`
    display: flex;
    flex-direction: column;
    gap: ${theme.sizeUnit * 2}px;
    padding: ${theme.sizeUnit * 3}px;
    border-top: 1px solid ${theme.colorBorderSecondary};
    background: ${theme.colorBgBase};
  `}
`;

const pulseGlow = keyframes`
  0% {
    opacity: 0.9;
  }
  50% {
    opacity: 0.25;
  }
  100% {
    opacity: 0.9;
  }
`;

const ComposerInput = styled.div<{ 'data-loading'?: boolean }>`
  ${({ theme, 'data-loading': loading }) => css`
    position: relative;
    border-radius: ${theme.borderRadius}px;

    /* A soft glowing ring shown only while the agent is working. Rather than
       spinning, the glow pulses its opacity (high -> low -> high). The textarea
       sits on top with an opaque background, so only the 2px overhang reads as a
       glowing border. */
    &::before {
      content: '';
      position: absolute;
      inset: -2px;
      border-radius: ${theme.borderRadius + 2}px;
      background: ${theme.colorPrimary};
      filter: blur(2px);
      opacity: ${loading ? 0.9 : 0};
      animation: ${pulseGlow} 1.6s ease-in-out infinite;
      animation-play-state: ${loading ? 'running' : 'paused'};
      transition: opacity 0.2s ease;
      pointer-events: none;
      z-index: 0;
    }

    .ant-input,
    textarea {
      position: relative;
      z-index: 1;
    }
  `}
`;

const ProgressBubble = styled.div`
  ${({ theme }) => css`
    display: inline-flex;
    align-items: center;
    gap: ${theme.sizeUnit * 2}px;
    padding: ${theme.sizeUnit * 2}px ${theme.sizeUnit * 3}px;
    border: 1px solid ${theme.colorBorderSecondary};
    border-radius: ${theme.borderRadius}px;
    background: ${theme.colorBgContainer};
    color: ${theme.colorTextSecondary};
    line-height: 1.5;
  `}
`;

const ExecutionModeControl = styled.div`
  ${() => css`
    min-width: 176px;

    .ant-select {
      width: 100%;
    }

    @media (max-width: 480px) {
      flex: 1 1 100%;
      min-width: 0;
    }
  `}
`;

const MetaText = styled(Typography.Text)`
  ${({ theme }) => css`
    color: ${theme.colorTextSecondary};
    font-size: ${theme.fontSizeSM}px;
    overflow-wrap: anywhere;
  `}
`;

const getActiveQueryEditor = ({
  sqlLab: {
    queryEditors,
    tabHistory,
    unsavedQueryEditor,
    lastUpdatedActiveTab,
  },
}: SqlLabRootState): Partial<QueryEditor> | undefined => {
  const activeId = lastUpdatedActiveTab || tabHistory.slice(-1)[0];
  const persisted = queryEditors.find(
    queryEditor => queryEditor.id === activeId,
  );
  // Schema/catalog/sql edits (e.g. the left-bar schema switcher) are written to
  // `unsavedQueryEditor` before they are flushed to `queryEditors`. Merge the
  // pending edits over the persisted editor so the AI panel's scope follows the
  // currently selected schema instead of a stale one.
  const pending =
    unsavedQueryEditor.id === activeId ? unsavedQueryEditor : undefined;
  if (!persisted) {
    return pending;
  }
  return pending ? { ...persisted, ...pending } : persisted;
};

const buildConversationScope = (
  queryEditor: Partial<QueryEditor> | undefined,
  databaseId: number,
  datasetIds: number[],
): ConversationScope => ({
  database_id: databaseId,
  catalog_name: queryEditor?.catalog || null,
  schema_name: queryEditor?.schema || null,
  dataset_ids: datasetIds,
  query_editor_id: queryEditor?.id || null,
  current_sql: queryEditor?.sql || null,
  selected_text: queryEditor?.selectedText || null,
});

// Custom renderers for assistant markdown: fenced code is highlighted with a
// copy button, and the default `pre` wrapper is dropped so the highlighter owns
// the block layout.
const markdownComponents = {
  code: MarkdownCodeBlock,
  pre: ({ children }: { children?: ReactNode }) => <>{children}</>,
};

const executionModes: ExecutionMode[] = ['manual', 'read_only', 'auto'];

const isExecutionMode = (value: SelectValue): value is ExecutionMode =>
  typeof value === 'string' && executionModes.includes(value as ExecutionMode);

// Persist the chosen execution mode per conversation so reopening a chat keeps
// its approval setting; new chats inherit the last-used default.
const EXEC_MODE_STORAGE_PREFIX = 'sqllab:ai-agent:exec-mode:';

const execModeStorageKey = (conversationId?: string) =>
  `${EXEC_MODE_STORAGE_PREFIX}${conversationId ?? 'default'}`;

const loadExecutionMode = (conversationId?: string): ExecutionMode | null => {
  try {
    const stored = window.localStorage.getItem(
      execModeStorageKey(conversationId),
    );
    return isExecutionMode(stored as SelectValue)
      ? (stored as ExecutionMode)
      : null;
  } catch {
    return null;
  }
};

const storeExecutionMode = (
  conversationId: string | undefined,
  mode: ExecutionMode,
) => {
  try {
    window.localStorage.setItem(execModeStorageKey(conversationId), mode);
  } catch {
    // Ignore storage failures (private mode, quota, disabled storage).
  }
};

const getValidationStatus = (artifact: ConversationArtifact) => {
  if (!artifact.validation) {
    return 'unknown' as const;
  }
  return artifact.validation.is_valid
    ? ('valid' as const)
    : ('invalid' as const);
};

const getValidationTooltip = (artifact: ConversationArtifact) => {
  if (!artifact.validation) {
    return t('SQL validation status is unavailable.');
  }
  if (artifact.validation.is_valid) {
    return t('SQL is valid.');
  }
  return artifact.validation.errors.length
    ? artifact.validation.errors.join('\n')
    : t('SQL is invalid.');
};

// Treat the transcript as "pinned" within a small slack of the bottom so the
// auto-scroll keeps following new content unless the user scrolls up to read.
const SCROLL_PIN_THRESHOLD_PX = 40;

const isScrolledToBottom = (el: HTMLElement) =>
  el.scrollHeight - el.scrollTop - el.clientHeight <= SCROLL_PIN_THRESHOLD_PX;

const isAbortError = (ex: unknown): boolean =>
  ex instanceof DOMException
    ? ex.name === 'AbortError'
    : ex instanceof Error && ex.name === 'AbortError';

const normalizeSql = (sql: string | null | undefined) =>
  (sql || '').trim().replace(/;+$/, '').replace(/\s+/g, ' ');

// The turn-level trace is cumulative: when the agent retries, a freshly drafted
// retry artifact inherits the failed `execute_sql` event from the earlier
// attempt. Attribute the error only when its recorded SQL matches this
// artifact's own SQL, so a never-executed retry draft is not treated as failed.
const getExecutionError = (artifact: ConversationArtifact) => {
  const artifactSqlKeys = new Set(
    [artifact.sql, artifact.validation?.normalized_sql]
      .map(normalizeSql)
      .filter(Boolean),
  );
  const executionEvent = artifact.trace.find(event => {
    if (event.step !== 'execute_sql' || event.status !== 'error') {
      return false;
    }
    const eventSql = event.details.sql;
    // Older trace events may omit the SQL; fall back to attributing the error
    // only when the artifact itself was never executed successfully.
    if (typeof eventSql !== 'string') {
      return !artifact.execution_result;
    }
    return artifactSqlKeys.has(normalizeSql(eventSql));
  });
  if (!executionEvent) {
    return null;
  }
  const detailError = executionEvent.details.error;
  return typeof detailError === 'string' ? detailError : executionEvent.summary;
};

const AiAgentPanel = () => {
  const dispatch = useAppDispatch();
  const queryEditor = useSelector(getActiveQueryEditor);
  const transcriptRef = useRef<HTMLDivElement | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const [composerValue, setComposerValue] = useState('');
  const [datasetIds, setDatasetIds] = useState<number[]>([]);
  const [executionMode, setExecutionMode] = useState<ExecutionMode>(
    () => loadExecutionMode() ?? 'manual',
  );
  const [isLoading, setIsLoading] = useState(false);
  const [conversation, setConversation] = useState<Conversation | null>(null);
  const [conversationSummaries, setConversationSummaries] = useState<
    ConversationSummary[]
  >([]);
  const [isHistoryOpen, setIsHistoryOpen] = useState(false);
  const [health, setHealth] = useState<AgentHealthResponse | null>(null);
  const [isHealthError, setIsHealthError] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pendingUserMessage, setPendingUserMessage] = useState<string | null>(
    null,
  );
  const [progress, setProgress] = useState<string | null>(null);
  // Steps streamed during the in-flight turn, so the explain dialog can fill its
  // sequence live (ai_agent_explain_and_audit.md F2).
  const [liveSteps, setLiveSteps] = useState<AgentStep[]>([]);
  // The explain-and-audit dialog target: the user message + its step timeline.
  const [explainTarget, setExplainTarget] = useState<{
    message: string;
    steps: AgentStep[];
    live?: boolean;
  } | null>(null);
  const [isPinnedToBottom, setIsPinnedToBottom] = useState(true);
  const [feedback, setFeedback] = useState<Record<string, 'up' | 'down'>>({});
  const [semanticLayerState, setSemanticLayerState] =
    useState<SemanticLayerState | null>(null);

  const databaseId = queryEditor?.dbId;
  const currentScope = useMemo(
    () =>
      typeof databaseId === 'number'
        ? buildConversationScope(queryEditor, databaseId, datasetIds)
        : null,
    [databaseId, datasetIds, queryEditor],
  );
  const canSend =
    Boolean(composerValue.trim()) && typeof databaseId === 'number';
  const healthLabel = isHealthError
    ? t('offline')
    : health?.status || t('local');
  const messages = conversation?.messages || [];
  const executionModeOptions = useMemo(
    () => [
      { value: 'manual', label: t('Manual approval') },
      { value: 'read_only', label: t('Read-only queries') },
      { value: 'auto', label: t('Auto approve') },
    ],
    [],
  );

  useEffect(() => {
    let isMounted = true;

    getAgentHealth()
      .then(result => {
        if (isMounted) {
          setHealth(result);
          setIsHealthError(false);
        }
      })
      .catch(() => {
        if (isMounted) {
          setIsHealthError(true);
        }
      });

    listConversations()
      .then(result => {
        if (isMounted) {
          setConversationSummaries(result);
        }
      })
      .catch(() => {
        if (isMounted) {
          setConversationSummaries([]);
        }
      });

    return () => {
      isMounted = false;
    };
  }, []);

  useEffect(() => {
    if (!currentScope?.schema_name) {
      setSemanticLayerState(null);
      return undefined;
    }
    let isMounted = true;
    // Probe for an existing schema-scoped project without creating one. Using the
    // listing endpoint (200 + possibly-empty array) instead of resolve avoids a
    // spurious 404 in the console when no project exists yet for the schema.
    listSemanticProjects(
      currentScope.database_id,
      currentScope.catalog_name ?? null,
      currentScope.schema_name,
    )
      .then(projects => {
        const project = projects[0];
        if (!project) {
          if (isMounted) {
            setSemanticLayerState(null);
          }
          return undefined;
        }
        return getProjectSemanticLayerState(project.id).then(nextState => {
          if (isMounted) {
            setSemanticLayerState(nextState);
          }
        });
      })
      .catch(() => {
        if (isMounted) {
          setSemanticLayerState(null);
        }
      });
    return () => {
      isMounted = false;
    };
  }, [
    currentScope?.database_id,
    currentScope?.catalog_name,
    currentScope?.schema_name,
  ]);

  useEffect(() => {
    if (isPinnedToBottom && transcriptRef.current) {
      transcriptRef.current.scrollTop = transcriptRef.current.scrollHeight;
    }
  }, [
    messages.length,
    isLoading,
    pendingUserMessage,
    progress,
    isPinnedToBottom,
  ]);

  const onTranscriptScroll = () => {
    if (transcriptRef.current) {
      setIsPinnedToBottom(isScrolledToBottom(transcriptRef.current));
    }
  };

  const jumpToLatest = () => {
    if (transcriptRef.current) {
      transcriptRef.current.scrollTop = transcriptRef.current.scrollHeight;
    }
    setIsPinnedToBottom(true);
  };

  const refreshConversationSummaries = () =>
    listConversations()
      .then(setConversationSummaries)
      .catch(() => setConversationSummaries([]));

  // Best-effort resync of the transcript with the server (e.g. after the user
  // cancels a turn) without surfacing errors of its own.
  const reloadConversation = async (conversationId?: string) => {
    if (!conversationId) {
      return;
    }
    try {
      setConversation(await getConversation(conversationId));
      await refreshConversationSummaries();
    } catch {
      // Leave the current state in place if the resync fails.
    }
  };

  // Centralised handling for a failed turn: cancellations and dropped streams
  // resync silently/with a notice; everything else surfaces as an error.
  const handleTurnError = async (
    ex: unknown,
    conversationId: string | undefined,
    fallbackMessage: string,
  ) => {
    if (isAbortError(ex)) {
      await reloadConversation(conversationId);
      return;
    }
    if (ex instanceof StreamInterruptedError) {
      dispatch(
        addInfoToast(t('Connection interrupted — reloading the latest reply.')),
      );
      await reloadConversation(conversationId);
      return;
    }
    const messageText = ex instanceof Error ? ex.message : fallbackMessage;
    setError(messageText);
    dispatch(addDangerToast(messageText));
  };

  const ensureConversation = async (scope: ConversationScope) => {
    if (conversation) {
      return conversation;
    }
    const createdConversation = await createConversation(scope);
    setConversation(createdConversation);
    setDatasetIds(createdConversation.scope.dataset_ids);
    // Carry the current default mode onto the freshly created conversation.
    storeExecutionMode(createdConversation.id, executionMode);
    await refreshConversationSummaries();
    return createdConversation;
  };

  const onExecutionModeChange = (value: SelectValue) => {
    if (isExecutionMode(value)) {
      setExecutionMode(value);
      storeExecutionMode(conversation?.id, value);
    }
  };

  const onProgressUpdate = (event: ConversationProgressEvent) => {
    setProgress(event.summary);
    if (event.agent_step) {
      const { agent_step: step } = event;
      setLiveSteps(prev => [...prev, step]);
    }
  };

  // Run a turn over the streaming endpoint, falling back to the buffered request
  // only when streaming never started (StreamUnavailableError). A mid-stream
  // failure means the turn was already sent, so it must not be retried.
  const runConversationTurn = async (
    conversationId: string,
    payload: Parameters<typeof sendConversationMessage>[1],
    signal: AbortSignal,
  ): Promise<ConversationTurnResponse> => {
    try {
      return await streamConversationMessage(conversationId, payload, {
        onProgress: onProgressUpdate,
        signal,
      });
    } catch (ex) {
      if (ex instanceof StreamUnavailableError) {
        return sendConversationMessage(conversationId, payload);
      }
      throw ex;
    }
  };

  const onCancel = () => abortRef.current?.abort();

  const onSend = async (messageOverride?: string) => {
    const message = (messageOverride || composerValue).trim();
    if (!message || typeof databaseId !== 'number' || !currentScope) {
      return;
    }
    const controller = new AbortController();
    abortRef.current = controller;
    setIsLoading(true);
    setError(null);
    setProgress(null);
    setLiveSteps([]);
    // Show the user's message immediately instead of a placeholder while the
    // agent works; it is reconciled once the turn response arrives.
    setPendingUserMessage(message);
    let activeConversationId: string | undefined;
    try {
      const activeConversation = await ensureConversation(currentScope);
      activeConversationId = activeConversation.id;
      if (!messageOverride) {
        setComposerValue('');
      }
      const result = await runConversationTurn(
        activeConversation.id,
        {
          message,
          scope: currentScope,
          execution_mode: executionMode,
        },
        controller.signal,
      );
      setConversation(result.conversation);
      setDatasetIds(result.conversation.scope.dataset_ids);
      await refreshConversationSummaries();
      if (result.status === 'error') {
        dispatch(addDangerToast(t('The agent could not complete the turn.')));
      }
    } catch (ex) {
      await handleTurnError(
        ex,
        activeConversationId,
        t('Agent request failed'),
      );
    } finally {
      abortRef.current = null;
      setIsLoading(false);
      setProgress(null);
      setPendingUserMessage(null);
    }
  };

  const onComposerKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      onSend();
    }
  };

  const onNewConversation = () => {
    setConversation(null);
    setComposerValue('');
    setError(null);
    setDatasetIds([]);
    setExecutionMode(loadExecutionMode() ?? 'manual');
  };

  const onOpenConversation = async (conversationId: string) => {
    try {
      const result = await getConversation(conversationId);
      setConversation(result);
      setDatasetIds(result.scope.dataset_ids);
      setExecutionMode(
        loadExecutionMode(result.id) ?? loadExecutionMode() ?? 'manual',
      );
      setIsHistoryOpen(false);
    } catch (ex) {
      const messageText =
        ex instanceof Error
          ? ex.message
          : t('Conversation could not be loaded');
      dispatch(addDangerToast(messageText));
    }
  };

  const onDeleteConversation = async () => {
    if (!conversation) {
      return;
    }
    try {
      await deleteConversation(conversation.id);
      onNewConversation();
      await refreshConversationSummaries();
    } catch (ex) {
      const messageText =
        ex instanceof Error
          ? ex.message
          : t('Conversation could not be deleted');
      dispatch(addDangerToast(messageText));
    }
  };

  const onInsertSql = (sql: string) => {
    if (!sql || !queryEditor?.id) {
      return;
    }
    dispatch(queryEditorSetSql(queryEditor, sql));
    dispatch(addSuccessToast(t('SQL inserted into editor.')));
  };

  const onCopySql = (sql: string) => {
    if (!sql) {
      return;
    }
    copyTextToClipboard(() => Promise.resolve(sql))
      .then(() => dispatch(addSuccessToast(t('SQL copied.'))))
      .catch(() => dispatch(addInfoToast(t('Copy failed.'))));
  };

  const onCopyMessage = (content: string) => {
    if (!content) {
      return;
    }
    copyTextToClipboard(() => Promise.resolve(content))
      .then(() => dispatch(addSuccessToast(t('Message copied.'))))
      .catch(() => dispatch(addInfoToast(t('Copy failed.'))));
  };

  // Re-run the most recent user prompt to produce a fresh assistant turn.
  const onRegenerate = () => {
    if (isLoading) {
      return;
    }
    const lastUserMessage = [...messages]
      .reverse()
      .find(message => message.role === 'user');
    if (lastUserMessage?.content) {
      onSend(lastUserMessage.content);
    }
  };

  // Lightweight feedback hook: records the rating locally and acknowledges it.
  // A backend feedback endpoint can later consume this signal.
  const onFeedback = (messageId: string, rating: 'up' | 'down') => {
    setFeedback(previous => {
      const next = { ...previous };
      if (next[messageId] === rating) {
        delete next[messageId];
      } else {
        next[messageId] = rating;
      }
      return next;
    });
    dispatch(addInfoToast(t('Thanks for the feedback.')));
  };

  const onRenameConversation = async (title: string) => {
    const trimmed = title.trim();
    if (!conversation || !trimmed || trimmed === conversation.title) {
      return;
    }
    try {
      const updated = await updateConversationTitle(conversation.id, trimmed);
      setConversation(updated);
      await refreshConversationSummaries();
    } catch (ex) {
      dispatch(
        addDangerToast(
          ex instanceof Error
            ? ex.message
            : t('Conversation could not be renamed'),
        ),
      );
    }
  };

  const onExecuteArtifact = async (artifact: ConversationArtifact) => {
    if (
      !conversation ||
      !artifact.sql ||
      typeof databaseId !== 'number' ||
      !currentScope
    ) {
      return;
    }
    if (!artifact.validation?.is_valid || !artifact.validation.is_read_only) {
      dispatch(addDangerToast(getValidationTooltip(artifact)));
      return;
    }
    const conversationId = conversation.id;
    const payload = {
      sql: artifact.validation.normalized_sql || artifact.sql,
      scope: currentScope,
      execution_mode: executionMode,
      artifact_id: artifact.id,
    };
    const controller = new AbortController();
    abortRef.current = controller;
    setIsLoading(true);
    setError(null);
    setProgress(null);
    try {
      let result: ConversationTurnResponse;
      try {
        result = await streamExecuteConversationSql(conversationId, payload, {
          onProgress: onProgressUpdate,
          signal: controller.signal,
        });
      } catch (ex) {
        if (ex instanceof StreamUnavailableError) {
          result = await executeConversationSql(conversationId, payload);
        } else {
          throw ex;
        }
      }
      setConversation(result.conversation);
      await refreshConversationSummaries();
      if (result.status === 'error') {
        dispatch(addDangerToast(t('The agent could not execute SQL.')));
      }
    } catch (ex) {
      await handleTurnError(ex, conversationId, t('SQL execution failed'));
    } finally {
      abortRef.current = null;
      setIsLoading(false);
      setProgress(null);
    }
  };

  return (
    <Panel data-test="sql-lab-ai-agent-panel">
      <Header>
        <Flex vertical gap={0} style={{ minWidth: 0 }}>
          {conversation ? (
            <Typography.Title
              level={5}
              style={{ margin: 0 }}
              ellipsis={{ tooltip: conversation.title }}
              editable={{
                onChange: onRenameConversation,
                tooltip: t('Rename conversation'),
                maxLength: 255,
              }}
            >
              {conversation.title}
            </Typography.Title>
          ) : (
            <Typography.Title level={5} style={{ margin: 0 }}>
              {t('AI SQL')}
            </Typography.Title>
          )}
          <Tooltip title={getAgentBaseUrl()}>
            <MetaText>{healthLabel}</MetaText>
          </Tooltip>
        </Flex>
        <HeaderActions>
          <Button
            aria-label={t('New conversation')}
            tooltip={t('New conversation')}
            buttonSize="small"
            buttonStyle="tertiary"
            onClick={onNewConversation}
            icon={<Icons.PlusOutlined iconSize="m" />}
          />
          <Button
            aria-label={t('Conversation history')}
            tooltip={t('Conversation history')}
            buttonSize="small"
            buttonStyle="tertiary"
            onClick={() => setIsHistoryOpen(!isHistoryOpen)}
            icon={<Icons.HistoryOutlined iconSize="m" />}
          />
          <Button
            aria-label={t('Delete conversation')}
            tooltip={t('Delete conversation')}
            buttonSize="small"
            buttonStyle="tertiary"
            disabled={!conversation}
            onClick={onDeleteConversation}
            icon={<Icons.DeleteOutlined iconSize="m" />}
          />
        </HeaderActions>
      </Header>

      <ContextBar>
        <ContextChips>
          <Chip>
            {typeof databaseId === 'number'
              ? t('Database %s', databaseId)
              : t('Select a database')}
          </Chip>
          {queryEditor?.schema && <Chip>{queryEditor.schema}</Chip>}
          {queryEditor?.catalog && <Chip>{queryEditor.catalog}</Chip>}
          {queryEditor?.selectedText && <Chip>{t('Selection')}</Chip>}
          <SemanticLayerStateBadge state={semanticLayerState} />
        </ContextChips>
        {health?.semantic_layer_persistent === false && (
          <Alert
            type="warning"
            closable={false}
            message={t(
              'Semantic models are not persisted — they are kept in memory and ' +
                'lost on restart. Set the semantic layer store to a database to ' +
                'keep them.',
            )}
          />
        )}
        {health?.vector_index === 'memory_fallback' && (
          <Alert
            type="warning"
            closable={false}
            message={t(
              'LanceDB was requested but did not load — embedding retrieval is ' +
                'running in memory and its index is not persisted. Install ' +
                'lancedb or set the vector index to memory.',
            )}
          />
        )}
        <DatasetSelect
          databaseId={databaseId}
          schema={queryEditor?.schema}
          value={datasetIds}
          onChange={setDatasetIds}
        />
        {isHistoryOpen && (
          <HistoryPanel>
            {conversationSummaries.map(summary => (
              <HistoryButton
                key={summary.id}
                type="button"
                onClick={() => onOpenConversation(summary.id)}
              >
                <Typography.Text strong>{summary.title}</Typography.Text>
                {summary.last_message && (
                  <MetaText>{summary.last_message}</MetaText>
                )}
              </HistoryButton>
            ))}
          </HistoryPanel>
        )}
      </ContextBar>

      <TranscriptWrapper>
        <Transcript
          ref={transcriptRef}
          onScroll={onTranscriptScroll}
          data-test="agent-transcript"
        >
          {messages.map((message, messageIndex) => {
            const isLastMessage = messageIndex === messages.length - 1;
            const renderArtifactsBeforeMessage =
              message.role === 'assistant' &&
              message.artifacts.some(artifact => artifact.execution_result) &&
              !message.artifacts.some(artifact =>
                artifact.trace.some(event => event.step === 'approved_sql'),
              );
            const artifactBlocks = message.artifacts.map((artifact, index) => {
              const validationStatus = getValidationStatus(artifact);
              const executionError = getExecutionError(artifact);
              const isExecuted = Boolean(artifact.execution_result);
              // A successful execution does not lock the button — re-running is
              // allowed and the label/icon change is what signals the prior run.
              const canExecuteArtifact =
                Boolean(artifact.sql) &&
                Boolean(conversation) &&
                !executionError &&
                artifact.validation?.is_valid === true &&
                artifact.validation?.is_read_only === true;

              return (
                <ArtifactBlock key={`${message.id}-${artifact.type}-${index}`}>
                  {artifact.answer_summary && (
                    <Typography.Text strong>
                      {artifact.answer_summary}
                    </Typography.Text>
                  )}
                  <InsightCards cards={artifact.insight_cards} />
                  {artifact.explanation && (
                    <MetaText>{artifact.explanation}</MetaText>
                  )}
                  <AiChartPreview
                    chartSpec={artifact.chart_spec}
                    result={artifact.execution_result || artifact.data_preview}
                  />
                  <SqlBlockRow>
                    <SqlBlock>{artifact.sql}</SqlBlock>
                    <Tooltip title={getValidationTooltip(artifact)}>
                      <ValidationStatus
                        data-validation-status={validationStatus}
                      >
                        {validationStatus === 'valid' ? (
                          <Icons.CheckCircleOutlined iconSize="m" />
                        ) : validationStatus === 'invalid' ? (
                          <Icons.CloseCircleOutlined iconSize="m" />
                        ) : (
                          <Icons.InfoCircleOutlined iconSize="m" />
                        )}
                      </ValidationStatus>
                    </Tooltip>
                  </SqlBlockRow>
                  {artifact.validation?.errors.length ? (
                    <Alert
                      type="warning"
                      message={artifact.validation.errors.join('\n')}
                    />
                  ) : null}
                  {executionError ? (
                    <Alert type="warning" message={executionError} />
                  ) : null}
                  <DataPreviewToggle
                    result={artifact.execution_result || artifact.data_preview}
                  />
                  <AuditInfoPanel
                    audit={artifact.audit || artifact.execution_result?.audit}
                    wrenContext={artifact.wren_context}
                  />
                  <Flex gap="small" wrap="wrap">
                    <Button
                      aria-label={t('Insert')}
                      buttonStyle="tertiary"
                      onClick={() => onInsertSql(artifact.sql ?? '')}
                      disabled={!artifact.sql || !queryEditor?.id}
                      icon={<Icons.EditOutlined iconSize="m" />}
                    >
                      {t('Insert')}
                    </Button>
                    <Button
                      aria-label={t('Copy')}
                      buttonStyle="tertiary"
                      onClick={() => onCopySql(artifact.sql ?? '')}
                      disabled={!artifact.sql}
                      icon={<Icons.CopyOutlined iconSize="m" />}
                    >
                      {t('Copy')}
                    </Button>
                    <Button
                      aria-label={isExecuted ? t('Executed') : t('Execute')}
                      buttonStyle={isExecuted ? 'secondary' : 'tertiary'}
                      onClick={() => onExecuteArtifact(artifact)}
                      disabled={!canExecuteArtifact || isLoading}
                      icon={
                        isExecuted ? (
                          <Icons.CheckCircleOutlined iconSize="m" />
                        ) : (
                          <Icons.PlayCircleOutlined iconSize="m" />
                        )
                      }
                    >
                      {isExecuted ? t('Executed') : t('Execute')}
                    </Button>
                  </Flex>
                  <FollowupQuestions
                    questions={artifact.recommended_followups}
                    disabled={isLoading}
                    onSelect={question => onSend(question)}
                  />
                  {(artifact.timeline?.length || artifact.trace.length) > 0 && (
                    <Button
                      aria-label={t('Explain')}
                      buttonStyle="link"
                      onClick={() =>
                        setExplainTarget({
                          message:
                            messages
                              .slice(0, messageIndex)
                              .reverse()
                              .find(item => item.role === 'user')?.content ||
                            '',
                          steps: artifact.timeline ?? [],
                        })
                      }
                      icon={<Icons.InfoCircleOutlined iconSize="m" />}
                    >
                      {t('Explain')}
                    </Button>
                  )}
                </ArtifactBlock>
              );
            });

            return (
              <MessageBlock key={message.id} data-message-role={message.role}>
                {renderArtifactsBeforeMessage && artifactBlocks}
                {message.content.trim() && (
                  <MessageBubble data-message-role={message.role}>
                    {message.role === 'assistant' ? (
                      <MarkdownContent>
                        <SafeMarkdown
                          source={message.content}
                          components={markdownComponents}
                        />
                      </MarkdownContent>
                    ) : (
                      message.content
                    )}
                  </MessageBubble>
                )}
                {!renderArtifactsBeforeMessage && artifactBlocks}
                <MessageMeta data-message-role={message.role}>
                  <Tooltip
                    title={extendedDayjs(message.created_at).format('LLL')}
                  >
                    <span>{extendedDayjs(message.created_at).fromNow()}</span>
                  </Tooltip>
                  <Flex className="message-actions" align="center" gap={0}>
                    {message.content.trim() && (
                      <Button
                        aria-label={t('Copy message')}
                        tooltip={t('Copy message')}
                        buttonSize="small"
                        buttonStyle="link"
                        onClick={() => onCopyMessage(message.content)}
                        icon={<Icons.CopyOutlined iconSize="s" />}
                      />
                    )}
                    {message.role === 'assistant' && (
                      <>
                        <Button
                          aria-label={t('Good response')}
                          tooltip={t('Good response')}
                          buttonSize="small"
                          buttonStyle="link"
                          onClick={() => onFeedback(message.id, 'up')}
                          icon={
                            feedback[message.id] === 'up' ? (
                              <Icons.LikeFilled iconSize="s" />
                            ) : (
                              <Icons.LikeOutlined iconSize="s" />
                            )
                          }
                        />
                        <Button
                          aria-label={t('Bad response')}
                          tooltip={t('Bad response')}
                          buttonSize="small"
                          buttonStyle="link"
                          onClick={() => onFeedback(message.id, 'down')}
                          icon={
                            feedback[message.id] === 'down' ? (
                              <Icons.DislikeFilled iconSize="s" />
                            ) : (
                              <Icons.DislikeOutlined iconSize="s" />
                            )
                          }
                        />
                        {isLastMessage && (
                          <Button
                            aria-label={t('Regenerate')}
                            tooltip={t('Regenerate response')}
                            buttonSize="small"
                            buttonStyle="link"
                            disabled={isLoading}
                            onClick={onRegenerate}
                            icon={<Icons.ReloadOutlined iconSize="s" />}
                          />
                        )}
                      </>
                    )}
                  </Flex>
                </MessageMeta>
              </MessageBlock>
            );
          })}
          {pendingUserMessage && (
            <MessageBlock data-message-role="user">
              <MessageBubble data-message-role="user">
                {pendingUserMessage}
              </MessageBubble>
            </MessageBlock>
          )}
          {isLoading && (
            <MessageBlock data-message-role="assistant">
              <ProgressBubble data-test="agent-progress">
                <Icons.LoadingOutlined iconSize="m" spin />
                {progress || t('Working…')}
                {liveSteps.length > 0 && (
                  <Button
                    aria-label={t('Explain')}
                    buttonStyle="link"
                    onClick={() =>
                      setExplainTarget({
                        message: pendingUserMessage || '',
                        steps: [],
                        live: true,
                      })
                    }
                    icon={<Icons.InfoCircleOutlined iconSize="s" />}
                  >
                    {t('Explain')}
                  </Button>
                )}
              </ProgressBubble>
            </MessageBlock>
          )}
          {error && <Alert type="error" message={error} />}
        </Transcript>
        {!isPinnedToBottom && (
          <JumpToLatestButton
            type="button"
            onClick={jumpToLatest}
            data-test="jump-to-latest"
          >
            <Icons.DownOutlined iconSize="s" />
            {t('Jump to latest')}
          </JumpToLatestButton>
        )}
      </TranscriptWrapper>

      <ExplainDialog
        open={Boolean(explainTarget)}
        onClose={() => setExplainTarget(null)}
        userMessage={explainTarget?.message}
        steps={explainTarget?.live ? liveSteps : (explainTarget?.steps ?? [])}
      />

      <Composer>
        <ComposerInput data-loading={isLoading}>
          <Input.TextArea
            rows={3}
            value={composerValue}
            placeholder={t('Ask about this database')}
            onKeyDown={onComposerKeyDown}
            onChange={(event: ChangeEvent<HTMLTextAreaElement>) =>
              setComposerValue(event.target.value)
            }
          />
        </ComposerInput>
        <Flex justify="space-between" align="center" gap="small" wrap="wrap">
          <Flex gap="small" align="center" wrap="wrap">
            <ExecutionModeControl>
              <Select
                ariaLabel={t('SQL execution mode')}
                options={executionModeOptions}
                value={executionMode}
                showSearch={false}
                onChange={onExecutionModeChange}
              />
            </ExecutionModeControl>
            <MetaText>
              {health?.default_model || health?.model_provider}
            </MetaText>
          </Flex>
          {isLoading ? (
            <Button
              aria-label={t('Stop')}
              buttonStyle="danger"
              onClick={onCancel}
              icon={<Icons.StopOutlined iconSize="m" />}
            >
              {t('Stop')}
            </Button>
          ) : (
            <Button
              aria-label={t('Send')}
              buttonStyle="primary"
              onClick={() => onSend()}
              disabled={!canSend}
              icon={<Icons.ArrowRightOutlined iconSize="m" />}
            >
              {t('Send')}
            </Button>
          )}
        </Flex>
      </Composer>
    </Panel>
  );
};

export default AiAgentPanel;
