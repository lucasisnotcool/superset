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
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
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
  Tooltip,
  Typography,
} from '@superset-ui/core/components';
import { Icons } from '@superset-ui/core/components/Icons';
import {
  AgentApiError,
  AgentStep,
  applyCopilotChangeset,
  Changeset,
  ChangesetItem,
  ConversationMessage,
  ConversationSummary,
  CopilotInspector,
  createCopilotConversation,
  deleteCopilotConversation,
  getCopilotConversation,
  getCopilotInspector,
  getSemanticDocument,
  listCopilotConversations,
  MessageAttachment,
  SemanticDocument,
  SemanticProjectReadinessStatus,
  streamCopilot,
  updateCopilotConversationTitle,
} from '../api';
import {
  getDocumentStatusMeta,
  isPendingDocumentStatus,
} from './documentStatus';
import AttachDocumentDialog from './AttachDocumentDialog';
import CopilotInspectorDialog from './CopilotInspectorDialog';
import CoverageDialog from './CoverageDialog';

/** Pull the persisted Copilot changeset off an assistant message, if any. */
const changesetFromMessage = (
  message: ConversationMessage,
): Changeset | null => {
  const artifact = message.artifacts?.find(item => item.type === 'changeset');
  return artifact?.payload ? (artifact.payload as unknown as Changeset) : null;
};

/** localStorage key for the active thread, so it resumes across page reloads. */
const activeThreadKey = (projectId: string) =>
  `sqllab:mdl-copilot:conversation:${projectId}`;

export interface CopilotPanelProps {
  projectId: string;
  /**
   * Name of the project this Copilot is bound to. Surfaced as a badge next to the
   * title so the user can always see which MDL Lab project the Copilot is scoped
   * to — the Copilot's entire grounding (and every API call) is keyed by
   * `projectId`, so this badge is a visible, redundant confirmation of that scope.
   */
  projectName?: string | null;
  canWrite: boolean;
  /** Called after accepted edits are persisted, so the editor can refresh. */
  onApplied?: () => void;
  /**
   * Backend-derived readiness of the semantic layer. When not `ready` the panel
   * renders a bootstrap view (onboarding is a separate process) instead of the
   * chat; the transcript is preserved across the transition.
   */
  readinessStatus: SemanticProjectReadinessStatus;
  /** Human-readable readiness detail (used as the error text when `failed`). */
  readinessDetail?: string | null;
  /** Onboard the whole schema manually (the deterministic table-picker job). */
  onOnboard: () => void;
  /** Open the Auto-onboard document picker (the primary empty-state action). */
  onAutoOnboard?: () => void;
  /**
   * Fire a document-grounded onboarding turn from outside the panel (the
   * Auto-onboard flow). Each new `token` triggers exactly one Copilot turn with
   * the given message and the documents attached — the kickstart of the
   * BI-doc-first onboarding conversation.
   */
  kickstart?: CopilotKickstart;
  /**
   * Called once the panel has consumed a `kickstart` (fired its turn). The parent
   * must clear the kickstart so a later remount (e.g. after Apply → refresh) does
   * not re-fire the same onboarding turn.
   */
  onKickstartHandled?: () => void;
  /**
   * Called after attaching persists one or more documents, so the editor can
   * refresh its document list and the new files appear in the workspace tree.
   */
  onDocumentsChanged?: () => void;
}

export interface CopilotKickstart {
  /** Monotonic token; a change (not the value) fires one turn. */
  token: number;
  /** The templated user message that kickstarts the onboarding conversation. */
  message: string;
  /** Documents to attach to (ground) the turn. */
  documents: SemanticDocument[];
}

type Decision = 'accepted' | 'rejected';

const MAX_ATTACHMENT_CHARS = 200_000;

// Live attach-status poll: a document over the async-extraction threshold uploads
// as `extracting` and finishes on a background thread, so the staged chip is
// polled to its terminal status. Extraction is far faster than onboarding (which
// polls 2s × 450 ≈ 15min), so a shorter interval and a ~3min cap suffice; on cap
// the Send gate stops blocking even if the doc is still extracting.
const ATTACH_POLL_INTERVAL_MS = 1500;
const ATTACH_POLL_MAX_ATTEMPTS = 120;

