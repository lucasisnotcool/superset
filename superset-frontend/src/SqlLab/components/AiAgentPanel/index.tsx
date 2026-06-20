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
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import { useSelector } from 'react-redux';
import { t } from '@apache-superset/core/translation';
import { Alert } from '@apache-superset/core/components';
import { css, styled } from '@apache-superset/core/theme';
import {
  Button,
  Flex,
  Input,
  Select,
  Tooltip,
  Typography,
  type SelectValue,
} from '@superset-ui/core/components';
import { Icons } from '@superset-ui/core/components/Icons';
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
  getAgentBaseUrl,
  getAgentHealth,
  getConversation,
  listConversations,
  sendConversationMessage,
  validateSql,
  type ExecutionMode,
} from './api';

const Panel = styled.div`
  ${({ theme }) => css`
    display: flex;
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

const SqlBlock = styled.pre`
  ${({ theme }) => css`
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

const TraceDetails = styled.details`
  ${({ theme }) => css`
    color: ${theme.colorTextSecondary};
    font-size: ${theme.fontSizeSM}px;

    summary {
      cursor: pointer;
    }
  `}
`;

const TraceList = styled.ul`
  ${({ theme }) => css`
    margin: ${theme.sizeUnit}px 0 0;
    padding-left: ${theme.sizeUnit * 4}px;
  `}
`;

const parseDatasetIds = (value: string) =>
  value
    .split(',')
    .map(item => Number.parseInt(item.trim(), 10))
    .filter(Number.isFinite);

const getActiveQueryEditor = ({
  sqlLab: {
    queryEditors,
    tabHistory,
    unsavedQueryEditor,
    lastUpdatedActiveTab,
  },
}: SqlLabRootState): Partial<QueryEditor> | undefined => {
  const activeId = lastUpdatedActiveTab || tabHistory.slice(-1)[0];
  return (
    queryEditors.find(queryEditor => queryEditor.id === activeId) ||
    (unsavedQueryEditor.id === activeId ? unsavedQueryEditor : undefined)
  );
};

const buildConversationScope = (
  queryEditor: Partial<QueryEditor> | undefined,
  databaseId: number,
  datasetIds: number[],
): ConversationScope => ({
  database_id: databaseId,
  schema_name: queryEditor?.schema || null,
  dataset_ids: datasetIds,
  query_editor_id: queryEditor?.id || null,
  current_sql: queryEditor?.sql || null,
  selected_text: queryEditor?.selectedText || null,
});

const conversationDatasetInput = (conversation: Conversation | null) =>
  conversation?.scope.dataset_ids.join(',') || '';

const executionModes: ExecutionMode[] = ['manual', 'read_only', 'auto'];

const isExecutionMode = (value: SelectValue): value is ExecutionMode =>
  typeof value === 'string' && executionModes.includes(value as ExecutionMode);

