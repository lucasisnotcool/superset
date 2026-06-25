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
import { ChangeEvent, useCallback, useMemo, useRef, useState } from 'react';
import ReactDiffViewer from 'react-diff-viewer-continued';
import { t } from '@apache-superset/core/translation';
import { css, isThemeDark, useTheme } from '@apache-superset/core/theme';
import { Alert } from '@apache-superset/core/components';
import {
  Button,
  Collapse,
  Empty,
  Flex,
  Input,
  Tag,
  Typography,
} from '@superset-ui/core/components';
import { Icons } from '@superset-ui/core/components/Icons';
import {
  AgentStep,
  applyCopilotChangeset,
  Changeset,
  ChangesetItem,
  CopilotInspector,
  getCopilotInspector,
  MessageAttachment,
  streamCopilot,
} from '../api';
import CopilotInspectorDrawer from './CopilotInspectorDrawer';

export interface CopilotPanelProps {
  projectId: string;
  canWrite: boolean;
  /** Called after accepted edits are persisted, so the editor can refresh. */
  onApplied?: () => void;
}

type Decision = 'accepted' | 'rejected';

interface TranscriptEntry {
  id: string;
  role: 'user' | 'assistant';
  content: string;
}

const MAX_ATTACHMENT_CHARS = 200_000;

const opLabel = (op: ChangesetItem['op']) => {
  if (op === 'create') return t('Create');
  if (op === 'delete') return t('Delete');
  return t('Update');
};

let entryCounter = 0;
const nextId = () => {
  entryCounter += 1;
  return `copilot-${entryCounter}`;
};

