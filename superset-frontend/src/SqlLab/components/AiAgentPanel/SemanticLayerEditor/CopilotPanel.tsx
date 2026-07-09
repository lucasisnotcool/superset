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
  ApplyGroupSummary,
  applyCopilotChangeset,
  Changeset,
  ChangesetItem,
  ConversationMessage,
  ConversationSummary,
  CopilotInspector,
  createCopilotConversation,
  deleteCopilotConversation,
  forkCopilotConversation,
  getCopilotConversation,
  getCopilotInspector,
  getCopilotRewritePreview,
  getSemanticDocument,
  listCopilotApplies,
  listCopilotConversations,
  MessageAttachment,
  revertCopilotApply,
  RewritePreview,
  SemanticDocument,
  SemanticProjectReadinessStatus,
  streamCopilot,
  undoCopilotRewrite,
  updateCopilotConversationTitle,
  upsertLiveStep,
} from '../api';
import RewriteConfirmModal from '../RewriteConfirmModal';
import copyTextToClipboard from 'src/utils/copy';
import {
  getDocumentStatusMeta,
  isPendingDocumentStatus,
} from './documentStatus';
import AttachDocumentDialog from './AttachDocumentDialog';
import CopilotInspectorDialog from './CopilotInspectorDialog';

/** Live elapsed-seconds ticker for a running step (client receipt time). */
function ElapsedSeconds() {
  const [seconds, setSeconds] = useState(0);
  useEffect(() => {
    const startedAt = Date.now();
    const timer = window.setInterval(
      () => setSeconds(Math.floor((Date.now() - startedAt) / 1000)),
      1000,
    );
    return () => window.clearInterval(timer);
  }, []);
  return seconds > 0 ? <> ({seconds}s)</> : null;
}