const AiAgentPanel = () => {
  const dispatch = useAppDispatch();
  const queryEditor = useSelector(getActiveQueryEditor);
  const transcriptRef = useRef<HTMLDivElement | null>(null);
  const [composerValue, setComposerValue] = useState('');
  const [datasetIds, setDatasetIds] = useState('');
  const [executionMode, setExecutionMode] = useState<ExecutionMode>('manual');
  const [isLoading, setIsLoading] = useState(false);
  const [conversation, setConversation] = useState<Conversation | null>(null);
  const [conversationSummaries, setConversationSummaries] = useState<
    ConversationSummary[]
  >([]);
  const [isHistoryOpen, setIsHistoryOpen] = useState(false);
  const [health, setHealth] = useState<AgentHealthResponse | null>(null);
  const [isHealthError, setIsHealthError] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const parsedDatasetIds = useMemo(
    () => parseDatasetIds(datasetIds),
    [datasetIds],
  );
  const databaseId = queryEditor?.dbId;
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
    if (transcriptRef.current) {
      transcriptRef.current.scrollTop = transcriptRef.current.scrollHeight;
    }
  }, [messages.length, isLoading]);

  const refreshConversationSummaries = () =>
    listConversations()
      .then(setConversationSummaries)
      .catch(() => setConversationSummaries([]));

  const ensureConversation = async (scope: ConversationScope) => {
    if (conversation) {
      return conversation;
    }
    const createdConversation = await createConversation(scope);
    setConversation(createdConversation);
    setDatasetIds(conversationDatasetInput(createdConversation));
    await refreshConversationSummaries();
    return createdConversation;
  };

  const onSend = async () => {
    if (!canSend || typeof databaseId !== 'number') {
      return;
    }
    setIsLoading(true);
    setError(null);
    const message = composerValue.trim();
    const scope = buildConversationScope(
      queryEditor,
      databaseId,
      parsedDatasetIds,
    );
    try {
      const activeConversation = await ensureConversation(scope);
      setComposerValue('');
      const result = await sendConversationMessage(activeConversation.id, {
        message,
        scope,
        execution_mode: executionMode,
      });
      setConversation(result.conversation);
      setDatasetIds(conversationDatasetInput(result.conversation));
      await refreshConversationSummaries();
      if (result.status === 'error') {
        dispatch(addDangerToast(t('The agent could not complete the turn.')));
      }
    } catch (ex) {
      const messageText =
        ex instanceof Error ? ex.message : t('Agent request failed');
      setError(messageText);
      dispatch(addDangerToast(messageText));
    } finally {
      setIsLoading(false);
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
    setDatasetIds('');
  };

  const onOpenConversation = async (conversationId: string) => {
    try {
      const result = await getConversation(conversationId);
      setConversation(result);
      setDatasetIds(conversationDatasetInput(result));
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

  const onValidateSql = async (artifact: ConversationArtifact) => {
    if (!artifact.sql) {
      return;
    }
    try {
      const result = await validateSql(
        artifact.sql,
        artifact.validation?.dialect || null,
      );
      dispatch(
        result.is_valid
          ? addSuccessToast(t('SQL is valid.'))
          : addDangerToast(result.errors.join('\n')),
      );
    } catch (ex) {
      const messageText =
        ex instanceof Error ? ex.message : t('SQL validation failed');
      dispatch(addDangerToast(messageText));
    }
  };

  const onExecuteArtifact = async (artifact: ConversationArtifact) => {
    if (!conversation || !artifact.sql || typeof databaseId !== 'number') {
      return;
    }
    setIsLoading(true);
    setError(null);
    const scope = buildConversationScope(
      queryEditor,
      databaseId,
      parsedDatasetIds,
    );
    const artifactExecutionMode =
      executionMode === 'manual' ? 'read_only' : executionMode;
    try {
      const result = await sendConversationMessage(conversation.id, {
        message: `${t('Execute this SQL and summarize the result')}:\n${artifact.sql}`,
        scope,
        execution_mode: artifactExecutionMode,
      });
      setConversation(result.conversation);
      await refreshConversationSummaries();
      if (result.status === 'error') {
        dispatch(addDangerToast(t('The agent could not execute SQL.')));
      }
    } catch (ex) {
      const messageText =
        ex instanceof Error ? ex.message : t('SQL execution failed');
      setError(messageText);
      dispatch(addDangerToast(messageText));
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <Panel data-test="sql-lab-ai-agent-panel">
      <Header>
        <Flex vertical gap={0}>
          <Typography.Title level={5} style={{ margin: 0 }}>
            {t('AI SQL')}
          </Typography.Title>
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
          {parsedDatasetIds.map(datasetId => (
            <Chip key={datasetId}>{t('Dataset %s', datasetId)}</Chip>
          ))}
          {queryEditor?.selectedText && <Chip>{t('Selection')}</Chip>}
        </ContextChips>
        <Flex gap="small" align="center">
          <Input
            value={datasetIds}
            placeholder={t('Dataset IDs')}
            onChange={(event: ChangeEvent<HTMLInputElement>) =>
              setDatasetIds(event.target.value)
            }
          />
        </Flex>
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

      <Transcript ref={transcriptRef}>
        {messages.map(message => (
          <MessageBlock key={message.id} data-message-role={message.role}>
            <MessageBubble data-message-role={message.role}>
              {message.content}
            </MessageBubble>
            {message.artifacts.map((artifact, index) => (
              <ArtifactBlock key={`${message.id}-${artifact.type}-${index}`}>
                {artifact.explanation && (
                  <MetaText>{artifact.explanation}</MetaText>
                )}
                <SqlBlock>{artifact.sql}</SqlBlock>
                {artifact.validation?.errors.length ? (
                  <Alert
                    type="warning"
                    message={artifact.validation.errors.join('\n')}
                  />
                ) : null}
                {artifact.execution_result && (
                  <MetaText>
                    {t(
                      '%s row(s) returned',
                      artifact.execution_result.row_count,
                    )}
                  </MetaText>
                )}
                <Flex gap="small" wrap="wrap">
                  <Button
                    aria-label={t('Insert')}
                    buttonStyle="tertiary"
                    onClick={() => onInsertSql(artifact.sql)}
                    disabled={!artifact.sql || !queryEditor?.id}
                    icon={<Icons.EditOutlined iconSize="m" />}
                  >
                    {t('Insert')}
                  </Button>
                  <Button
                    aria-label={t('Copy')}
                    buttonStyle="tertiary"
                    onClick={() => onCopySql(artifact.sql)}
                    disabled={!artifact.sql}
                    icon={<Icons.CopyOutlined iconSize="m" />}
                  >
                    {t('Copy')}
                  </Button>
                  <Button
                    aria-label={t('Validate')}
                    buttonStyle="tertiary"
                    onClick={() => onValidateSql(artifact)}
                    disabled={!artifact.sql}
                    icon={<Icons.CheckCircleOutlined iconSize="m" />}
                  >
                    {t('Validate')}
                  </Button>
                  <Button
                    aria-label={t('Execute')}
                    buttonStyle="tertiary"
                    onClick={() => onExecuteArtifact(artifact)}
                    disabled={!artifact.sql || !conversation || isLoading}
                    icon={<Icons.PlayCircleOutlined iconSize="m" />}
                  >
                    {t('Execute')}
                  </Button>
                </Flex>
                {artifact.trace.length > 0 && (
                  <TraceDetails>
                    <summary>{t('Trace')}</summary>
                    <TraceList>
                      {artifact.trace.map((event, index) => (
                        <li key={`${event.step}-${index}`}>
                          {event.step}: {event.summary}
                        </li>
                      ))}
                    </TraceList>
                  </TraceDetails>
                )}
              </ArtifactBlock>
            ))}
          </MessageBlock>
        ))}
        {isLoading && (
          <MessageBlock data-message-role="assistant">
            <MessageBubble data-message-role="assistant">
              {t('Working...')}
            </MessageBubble>
          </MessageBlock>
        )}
        {error && <Alert type="error" message={error} />}
      </Transcript>

      <Composer>
        <Input.TextArea
          rows={3}
          value={composerValue}
          placeholder={t('Ask about this database')}
          onKeyDown={onComposerKeyDown}
          onChange={(event: ChangeEvent<HTMLTextAreaElement>) =>
            setComposerValue(event.target.value)
          }
        />
        <Flex justify="space-between" align="center" gap="small" wrap="wrap">
          <Flex gap="small" align="center" wrap="wrap">
            <ExecutionModeControl>
              <Select
                ariaLabel={t('SQL execution mode')}
                options={executionModeOptions}
                value={executionMode}
                showSearch={false}
                onChange={value => {
                  if (isExecutionMode(value)) {
                    setExecutionMode(value);
                  }
                }}
              />
            </ExecutionModeControl>
            <MetaText>
              {health?.default_model || health?.model_provider}
            </MetaText>
          </Flex>
          <Button
            aria-label={t('Send')}
            buttonStyle="primary"
            onClick={onSend}
            disabled={!canSend || isLoading}
            loading={isLoading}
            icon={<Icons.ArrowRightOutlined iconSize="m" />}
          >
            {t('Send')}
          </Button>
        </Flex>
      </Composer>
    </Panel>
  );
};

export default AiAgentPanel;