const CopilotPanel = ({
  projectId,
  canWrite,
  onApplied,
}: CopilotPanelProps) => {
  const theme = useTheme();
  const [input, setInput] = useState('');
  const [transcript, setTranscript] = useState<TranscriptEntry[]>([]);
  const [attachments, setAttachments] = useState<MessageAttachment[]>([]);
  const [changeset, setChangeset] = useState<Changeset | null>(null);
  const [decisions, setDecisions] = useState<Record<string, Decision>>({});
  const [isRunning, setIsRunning] = useState(false);
  const [isApplying, setIsApplying] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [inspector, setInspector] = useState<CopilotInspector | null>(null);
  const [inspectorOpen, setInspectorOpen] = useState(false);
  const [liveSteps, setLiveSteps] = useState<AgentStep[]>([]);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const diffStyles = useMemo(() => {
    const variables = {
      diffViewerBackground: theme.colorBgContainer,
      diffViewerColor: theme.colorText,
      addedBackground: theme.colorSuccessBg,
      addedColor: theme.colorText,
      removedBackground: theme.colorErrorBg,
      removedColor: theme.colorText,
      gutterBackground: theme.colorBgLayout,
      gutterColor: theme.colorTextTertiary,
      emptyLineBackground: theme.colorBgContainer,
    };
    return {
      variables: { dark: variables, light: variables },
      diffContainer: {
        borderRadius: `${theme.borderRadius}px`,
        border: `1px solid ${theme.colorBorder}`,
      },
    };
  }, [theme]);

  const resetProposal = useCallback(() => {
    setChangeset(null);
    setDecisions({});
  }, []);

  const handleAttach = useCallback(
    async (event: ChangeEvent<HTMLInputElement>) => {
      const files = Array.from(event.target.files ?? []);
      event.target.value = '';
      const next: MessageAttachment[] = [];
      for (const file of files) {
        // eslint-disable-next-line no-await-in-loop
        const raw = await file.text();
        next.push({
          filename: file.name,
          content_type: file.type || 'text/plain',
          text: raw.slice(0, MAX_ATTACHMENT_CHARS),
          truncated: raw.length > MAX_ATTACHMENT_CHARS,
        });
      }
      setAttachments(prev => [...prev, ...next]);
    },
    [],
  );

  const handleSend = useCallback(async () => {
    const message = input.trim();
    if (!message || isRunning) return;
    setError(null);
    resetProposal();
    setInput('');
    const userEntry: TranscriptEntry = {
      id: nextId(),
      role: 'user',
      content: message,
    };
    setTranscript(prev => [...prev, userEntry]);
    setIsRunning(true);
    setLiveSteps([]);
    try {
      const result = await streamCopilot(
        projectId,
        {
          message,
          attachments: attachments.length ? attachments : undefined,
        },
        step => setLiveSteps(prev => [...prev, step]),
      );
      setAttachments([]);
      setTranscript(prev => [
        ...prev,
        {
          id: nextId(),
          role: 'assistant',
          content:
            result.message || t('Proposed changes are ready for review.'),
        },
      ]);
      const initial: Record<string, Decision> = {};
      result.items.forEach(item => {
        initial[item.path] = 'accepted';
      });
      setDecisions(initial);
      setChangeset(result);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setIsRunning(false);
    }
  }, [attachments, input, isRunning, projectId, resetProposal]);

  const acceptedItems = useMemo(
    () =>
      (changeset?.items ?? []).filter(
        item => decisions[item.path] === 'accepted',
      ),
    [changeset, decisions],
  );

  const handleApply = useCallback(async () => {
    if (!changeset || !acceptedItems.length) return;
    setIsApplying(true);
    setError(null);
    try {
      await applyCopilotChangeset(projectId, acceptedItems);
      setTranscript(prev => [
        ...prev,
        {
          id: nextId(),
          role: 'assistant',
          content: t('Applied %s file(s) as drafts.', acceptedItems.length),
        },
      ]);
      resetProposal();
      onApplied?.();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setIsApplying(false);
    }
  }, [acceptedItems, changeset, onApplied, projectId, resetProposal]);

  const openInspector = useCallback(async () => {
    setInspectorOpen(true);
    if (!inspector) {
      try {
        setInspector(await getCopilotInspector(projectId));
      } catch (caught) {
        setError(caught instanceof Error ? caught.message : String(caught));
      }
    }
  }, [inspector, projectId]);

  return (
    <Flex
      vertical
      css={css`
        height: 100%;
        min-height: 0;
      `}
      data-test="copilot-panel"
    >
      <Flex
        justify="space-between"
        align="center"
        css={css`
          padding: ${theme.sizeUnit * 2}px;
          border-bottom: 1px solid ${theme.colorBorderSecondary};
        `}
      >
        <Typography.Text strong>{t('MDL Copilot')}</Typography.Text>
        <Button
          buttonStyle="link"
          buttonSize="small"
          icon={<Icons.SettingOutlined />}
          onClick={openInspector}
          data-test="copilot-inspector-toggle"
        >
          {t('Inspector')}
        </Button>
      </Flex>

      <Flex
        vertical
        gap={theme.sizeUnit * 2}
        css={css`
          flex: 1;
          min-height: 0;
          overflow-y: auto;
          padding: ${theme.sizeUnit * 2}px;
        `}
        data-test="copilot-transcript"
      >
        {transcript.length === 0 && !isRunning ? (
          <Empty
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            description={t(
              'Ask the agent to model a table, add a metric, or fix validation.',
            )}
          />
        ) : null}
        {transcript.map(entry => (
          <Flex
            key={entry.id}
            justify={entry.role === 'user' ? 'flex-end' : 'flex-start'}
          >
            <div
              css={css`
                max-width: 90%;
                padding: ${theme.sizeUnit * 2}px;
                border-radius: ${theme.borderRadius}px;
                background: ${entry.role === 'user'
                  ? theme.colorPrimaryBg
                  : theme.colorBgLayout};
                white-space: pre-wrap;
              `}
              data-test={`copilot-message-${entry.role}`}
            >
              {entry.content}
            </div>
          </Flex>
        ))}
        {isRunning ? (
          <Flex vertical gap={theme.sizeUnit} data-test="copilot-running">
            <Typography.Text type="secondary">
              <Icons.LoadingOutlined /> {t('Agent is editing…')}
            </Typography.Text>
            {liveSteps.map((step, index) => (
              <Typography.Text
                // eslint-disable-next-line react/no-array-index-key
                key={`live-${step.kind}-${index}`}
                type={step.status === 'error' ? 'danger' : 'secondary'}
                css={css`
                  padding-left: ${theme.sizeUnit * 2}px;
                `}
              >
                {step.kind}: {step.summary}
              </Typography.Text>
            ))}
          </Flex>
        ) : null}

        {error ? (
          <Alert type="error" showIcon message={error} closable />
        ) : null}

        {changeset?.warnings?.map(warning => (
          <Alert key={warning} type="warning" showIcon message={warning} />
        ))}

        {changeset && changeset.items.length > 0 ? (
          <Flex vertical gap={theme.sizeUnit * 2} data-test="copilot-changeset">
            <Flex justify="space-between" align="center">
              <Typography.Text strong>
                {t('%s proposed change(s)', changeset.items.length)}
              </Typography.Text>
              <Button
                buttonStyle="primary"
                buttonSize="small"
                disabled={!canWrite || isApplying || acceptedItems.length === 0}
                loading={isApplying}
                onClick={handleApply}
                data-test="copilot-apply"
              >
                {t('Apply %s accepted', acceptedItems.length)}
              </Button>
            </Flex>
            {changeset.items.map(item => {
              const decision = decisions[item.path];
              const invalid = item.validation?.valid === false;
              return (
                <Flex
                  vertical
                  key={item.path}
                  gap={theme.sizeUnit}
                  css={css`
                    border: 1px solid ${theme.colorBorderSecondary};
                    border-radius: ${theme.borderRadius}px;
                    padding: ${theme.sizeUnit * 2}px;
                    opacity: ${decision === 'rejected' ? 0.55 : 1};
                  `}
                  data-test="copilot-changeset-item"
                >
                  <Flex justify="space-between" align="center" wrap="wrap">
                    <Flex align="center" gap={theme.sizeUnit}>
                      <Tag
                        color={item.op === 'delete' ? 'error' : 'processing'}
                      >
                        {opLabel(item.op)}
                      </Tag>
                      <Typography.Text code>{item.path}</Typography.Text>
                      {invalid ? <Tag color="error">{t('invalid')}</Tag> : null}
                    </Flex>
                    <Flex gap={theme.sizeUnit}>
                      <Button
                        buttonSize="small"
                        buttonStyle={
                          decision === 'accepted' ? 'primary' : 'secondary'
                        }
                        onClick={() =>
                          setDecisions(prev => ({
                            ...prev,
                            [item.path]: 'accepted',
                          }))
                        }
                        data-test="copilot-accept"
                      >
                        {t('Accept')}
                      </Button>
                      <Button
                        buttonSize="small"
                        buttonStyle={
                          decision === 'rejected' ? 'danger' : 'secondary'
                        }
                        onClick={() =>
                          setDecisions(prev => ({
                            ...prev,
                            [item.path]: 'rejected',
                          }))
                        }
                        data-test="copilot-reject"
                      >
                        {t('Reject')}
                      </Button>
                    </Flex>
                  </Flex>
                  {item.summary ? (
                    <Typography.Text type="secondary">
                      {item.summary}
                    </Typography.Text>
                  ) : null}
                  {item.op !== 'delete' ? (
                    <ReactDiffViewer
                      oldValue={item.current_content || ''}
                      newValue={item.proposed_content || ''}
                      splitView={false}
                      useDarkTheme={isThemeDark(theme)}
                      styles={diffStyles}
                    />
                  ) : (
                    <Typography.Text type="danger">
                      {t('This file will be deleted.')}
                    </Typography.Text>
                  )}
                </Flex>
              );
            })}
          </Flex>
        ) : null}

        {changeset?.steps?.length ? (
          <Collapse
            ghost
            items={[
              {
                key: 'steps',
                label: t('Agent steps (%s)', changeset.steps.length),
                children: (
                  <Flex vertical gap={theme.sizeUnit}>
                    {changeset.steps.map((step, index) => (
                      <Typography.Text
                        // eslint-disable-next-line react/no-array-index-key
                        key={`${step.kind}-${index}`}
                        type={step.status === 'error' ? 'danger' : 'secondary'}
                      >
                        {step.kind}: {step.summary}
                      </Typography.Text>
                    ))}
                  </Flex>
                ),
              },
            ]}
          />
        ) : null}
      </Flex>

      <Flex
        vertical
        gap={theme.sizeUnit}
        css={css`
          border-top: 1px solid ${theme.colorBorderSecondary};
          padding: ${theme.sizeUnit * 2}px;
        `}
      >
        {attachments.length > 0 ? (
          <Flex wrap="wrap" gap={theme.sizeUnit}>
            {attachments.map((attachment, index) => (
              <Tag
                // eslint-disable-next-line react/no-array-index-key
                key={`${attachment.filename}-${index}`}
                closable
                onClose={() =>
                  setAttachments(prev => prev.filter((_, i) => i !== index))
                }
              >
                {attachment.filename}
                {attachment.truncated ? ` (${t('truncated')})` : ''}
              </Tag>
            ))}
          </Flex>
        ) : null}
        <Input.TextArea
          value={input}
          onChange={event => setInput(event.target.value)}
          placeholder={
            canWrite
              ? t('Ask the agent to edit your MDL…')
              : t('You do not have permission to edit this project.')
          }
          autoSize={{ minRows: 2, maxRows: 6 }}
          disabled={!canWrite || isRunning}
          onPressEnter={event => {
            if (!event.shiftKey) {
              event.preventDefault();
              handleSend();
            }
          }}
          data-test="copilot-input"
        />
        <Flex justify="space-between" align="center">
          <input
            ref={fileInputRef}
            type="file"
            multiple
            accept=".json,.md,.txt,.yml,.yaml,.csv,text/*"
            css={css`
              display: none;
            `}
            onChange={handleAttach}
            data-test="copilot-attach-input"
          />
          <Button
            buttonStyle="link"
            buttonSize="small"
            icon={<Icons.UploadOutlined />}
            disabled={!canWrite || isRunning}
            onClick={() => fileInputRef.current?.click()}
            data-test="copilot-attach"
          >
            {t('Attach')}
          </Button>
          <Button
            buttonStyle="primary"
            buttonSize="small"
            disabled={!canWrite || isRunning || !input.trim()}
            loading={isRunning}
            onClick={handleSend}
            data-test="copilot-send"
          >
            {t('Send')}
          </Button>
        </Flex>
      </Flex>

      <CopilotInspectorDrawer
        open={inspectorOpen}
        inspector={inspector}
        onClose={() => setInspectorOpen(false)}
      />
    </Flex>
  );
};

export default CopilotPanel;