const opLabel = (op: ChangesetItem['op']) => {
  if (op === 'create') return t('Create');
  if (op === 'delete') return t('Delete');
  return t('Update');
};

const CopilotPanel = ({
  projectId,
  projectName,
  canWrite,
  onApplied,
  readinessStatus,
  readinessDetail,
  onOnboard,
  onAutoOnboard,
  kickstart,
  onKickstartHandled,
  onDocumentsChanged,
}: CopilotPanelProps) => {
  const theme = useTheme();
  const isReady = readinessStatus === 'ready';
  // F4: the Copilot is usable pre-onboarding — it can drive onboarding itself
  // (propose models from a BI doc, human-in-the-loop). The only hard block is an
  // in-flight onboarding *job* (``indexing``), which would race file writes; empty
  // and failed projects open straight into a chat that can onboard them.
  const isBootstrapping = readinessStatus === 'indexing';
  const needsOnboarding = !isReady && !isBootstrapping;
  // Lets the user dismiss the onboarding banner and just chat (the Copilot can
  // onboard from the conversation too). Resets per project — the panel is keyed
  // by project id, so opening another project shows the banner again.
  const [onboardBannerDismissed, setOnboardBannerDismissed] = useState(false);
  const [input, setInput] = useState('');
  // Persisted thread state: the transcript lives on the backend (survives
  // reload + is multi-turn). ``pendingUser`` is the optimistic in-flight bubble.
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ConversationMessage[]>([]);
  const [pendingUser, setPendingUser] = useState<string | null>(null);
  const [summaries, setSummaries] = useState<ConversationSummary[]>([]);
  const [isHistoryOpen, setIsHistoryOpen] = useState(false);
  // Attaching now PERSISTS each file as a workspace document (upload + dedup +
  // vectorize) — the same pipeline as the "Upload document" button — and then
  // grounds the current turn by inlining the server-extracted text. We hold the
  // persisted documents (not raw text) so a chip can show live status and the
  // send payload is derived from the authoritative extraction.
  const [attachedDocs, setAttachedDocs] = useState<SemanticDocument[]>([]);
  // True once the status poll exhausts its attempt budget while a doc is still
  // extracting. It stops the Send gate from blocking forever on a hung/slow
  // extraction; reset whenever the attachment set changes (a new attach re-arms).
  const [attachPollGaveUp, setAttachPollGaveUp] = useState(false);
  // The LIVE, actionable changeset for the just-completed turn. Past changesets
  // re-render read-only from message artifacts on resume (no stale Apply).
  const [changeset, setChangeset] = useState<Changeset | null>(null);
  const [decisions, setDecisions] = useState<Record<string, Decision>>({});
  const [isRunning, setIsRunning] = useState(false);
  const [isApplying, setIsApplying] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [inspector, setInspector] = useState<CopilotInspector | null>(null);
  const [inspectorOpen, setInspectorOpen] = useState(false);
  const [coverageOpen, setCoverageOpen] = useState(false);
  const [liveSteps, setLiveSteps] = useState<AgentStep[]>([]);
  // Drives the Attach dialog (pick existing `raw/` documents and/or upload new
  // ones). Replaces the former bare hidden file input.
  const [attachOpen, setAttachOpen] = useState(false);

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

  const refreshSummaries = useCallback(async () => {
    try {
      setSummaries(await listCopilotConversations(projectId));
    } catch {
      // History is non-critical; a transient failure should not break the chat.
    }
  }, [projectId]);

  const resumeConversation = useCallback(
    async (id: string, { closeHistory = true } = {}) => {
      setError(null);
      resetProposal();
      try {
        const conversation = await getCopilotConversation(projectId, id);
        setConversationId(conversation.id);
        setMessages(conversation.messages);
        setPendingUser(null);
        // Auto-resume (on open) must not yank a history panel the user just
        // opened; only an explicit history-item resume closes it.
        if (closeHistory) setIsHistoryOpen(false);
        localStorage.setItem(activeThreadKey(projectId), conversation.id);
      } catch (caught) {
        // A thread deleted elsewhere (e.g. another device) is gone, not an error:
        // forget the stale id and fall back to a fresh chat instead of an alarm.
        if (caught instanceof AgentApiError && caught.status === 404) {
          localStorage.removeItem(activeThreadKey(projectId));
          setConversationId(null);
          setMessages([]);
          return;
        }
        setError(caught instanceof Error ? caught.message : String(caught));
      }
    },
    [projectId, resetProposal],
  );

  const startNewChat = useCallback(() => {
    setConversationId(null);
    setMessages([]);
    setPendingUser(null);
    setInput('');
    setAttachedDocs([]);
    setAttachPollGaveUp(false);
    setError(null);
    resetProposal();
    setIsHistoryOpen(false);
    localStorage.removeItem(activeThreadKey(projectId));
  }, [projectId, resetProposal]);

  const ensureConversation = useCallback(async (): Promise<string> => {
    if (conversationId) return conversationId;
    const conversation = await createCopilotConversation(projectId);
    setConversationId(conversation.id);
    localStorage.setItem(activeThreadKey(projectId), conversation.id);
    return conversation.id;
  }, [conversationId, projectId]);

  // On project change (and first open) hard-reset the thread state, then load the
  // project's history and resume its latest conversation. This is what scopes the
  // Copilot to the *currently open* project: without the reset, the previous
  // project's conversationId/transcript/changeset would leak into the new one — and
  // sending would POST a foreign conversationId (→ 404 "conversation not found").
  // Not gated on readiness: an empty project can still have a prior (doc-driven)
  // onboarding thread to show on open.
  useEffect(() => {
    let cancelled = false;
    // Clear synchronously so no foreign-project state is ever shown/sent.
    setConversationId(null);
    setMessages([]);
    setPendingUser(null);
    setInput('');
    setAttachedDocs([]);
    setAttachPollGaveUp(false);
    setError(null);
    resetProposal();
    setIsHistoryOpen(false);
    (async () => {
      let list: ConversationSummary[] = [];
      try {
        list = await listCopilotConversations(projectId);
      } catch {
        // History is non-critical; a transient failure should not break the chat.
      }
      if (cancelled) return;
      setSummaries(list);
      // Prefer the per-project active thread (this device); else the most recent
      // thread for the project — "the latest user-convo per project on open". A
      // stored id that was deleted elsewhere 404s in resumeConversation, which
      // forgets it and falls back to an empty chat (no error).
      const stored = localStorage.getItem(activeThreadKey(projectId));
      const target = stored ?? list[0]?.id ?? null;
      if (target) resumeConversation(target, { closeHistory: false });
    })();
    return () => {
      cancelled = true;
    };
    // Re-run only when the open project changes (resume helpers are stable per id).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);

  // Commit the Attach dialog's chosen document set as this turn's attachments.
  // The dialog is seeded from the current `attachedDocs`, so its selection is
  // authoritative — this replaces the set (deselecting in the dialog removes a
  // chip; uploads add and ground new documents). Status polling + the Send gate
  // then operate on the new set unchanged.
  const handleAttachConfirm = useCallback((docs: SemanticDocument[]) => {
    setAttachedDocs(docs);
    // A fresh selection re-arms the poll's give-up budget (so a previous
    // exhausted poll doesn't leave a newly-added doc's Send gate disengaged).
    setAttachPollGaveUp(false);
  }, []);

  // Live-update staged attachments that are still extracting (large files extract
  // on a background thread). Polls each pending doc to its terminal status so the
  // chip reflects progress (R1) and `attachmentsForSend` grounds the turn on the
  // finished text (R3). Bounded + cancel-safe, mirroring the onboarding poller.
  useEffect(() => {
    const pending = attachedDocs.filter(doc =>
      isPendingDocumentStatus(doc.status),
    );
    if (!pending.length) return undefined;
    let cancelled = false;
    let attemptsLeft = ATTACH_POLL_MAX_ATTEMPTS;
    let timer: ReturnType<typeof setTimeout>;

    const poll = async () => {
      const fresh = await Promise.all(
        pending.map(doc => getSemanticDocument(doc.id).catch(() => null)),
      );
      if (cancelled) return;
      // Patch only changed rows so an unchanged poll keeps the array identity
      // stable and does not re-arm this effect (avoids a tight reschedule loop).
      setAttachedDocs(prev => {
        let changed = false;
        const next = prev.map(doc => {
          const updated = fresh.find(item => item?.id === doc.id);
          if (
            updated &&
            (updated.status !== doc.status ||
              updated.extracted_text !== doc.extracted_text)
          ) {
            changed = true;
            return updated;
          }
          return doc;
        });
        return changed ? next : prev;
      });
      attemptsLeft -= 1;
      const stillPending = fresh.some(
        item => item && isPendingDocumentStatus(item.status),
      );
      if (!stillPending) return;
      if (attemptsLeft <= 0) {
        // Give up the Send gate but keep the (still-pending) status visible; the
        // turn may proceed ungrounded for this doc and RAG catches up later.
        setAttachPollGaveUp(true);
        return;
      }
      timer = setTimeout(poll, ATTACH_POLL_INTERVAL_MS);
    };

    timer = setTimeout(poll, ATTACH_POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [attachedDocs]);

  // Build the inline grounding payload from the persisted documents' extracted
  // text (server-side extraction handles PDF/DOCX/etc.), bounded per attachment.
  const attachmentsForSend = useCallback(
    (): MessageAttachment[] =>
      attachedDocs.map(doc => {
        const text = doc.extracted_text ?? '';
        return {
          filename: doc.filename,
          content_type: doc.content_type,
          text: text.slice(0, MAX_ATTACHMENT_CHARS),
          truncated: text.length > MAX_ATTACHMENT_CHARS,
        };
      }),
    [attachedDocs],
  );

  // Attachments still extracting: a turn waits for them so their text can ground
  // the chat — unless the poll already gave up (then proceed; RAG catches up).
  const pendingAttachments = useMemo(
    () => attachedDocs.filter(doc => isPendingDocumentStatus(doc.status)),
    [attachedDocs],
  );
  const attachmentBlocksSend =
    pendingAttachments.length > 0 && !attachPollGaveUp;

  // Stream one Copilot turn and reconcile the transcript + proposed changeset.
  // Shared by the manual Send and the Auto-onboard kickstart so both paths apply
  // identical optimistic-bubble, reload-from-server, and accept-default logic.
  const submitTurn = useCallback(
    async (message: string, attachments: MessageAttachment[]) => {
      setError(null);
      resetProposal();
      setPendingUser(message);
      setIsRunning(true);
      setLiveSteps([]);
      try {
        const id = await ensureConversation();
        const result = await streamCopilot(
          projectId,
          {
            message,
            conversation_id: id,
            attachments: attachments.length ? attachments : undefined,
          },
          step => setLiveSteps(prev => [...prev, step]),
        );
        setAttachedDocs([]);
        setAttachPollGaveUp(false);
        // The turn (user + assistant + changeset artifact) is now persisted; reload
        // the thread so the transcript matches the durable record exactly.
        const conversation = await getCopilotConversation(projectId, id);
        setMessages(conversation.messages);
        setPendingUser(null);
        // Default valid items to accepted (the common "apply all" flow), but
        // auto-exclude items that failed validation so a known-bad draft is never
        // applied — and so the per-item Accept becomes a meaningful opt-in for
        // them rather than a no-op on an already-accepted item (P3).
        const initial: Record<string, Decision> = {};
        result.items.forEach(item => {
          initial[item.path] =
            item.validation?.valid === false ? 'rejected' : 'accepted';
        });
        setDecisions(initial);
        setChangeset(result);
        refreshSummaries();
      } catch (caught) {
        setError(caught instanceof Error ? caught.message : String(caught));
        setPendingUser(null);
      } finally {
        setIsRunning(false);
      }
    },
    [ensureConversation, projectId, refreshSummaries, resetProposal],
  );

  const handleSend = useCallback(async () => {
    const message = input.trim();
    if (!message || isRunning || attachmentBlocksSend) return;
    setInput('');
    await submitTurn(message, attachmentsForSend());
  }, [attachmentsForSend, attachmentBlocksSend, input, isRunning, submitTurn]);

  // Auto-onboard kickstart: attach the chosen documents and send the templated
  // message as one turn. Builds the attachment payload directly from the passed
  // documents (not the async `attachedDocs` state) so there is no setState race;
  // the chips still render because we stage the same docs for display.
  const runKickstart = useCallback(
    async (message: string, docs: SemanticDocument[]) => {
      if (!canWrite || isRunning) return;
      setInput('');
      setAttachedDocs(docs);
      setAttachPollGaveUp(false);
      const attachments: MessageAttachment[] = docs.map(doc => {
        const text = doc.extracted_text ?? '';
        return {
          filename: doc.filename,
          content_type: doc.content_type,
          text: text.slice(0, MAX_ATTACHMENT_CHARS),
          truncated: text.length > MAX_ATTACHMENT_CHARS,
        };
      });
      await submitTurn(message, attachments);
    },
    [canWrite, isRunning, submitTurn],
  );

  // Fire the kickstart exactly once per new token (the guard prevents a re-render
  // — e.g. isRunning toggling — from re-sending the same onboarding turn), then
  // tell the parent to clear it so a later remount (Apply → refresh) cannot
  // re-fire the same onboarding turn.
  const lastKickstartToken = useRef<number | null>(null);
  useEffect(() => {
    if (!kickstart || kickstart.token === lastKickstartToken.current) return;
    lastKickstartToken.current = kickstart.token;
    runKickstart(kickstart.message, kickstart.documents);
    onKickstartHandled?.();
  }, [kickstart, runKickstart, onKickstartHandled]);

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
      await applyCopilotChangeset(projectId, acceptedItems, conversationId);
      // The apply is recorded as an assistant turn server-side; reload the thread
      // so the "Applied N draft(s)" note shows and the prior proposal becomes
      // read-only history (drafts now exist).
      if (conversationId) {
        const conversation = await getCopilotConversation(
          projectId,
          conversationId,
        );
        setMessages(conversation.messages);
        refreshSummaries();
      }
      resetProposal();
      onApplied?.();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setIsApplying(false);
    }
  }, [
    acceptedItems,
    changeset,
    conversationId,
    onApplied,
    projectId,
    refreshSummaries,
    resetProposal,
  ]);

  const handleRename = useCallback(async () => {
    if (!conversationId) return;
    // eslint-disable-next-line no-alert
    const title = window.prompt(t('Rename conversation'))?.trim();
    if (!title) return;
    try {
      await updateCopilotConversationTitle(projectId, conversationId, title);
      refreshSummaries();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    }
  }, [conversationId, projectId, refreshSummaries]);

  const handleDelete = useCallback(
    async (id: string) => {
      try {
        await deleteCopilotConversation(projectId, id);
        if (id === conversationId) startNewChat();
        refreshSummaries();
      } catch (caught) {
        setError(caught instanceof Error ? caught.message : String(caught));
      }
    },
    [conversationId, projectId, refreshSummaries, startNewChat],
  );

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

  // Renders a changeset either as the LIVE actionable proposal (accept/reject +
  // Apply) or, on a resumed thread, as a read-only history of a past proposal.
  const renderChangesetReview = useCallback(
    (cs: Changeset, actionable: boolean) => {
      if (!cs.items.length) return null;
      return (
        <Flex vertical gap={theme.sizeUnit * 2} data-test="copilot-changeset">
          <Flex justify="space-between" align="center">
            <Typography.Text strong>
              {actionable
                ? t('%s proposed change(s)', cs.items.length)
                : t('%s proposed change(s) (history)', cs.items.length)}
            </Typography.Text>
            {actionable ? (
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
            ) : null}
          </Flex>
          {cs.items.map(item => {
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
                  opacity: ${actionable && decision === 'rejected' ? 0.55 : 1};
                `}
                data-test="copilot-changeset-item"
              >
                <Flex justify="space-between" align="center" wrap="wrap">
                  <Flex align="center" gap={theme.sizeUnit}>
                    <Tag color={item.op === 'delete' ? 'error' : 'processing'}>
                      {opLabel(item.op)}
                    </Tag>
                    <Typography.Text code>{item.path}</Typography.Text>
                    {invalid ? <Tag color="error">{t('invalid')}</Tag> : null}
                  </Flex>
                  {actionable ? (
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
                  ) : null}
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
      );
    },
    [
      acceptedItems,
      canWrite,
      decisions,
      diffStyles,
      handleApply,
      isApplying,
      theme,
    ],
  );

  // The live changeset belongs to the last assistant message; suppress that
  // message's read-only render so we don't show the same proposal twice.
  const lastAssistantId = useMemo(() => {
    for (let i = messages.length - 1; i >= 0; i -= 1) {
      if (messages[i].role === 'assistant') return messages[i].id;
    }
    return null;
  }, [messages]);

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
        vertical
        gap={theme.sizeUnit}
        css={css`
          padding: ${theme.sizeUnit * 2}px;
          border-bottom: 1px solid ${theme.colorBorderSecondary};
        `}
      >
        {/* Title sits above the actions and the actions wrap, so a narrow rail
            never squeezes "MDL Copilot" into one character per line. The project
            badge makes the Copilot's scope (which MDL Lab project it edits and
            grounds on) visible at all times. */}
        <Flex align="center" gap={theme.sizeUnit} wrap="wrap">
          <Typography.Text strong>{t('MDL Copilot')}</Typography.Text>
          {projectName ? (
            <Tooltip title={t('This Copilot is scoped to the open project')}>
              <Tag color="blue" data-test="copilot-project-badge">
                {projectName}
              </Tag>
            </Tooltip>
          ) : null}
        </Flex>
        {/* Coverage + Inspector operate on an active semantic layer, so they are
            hidden until the layer is ready (decision: UI-hide, no backend gate).
            Thread actions (new / history / rename / delete) mirror the AI SQL
            chat for cross-agent parity. */}
        {!isBootstrapping ? (
          <Flex gap={theme.sizeUnit} wrap="wrap">
            <Button
              buttonStyle="link"
              buttonSize="small"
              icon={<Icons.PlusOutlined />}
              onClick={startNewChat}
              data-test="copilot-new-chat"
            >
              {t('New chat')}
            </Button>
            <Button
              buttonStyle={isHistoryOpen ? 'primary' : 'link'}
              buttonSize="small"
              icon={<Icons.HistoryOutlined />}
              onClick={() => setIsHistoryOpen(open => !open)}
              data-test="copilot-history-toggle"
            >
              {t('History')}
            </Button>
            <Button
              buttonStyle="link"
              buttonSize="small"
              icon={<Icons.EditOutlined />}
              disabled={!conversationId}
              onClick={handleRename}
              data-test="copilot-rename"
            >
              {t('Rename')}
            </Button>
            <Button
              buttonStyle="link"
              buttonSize="small"
              icon={<Icons.DeleteOutlined />}
              disabled={!conversationId}
              onClick={() => conversationId && handleDelete(conversationId)}
              data-test="copilot-delete"
            >
              {t('Delete')}
            </Button>
            <Button
              buttonStyle="link"
              buttonSize="small"
              icon={<Icons.CheckSquareOutlined />}
              onClick={() => setCoverageOpen(true)}
              data-test="copilot-coverage-toggle"
            >
              {t('Coverage')}
            </Button>
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
        ) : null}
      </Flex>

      {!isBootstrapping && isHistoryOpen ? (
        <Flex
          vertical
          gap={theme.sizeUnit}
          css={css`
            max-height: 180px;
            overflow-y: auto;
            padding: ${theme.sizeUnit * 2}px;
            border-bottom: 1px solid ${theme.colorBorderSecondary};
          `}
          data-test="copilot-history"
        >
          {summaries.length === 0 ? (
            <Typography.Text type="secondary">
              {t('No saved conversations yet.')}
            </Typography.Text>
          ) : (
            summaries.map(summary => (
              <Flex key={summary.id} align="center" gap={theme.sizeUnit}>
                <Button
                  block
                  buttonStyle={
                    summary.id === conversationId ? 'primary' : 'tertiary'
                  }
                  buttonSize="small"
                  onClick={() => resumeConversation(summary.id)}
                  data-test="copilot-history-item"
                  css={css`
                    justify-content: flex-start;
                    text-align: left;
                  `}
                >
                  <Typography.Text ellipsis>{summary.title}</Typography.Text>
                </Button>
                <Button
                  buttonStyle="link"
                  buttonSize="small"
                  icon={<Icons.DeleteOutlined />}
                  onClick={() => handleDelete(summary.id)}
                  data-test="copilot-history-delete"
                  aria-label={t('Delete conversation')}
                />
              </Flex>
            ))
          )}
        </Flex>
      ) : null}

      {isBootstrapping ? (
        <Flex
          vertical
          align="center"
          justify="center"
          gap={theme.sizeUnit * 3}
          css={css`
            flex: 1;
            min-height: 0;
            padding: ${theme.sizeUnit * 6}px;
            text-align: center;
          `}
          data-test="copilot-not-ready"
        >
          <Icons.LoadingOutlined
            iconSize="xl"
            aria-label={t('Onboarding in progress')}
          />
          <Typography.Text type="secondary">
            {t(
              'Onboarding is running — building the base semantic layer from your ' +
                'registered datasets. The Copilot opens automatically when it ' +
                'finishes.',
            )}
          </Typography.Text>
        </Flex>
      ) : (
        <>
          {/* F4: empty/failed projects open straight into the chat. A slim banner
              keeps the one-click whole-schema onboarding affordance, while the chat
              itself can onboard specific tables (incl. across schemas) from a doc. */}
          {needsOnboarding && !onboardBannerDismissed ? (
            <Flex
              vertical
              gap={theme.sizeUnit}
              css={css`
                margin: ${theme.sizeUnit * 2}px;
                padding: ${theme.sizeUnit * 2}px;
                border: 1px solid ${theme.colorBorderSecondary};
                border-radius: ${theme.borderRadius}px;
              `}
              data-test="copilot-onboard-banner"
            >
              <Typography.Text
                type={readinessStatus === 'failed' ? 'danger' : 'secondary'}
              >
                {readinessStatus === 'failed'
                  ? t(
                      'Onboarding didn’t finish: %s',
                      readinessDetail || t('unknown error'),
                    )
                  : t(
                      'This project has no active models yet. Auto-onboard from a ' +
                        'business document — the Copilot reads it, maps the tables ' +
                        'it describes, and proposes a changeset to review — or ' +
                        'onboard the whole schema manually.',
                    )}
              </Typography.Text>
              <Flex gap={theme.sizeUnit * 2}>
                {onAutoOnboard ? (
                  <Button
                    buttonStyle="primary"
                    buttonSize="small"
                    disabled={!canWrite}
                    onClick={onAutoOnboard}
                    data-test="copilot-auto-onboard"
                  >
                    {t('Auto-onboard')}
                  </Button>
                ) : null}
                <Button
                  buttonStyle={onAutoOnboard ? 'secondary' : 'primary'}
                  buttonSize="small"
                  disabled={!canWrite}
                  onClick={onOnboard}
                  data-test="copilot-onboard"
                >
                  {readinessStatus === 'failed'
                    ? t('Retry onboarding')
                    : t('Onboard manually')}
                </Button>
                {/* Dismiss: an outlined (no-fill, grey-border) icon button to
                    close the banner and just chat. */}
                <Tooltip title={t('Dismiss')}>
                  <Button
                    buttonStyle="tertiary"
                    buttonSize="small"
                    icon={<Icons.CloseOutlined />}
                    onClick={() => setOnboardBannerDismissed(true)}
                    aria-label={t('Dismiss')}
                    data-test="copilot-onboard-dismiss"
                  />
                </Tooltip>
              </Flex>
            </Flex>
          ) : null}
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
            {messages.length === 0 && !pendingUser && !isRunning ? (
              <Empty
                image={Empty.PRESENTED_IMAGE_SIMPLE}
                description={t(
                  'Ask the agent to model a table, add a metric, or fix validation.',
                )}
              />
            ) : null}
            {messages.map(message => {
              const pastChangeset = changesetFromMessage(message);
              // The live (actionable) changeset belongs to the last assistant
              // message; render that one in the actionable block below instead.
              const showPast =
                pastChangeset && !(changeset && message.id === lastAssistantId);
              return (
                <Flex vertical gap={theme.sizeUnit} key={message.id}>
                  <Flex
                    justify={
                      message.role === 'user' ? 'flex-end' : 'flex-start'
                    }
                  >
                    <div
                      css={css`
                        max-width: 90%;
                        padding: ${theme.sizeUnit * 2}px;
                        border-radius: ${theme.borderRadius}px;
                        background: ${message.role === 'user'
                          ? theme.colorPrimaryBg
                          : theme.colorBgLayout};
                        white-space: pre-wrap;
                      `}
                      data-test={`copilot-message-${message.role}`}
                    >
                      {message.content}
                    </div>
                  </Flex>
                  {showPast
                    ? renderChangesetReview(pastChangeset, false)
                    : null}
                </Flex>
              );
            })}
            {pendingUser ? (
              <Flex justify="flex-end">
                <div
                  css={css`
                    max-width: 90%;
                    padding: ${theme.sizeUnit * 2}px;
                    border-radius: ${theme.borderRadius}px;
                    background: ${theme.colorPrimaryBg};
                    white-space: pre-wrap;
                  `}
                  data-test="copilot-message-user"
                >
                  {pendingUser}
                </div>
              </Flex>
            ) : null}
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

            {changeset ? renderChangesetReview(changeset, true) : null}

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
                            type={
                              step.status === 'error' ? 'danger' : 'secondary'
                            }
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
            {attachedDocs.length > 0 ? (
              <Flex
                wrap="wrap"
                gap={theme.sizeUnit}
                data-test="copilot-attachments"
              >
                {attachedDocs.map(doc => {
                  const meta = getDocumentStatusMeta(doc.status);
                  const pending = isPendingDocumentStatus(doc.status);
                  // Once the poll has given up, a still-pending doc shows a distinct
                  // "background" cue rather than a misleading perpetual "Extracting…"
                  // (the gate has re-enabled Send by this point). Otherwise show the
                  // live status label while in flight or when it needs attention.
                  const statusLabel =
                    pending && attachPollGaveUp
                      ? t('Still processing in the background')
                      : pending || meta.attention
                        ? meta.label
                        : null;
                  return (
                    <Tag
                      key={doc.id}
                      closable
                      onClose={() =>
                        setAttachedDocs(prev =>
                          prev.filter(item => item.id !== doc.id),
                        )
                      }
                    >
                      {doc.filename}
                      {statusLabel ? ` · ${statusLabel}` : ''}
                    </Tag>
                  );
                })}
              </Flex>
            ) : null}
            {attachPollGaveUp && pendingAttachments.length > 0 ? (
              <Typography.Text
                type="secondary"
                data-test="copilot-attach-giveup-note"
              >
                {t(
                  'Still extracting %s in the background — you can send now; ' +
                    'it’ll be available to later turns.',
                  pendingAttachments.map(doc => doc.filename).join(', '),
                )}
              </Typography.Text>
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
              <Tooltip
                title={t(
                  'Attach documents (PDF, Word, Excel, PowerPoint, CSV, HTML, ' +
                    'Markdown, JSON). Pick from this project’s documents or upload ' +
                    'new ones; they ground this chat.',
                )}
              >
                <Button
                  buttonStyle="link"
                  buttonSize="small"
                  icon={<Icons.UploadOutlined />}
                  disabled={!canWrite || isRunning}
                  onClick={() => setAttachOpen(true)}
                  data-test="copilot-attach"
                >
                  {t('Attach')}
                </Button>
              </Tooltip>
              <Tooltip
                title={
                  attachmentBlocksSend
                    ? t(
                        'Waiting for %s to finish extracting…',
                        pendingAttachments.map(doc => doc.filename).join(', '),
                      )
                    : ''
                }
              >
                <Button
                  buttonStyle="primary"
                  buttonSize="small"
                  disabled={
                    !canWrite ||
                    isRunning ||
                    !input.trim() ||
                    attachmentBlocksSend
                  }
                  loading={isRunning}
                  onClick={handleSend}
                  data-test="copilot-send"
                >
                  {t('Send')}
                </Button>
              </Tooltip>
            </Flex>
          </Flex>
        </>
      )}

      <CopilotInspectorDialog
        open={inspectorOpen}
        inspector={inspector}
        onClose={() => setInspectorOpen(false)}
      />
      <CoverageDialog
        projectId={projectId}
        open={coverageOpen}
        onClose={() => setCoverageOpen(false)}
      />
      <AttachDocumentDialog
        open={attachOpen}
        projectId={projectId}
        attachedDocs={attachedDocs}
        canWrite={canWrite}
        onConfirm={handleAttachConfirm}
        onClose={() => setAttachOpen(false)}
        onDocumentsChanged={onDocumentsChanged}
      />
    </Flex>
  );
};

export default CopilotPanel;