/** "1.2s" / "340ms" for a completed step's duration. */
const formatDuration = (durationMs?: number | null): string | null => {
  if (durationMs == null) return null;
  if (durationMs >= 1000) return `${(durationMs / 1000).toFixed(1)}s`;
  return `${durationMs}ms`;
};

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
  // Fork back-link of the active thread ("Branch from here"), if it is one.
  const [parentConversationId, setParentConversationId] = useState<
    string | null
  >(null);
  // Inline edit & resend state (which user message; the draft replacement).
  const [editingMessageId, setEditingMessageId] = useState<string | null>(null);
  const [editingValue, setEditingValue] = useState('');
  // Pending rewrite awaiting the side-effect confirm dialog.
  const [rewriteConfirm, setRewriteConfirm] = useState<{
    anchorId: string;
    message: string;
    preview: RewritePreview;
  } | null>(null);
  // Anchor of the last completed rewrite — single-step Undo until next turn.
  const [undoAnchorId, setUndoAnchorId] = useState<string | null>(null);
  // This thread's apply history (before-image groups) — drives the per-turn
  // Revert affordance on "Applied N drafts." messages.
  const [applies, setApplies] = useState<ApplyGroupSummary[]>([]);
  const [revertNotice, setRevertNotice] = useState<string | null>(null);
  const [isRunning, setIsRunning] = useState(false);
  const [isApplying, setIsApplying] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [inspector, setInspector] = useState<CopilotInspector | null>(null);
  const [inspectorOpen, setInspectorOpen] = useState(false);
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

  // Apply history (Revert affordances) — non-critical, fail-open to empty.
  const refreshApplies = useCallback(
    async (id: string | null) => {
      if (!id) {
        setApplies([]);
        return;
      }
      try {
        const groups = await listCopilotApplies(projectId, id);
        // Defensive: an older backend may not expose the route (proxy 200s).
        setApplies(Array.isArray(groups) ? groups : []);
      } catch {
        setApplies([]);
      }
    },
    [projectId],
  );

  const resumeConversation = useCallback(
    async (id: string, { closeHistory = true } = {}) => {
      setError(null);
      resetProposal();
      try {
        const conversation = await getCopilotConversation(projectId, id);
        setConversationId(conversation.id);
        setMessages(conversation.messages);
        setParentConversationId(conversation.parent_conversation_id ?? null);
        setPendingUser(null);
        setUndoAnchorId(null);
        setEditingMessageId(null);
        refreshApplies(conversation.id);
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
    [projectId, refreshApplies, resetProposal],
  );

  const startNewChat = useCallback(() => {
    setConversationId(null);
    setMessages([]);
    setParentConversationId(null);
    setPendingUser(null);
    setInput('');
    setAttachedDocs([]);
    setAttachPollGaveUp(false);
    setError(null);
    setUndoAnchorId(null);
    setEditingMessageId(null);
    setApplies([]);
    setRevertNotice(null);
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
    async (
      message: string,
      attachments: MessageAttachment[],
      options: {
        /** Target thread when it differs from state (e.g. a fresh branch). */
        conversationIdOverride?: string;
        /** Edit & resend / regenerate: truncate from this user message first. */
        rewriteFromMessageId?: string;
      } = {},
    ) => {
      setError(null);
      resetProposal();
      setPendingUser(message);
      setIsRunning(true);
      setLiveSteps([]);
      setUndoAnchorId(null);
      setEditingMessageId(null);
      try {
        const id =
          options.conversationIdOverride ?? (await ensureConversation());
        const result = await streamCopilot(
          projectId,
          {
            message,
            conversation_id: id,
            attachments: attachments.length ? attachments : undefined,
            rewrite_from_message_id: options.rewriteFromMessageId ?? undefined,
          },
          step => setLiveSteps(prev => upsertLiveStep(prev, step)),
        );
        setAttachedDocs([]);
        setAttachPollGaveUp(false);
        // The turn (user + assistant + changeset artifact) is now persisted; reload
        // the thread so the transcript matches the durable record exactly.
        const conversation = await getCopilotConversation(projectId, id);
        setMessages(conversation.messages);
        setPendingUser(null);
        if (options.rewriteFromMessageId) {
          setUndoAnchorId(options.rewriteFromMessageId);
        }
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

  // "Branch from here": copy the thread up to the message into a new thread and
  // switch to it. Branches share the project's files and drafts; copied
  // changesets are inert (history only).
  const handleBranchFrom = useCallback(
    async (messageId?: string | null) => {
      if (!conversationId) return;
      try {
        const fork = await forkCopilotConversation(
          projectId,
          conversationId,
          messageId ?? null,
        );
        setConversationId(fork.id);
        setMessages(fork.messages);
        setParentConversationId(fork.parent_conversation_id ?? null);
        setUndoAnchorId(null);
        setEditingMessageId(null);
        resetProposal();
        localStorage.setItem(activeThreadKey(projectId), fork.id);
        refreshSummaries();
      } catch (caught) {
        setError(caught instanceof Error ? caught.message : String(caught));
      }
    },
    [conversationId, projectId, refreshSummaries, resetProposal],
  );

  // Execute a rewrite (edit & resend / regenerate) on the active thread.
  const runRewriteTurn = useCallback(
    async (anchorId: string, message: string) => {
      if (!conversationId) return;
      setRewriteConfirm(null);
      await submitTurn(message, [], {
        conversationIdOverride: conversationId,
        rewriteFromMessageId: anchorId,
      });
    },
    [conversationId, submitTurn],
  );

  // Entry point for edit & resend / regenerate: consult the side-effect
  // manifest first (applied drafts stay in the project — the dialog says so).
  const beginRewrite = useCallback(
    async (anchorId: string, message: string) => {
      if (!conversationId || isRunning || !message.trim()) return;
      try {
        const preview = await getCopilotRewritePreview(
          projectId,
          conversationId,
          anchorId,
        );
        const hasSideEffects =
          preview.applied_changeset_items.length > 0 ||
          preview.unknown_applies ||
          preview.memory_write_count > 0;
        if (!hasSideEffects) {
          await runRewriteTurn(anchorId, message.trim());
          return;
        }
        setRewriteConfirm({ anchorId, message: message.trim(), preview });
      } catch (caught) {
        setError(caught instanceof Error ? caught.message : String(caught));
      }
    },
    [conversationId, isRunning, projectId, runRewriteTurn],
  );

  // "Branch instead" from the rewrite dialog: keep this thread intact; fork up
  // to the turn before the anchor (or start fresh) and send the message there.
  const branchInsteadOfRewrite = useCallback(
    async (anchorId: string, message: string) => {
      if (!conversationId) return;
      setRewriteConfirm(null);
      setEditingMessageId(null);
      const anchorIndex = messages.findIndex(item => item.id === anchorId);
      try {
        let targetId: string;
        if (anchorIndex > 0) {
          const fork = await forkCopilotConversation(
            projectId,
            conversationId,
            messages[anchorIndex - 1].id,
          );
          setConversationId(fork.id);
          setMessages(fork.messages);
          setParentConversationId(fork.parent_conversation_id ?? null);
          localStorage.setItem(activeThreadKey(projectId), fork.id);
          targetId = fork.id;
        } else {
          const created = await createCopilotConversation(projectId);
          setConversationId(created.id);
          setMessages([]);
          setParentConversationId(null);
          localStorage.setItem(activeThreadKey(projectId), created.id);
          targetId = created.id;
        }
        resetProposal();
        await submitTurn(message, [], { conversationIdOverride: targetId });
      } catch (caught) {
        setError(caught instanceof Error ? caught.message : String(caught));
      }
    },
    [conversationId, messages, projectId, resetProposal, submitTurn],
  );

  // Single-step undo of the last rewrite (until the next turn starts).
  const handleUndoRewrite = useCallback(async () => {
    if (!conversationId || !undoAnchorId || isRunning) return;
    try {
      const restored = await undoCopilotRewrite(
        projectId,
        conversationId,
        undoAnchorId,
      );
      setMessages(restored.messages);
      setUndoAnchorId(null);
      resetProposal();
      refreshSummaries();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    }
  }, [
    conversationId,
    isRunning,
    projectId,
    refreshSummaries,
    resetProposal,
    undoAnchorId,
  ]);

  // Regenerate = rewrite of the last turn with unchanged content.
  const handleRegenerate = useCallback(() => {
    if (isRunning) return;
    const lastUser = [...messages]
      .reverse()
      .find(message => message.role === 'user');
    if (lastUser?.content) {
      beginRewrite(lastUser.id, lastUser.content);
    }
  }, [beginRewrite, isRunning, messages]);

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
        refreshApplies(conversationId);
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
    refreshApplies,
    refreshSummaries,
    resetProposal,
  ]);

  // Restore an apply group's before-images ("Revert" on an Applied turn, or
  // the rewrite dialog's revert option). Excluded files are reported, never
  // silently clobbered.
  const handleRevertApply = useCallback(
    async (applyGroupId: string) => {
      if (!conversationId || isRunning || isApplying) return;
      setError(null);
      setRevertNotice(null);
      try {
        const result = await revertCopilotApply(
          projectId,
          conversationId,
          applyGroupId,
        );
        if (result.excluded.length) {
          setRevertNotice(
            t(
              'Reverted %s draft(s). Kept: %s',
              result.reverted_count,
              result.excluded
                .map(item => `${item.path} (${item.reason})`)
                .join('; '),
            ),
          );
        }
        const conversation = await getCopilotConversation(
          projectId,
          conversationId,
        );
        setMessages(conversation.messages);
        refreshApplies(conversationId);
        refreshSummaries();
        onApplied?.();
      } catch (caught) {
        setError(caught instanceof Error ? caught.message : String(caught));
      }
    },
    [
      conversationId,
      isApplying,
      isRunning,
      onApplied,
      projectId,
      refreshApplies,
      refreshSummaries,
    ],
  );

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
          {parentConversationId ? (
            <Button
              buttonSize="small"
              buttonStyle="link"
              data-test="copilot-branch-backlink"
              onClick={() => resumeConversation(parentConversationId)}
              icon={<Icons.BranchesOutlined iconSize="s" />}
            >
              {t('Branched — open original')}
            </Button>
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
              const isEditing =
                message.role === 'user' && editingMessageId === message.id;
              return (
                <Flex vertical gap={theme.sizeUnit} key={message.id}>
                  <Flex
                    justify={
                      message.role === 'user' ? 'flex-end' : 'flex-start'
                    }
                  >
                    {isEditing ? (
                      <Flex
                        vertical
                        gap={theme.sizeUnit}
                        css={css`
                          width: 90%;
                        `}
                        data-test="copilot-message-edit-form"
                      >
                        <Input.TextArea
                          autoFocus
                          autoSize={{ minRows: 2, maxRows: 8 }}
                          value={editingValue}
                          onChange={event =>
                            setEditingValue(event.target.value)
                          }
                          onKeyDown={event => {
                            if (event.key === 'Enter' && !event.shiftKey) {
                              event.preventDefault();
                              beginRewrite(message.id, editingValue);
                            }
                            if (event.key === 'Escape') {
                              setEditingMessageId(null);
                            }
                          }}
                        />
                        <Flex gap={theme.sizeUnit} justify="flex-end">
                          <Button
                            buttonSize="small"
                            buttonStyle="tertiary"
                            onClick={() => setEditingMessageId(null)}
                          >
                            {t('Cancel')}
                          </Button>
                          <Button
                            buttonSize="small"
                            buttonStyle="primary"
                            disabled={!editingValue.trim() || isRunning}
                            onClick={() =>
                              beginRewrite(message.id, editingValue)
                            }
                            data-test="copilot-message-edit-save"
                          >
                            {t('Save & resend')}
                          </Button>
                        </Flex>
                      </Flex>
                    ) : (
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
                    )}
                  </Flex>
                  {!isEditing && canWrite ? (
                    <Flex
                      justify={
                        message.role === 'user' ? 'flex-end' : 'flex-start'
                      }
                      gap={0}
                    >
                      {message.content.trim() && (
                        <Button
                          aria-label={t('Copy message')}
                          tooltip={t('Copy message')}
                          buttonSize="small"
                          buttonStyle="link"
                          onClick={() =>
                            copyTextToClipboard(() =>
                              Promise.resolve(message.content),
                            ).catch(() => {})
                          }
                          icon={<Icons.CopyOutlined iconSize="s" />}
                        />
                      )}
                      {message.role === 'assistant' &&
                        (() => {
                          const applyGroup = applies.find(
                            group =>
                              group.message_id === message.id &&
                              !group.reverted,
                          );
                          return applyGroup ? (
                            <Button
                              buttonSize="small"
                              buttonStyle="link"
                              disabled={isRunning || isApplying}
                              onClick={() =>
                                handleRevertApply(applyGroup.apply_group_id)
                              }
                              icon={<Icons.UndoOutlined iconSize="s" />}
                              data-test="copilot-revert-apply"
                            >
                              {t('Revert')}
                            </Button>
                          ) : null;
                        })()}
                      {message.role === 'user' && (
                        <Button
                          aria-label={t('Edit message')}
                          tooltip={t('Edit & resend')}
                          buttonSize="small"
                          buttonStyle="link"
                          disabled={isRunning}
                          onClick={() => {
                            setEditingMessageId(message.id);
                            setEditingValue(message.content);
                          }}
                          icon={<Icons.EditOutlined iconSize="s" />}
                        />
                      )}
                      {message.role === 'assistant' &&
                        message.id === lastAssistantId && (
                          <Button
                            aria-label={t('Regenerate')}
                            tooltip={t('Regenerate response')}
                            buttonSize="small"
                            buttonStyle="link"
                            disabled={isRunning}
                            onClick={handleRegenerate}
                            icon={<Icons.ReloadOutlined iconSize="s" />}
                          />
                        )}
                      <Button
                        aria-label={t('Branch from here')}
                        tooltip={t(
                          'Branch from here — continue in a copy of this ' +
                            'thread up to this message',
                        )}
                        buttonSize="small"
                        buttonStyle="link"
                        disabled={isRunning}
                        onClick={() => handleBranchFrom(message.id)}
                        icon={<Icons.BranchesOutlined iconSize="s" />}
                      />
                    </Flex>
                  ) : null}
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
                {liveSteps.map((step, index) => {
                  const running = step.status === 'running';
                  const duration = formatDuration(step.duration_ms);
                  return (
                    <Typography.Text
                      key={step.step_id || `live-${step.kind}-${index}`}
                      type={step.status === 'error' ? 'danger' : 'secondary'}
                      css={css`
                        padding-left: ${theme.sizeUnit * 2}px;
                      `}
                    >
                      {running ? (
                        <>
                          <Icons.LoadingOutlined /> {step.summary}
                          <ElapsedSeconds />
                        </>
                      ) : (
                        <>
                          {step.summary}
                          {duration ? ` — ${duration}` : null}
                        </>
                      )}
                      {running && step.progressNote ? (
                        <Typography.Text
                          type="secondary"
                          css={css`
                            display: block;
                            padding-left: ${theme.sizeUnit * 4}px;
                            font-size: ${theme.fontSizeSM}px;
                          `}
                        >
                          {step.progressNote}
                        </Typography.Text>
                      ) : null}
                    </Typography.Text>
                  );
                })}
              </Flex>
            ) : null}

            {error ? (
              <Alert type="error" showIcon message={error} closable />
            ) : null}

            {revertNotice ? (
              <Alert
                type="info"
                showIcon
                message={revertNotice}
                closable
                onClose={() => setRevertNotice(null)}
                data-test="copilot-revert-notice"
              />
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
                            {formatDuration(step.duration_ms)
                              ? ` — ${formatDuration(step.duration_ms)}`
                              : null}
                          </Typography.Text>
                        ))}
                      </Flex>
                    ),
                  },
                ]}
              />
            ) : null}
          </Flex>

          {undoAnchorId && !isRunning ? (
            <Flex
              align="center"
              justify="space-between"
              gap={theme.sizeUnit}
              css={css`
                border-top: 1px solid ${theme.colorBorderSecondary};
                padding: ${theme.sizeUnit}px ${theme.sizeUnit * 2}px;
                background: ${theme.colorBgLayout};
              `}
              data-test="copilot-rewrite-undo-bar"
            >
              <Typography.Text type="secondary">
                {t('Conversation edited — earlier turns were replaced.')}
              </Typography.Text>
              <Button
                buttonSize="small"
                buttonStyle="link"
                onClick={handleUndoRewrite}
                icon={<Icons.UndoOutlined iconSize="s" />}
              >
                {t('Undo')}
              </Button>
            </Flex>
          ) : null}

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

      <RewriteConfirmModal
        open={Boolean(rewriteConfirm)}
        preview={rewriteConfirm?.preview ?? null}
        canRevertApplied={Boolean(
          rewriteConfirm?.preview.apply_group_ids.length,
        )}
        onConfirm={async (_removeLearned, revertApplied) => {
          if (!rewriteConfirm) {
            return;
          }
          if (revertApplied) {
            // Restore before-images first so the rewritten turn starts from
            // the pre-apply project state (sequential; failures surface).
            // eslint-disable-next-line no-restricted-syntax
            for (const groupId of rewriteConfirm.preview.apply_group_ids) {
              // eslint-disable-next-line no-await-in-loop
              await handleRevertApply(groupId);
            }
          }
          runRewriteTurn(rewriteConfirm.anchorId, rewriteConfirm.message);
        }}
        onBranch={() => {
          if (rewriteConfirm) {
            branchInsteadOfRewrite(
              rewriteConfirm.anchorId,
              rewriteConfirm.message,
            );
          }
        }}
        onCancel={() => setRewriteConfirm(null)}
      />
      <CopilotInspectorDialog
        open={inspectorOpen}
        inspector={inspector}
        onClose={() => setInspectorOpen(false)}
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
