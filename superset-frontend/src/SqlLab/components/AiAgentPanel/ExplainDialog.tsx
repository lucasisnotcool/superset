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
import { t } from '@apache-superset/core/translation';
import { css, styled } from '@apache-superset/core/theme';
import { Modal, Typography } from '@superset-ui/core/components';
import type { AgentStep } from './api';
import AgentStepDetail from './AgentStepDetail';

// Human labels for each graph node (api.ts KNOWN_AGENT_STEP_KINDS). An unknown
// kind falls back to its raw name so a new backend node still renders.
const STEP_LABELS: Record<string, string> = {
  load_conversation: t('Loaded conversation'),
  classify_intent: t('Classified intent'),
  answer_directly: t('Answered directly'),
  load_context: t('Loaded schema context'),
  load_wren_context: t('Retrieved semantic context'),
  draft_sql: t('Drafted SQL'),
  draft_response: t('Drafted response'),
  approved_sql: t('Approved SQL'),
  dry_plan_with_wren: t('Engine dry-plan'),
  plan_semantic_sql: t('Rewrote semantic SQL'),
  validate_sql: t('Validated SQL'),
  repair_sql: t('Repaired SQL'),
  correct_semantic_sql: t('Corrected semantic SQL'),
  execute_sql: t('Executed SQL'),
  duplicate_sql: t('Skipped duplicate SQL'),
  build_artifacts: t('Built analytics artifacts'),
  reflect_sql_outcome: t('Reflected on result'),
  conversation_error: t('Error'),
  agent_error: t('Error'),
};

const StepRow = styled.div`
  ${({ theme }) => css`
    display: flex;
    gap: ${theme.sizeUnit * 2}px;
    padding: ${theme.sizeUnit * 2}px 0;
    border-bottom: 1px solid ${theme.colorBorderSecondary};

    &:last-of-type {
      border-bottom: none;
    }
  `}
`;

const Dot = styled.span<{ status: AgentStep['status'] }>`
  ${({ theme, status }) => {
    const color =
      status === 'error'
        ? theme.colorError
        : status === 'warning'
          ? theme.colorWarning
          : theme.colorSuccess;
    return css`
      flex: 0 0 auto;
      width: ${theme.sizeUnit * 2}px;
      height: ${theme.sizeUnit * 2}px;
      margin-top: ${theme.sizeUnit}px;
      border-radius: 50%;
      background: ${color};
    `;
  }}
`;

const StepBody = styled.div`
  flex: 1 1 auto;
  min-width: 0;
`;

const StepHeader = styled.div`
  ${({ theme }) => css`
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    gap: ${theme.sizeUnit * 2}px;
  `}
`;

const Duration = styled(Typography.Text)`
  ${({ theme }) => css`
    color: ${theme.colorTextTertiary};
    font-size: ${theme.fontSizeSM}px;
    white-space: nowrap;
  `}
`;

const AttemptHeading = styled(Typography.Text)`
  ${({ theme }) => css`
    display: block;
    margin: ${theme.sizeUnit * 3}px 0 ${theme.sizeUnit}px;
    color: ${theme.colorTextSecondary};
    font-weight: ${theme.fontWeightStrong};
  `}
`;

const UserMessage = styled.div`
  ${({ theme }) => css`
    margin-bottom: ${theme.sizeUnit * 3}px;
    padding: ${theme.sizeUnit * 2}px;
    background: ${theme.colorBgLayout};
    border-radius: ${theme.borderRadius}px;
  `}
`;

const stepLabel = (kind: string): string => STEP_LABELS[kind] ?? kind;

const formatDuration = (ms?: number | null): string | null => {
  if (ms === null || ms === undefined) {
    return null;
  }
  if (ms < 1000) {
    return t('%s ms', ms);
  }
  return t('%s s', (ms / 1000).toFixed(1));
};

export interface ExplainDialogProps {
  open: boolean;
  onClose: () => void;
  userMessage?: string | null;
  steps: AgentStep[];
}

export default function ExplainDialog({
  open,
  onClose,
  userMessage,
  steps,
}: ExplainDialogProps) {
  // Group steps into SQL attempts so retries read as separate cycles (Seam 5).
  const attempts = new Map<number, AgentStep[]>();
  steps.forEach(step => {
    const list = attempts.get(step.attempt_index) ?? [];
    list.push(step);
    attempts.set(step.attempt_index, list);
  });
  const orderedAttempts = Array.from(attempts.keys()).sort((a, b) => a - b);
  const multiAttempt = orderedAttempts.length > 1;

  return (
    <Modal
      show={open}
      onHide={onClose}
      title={t('How this answer was produced')}
      hideFooter
      destroyOnHidden
      responsive
      data-test="explain-dialog"
    >
      {userMessage ? (
        <UserMessage data-test="explain-user-message">
          <Typography.Text strong>{userMessage}</Typography.Text>
        </UserMessage>
      ) : null}
      {steps.length === 0 ? (
        <Typography.Text type="secondary">
          {t('No timeline is available for this message yet.')}
        </Typography.Text>
      ) : null}
      {orderedAttempts.map(attempt => (
        <div key={attempt} data-test="explain-attempt">
          {multiAttempt ? (
            <AttemptHeading>{t('Attempt %s', attempt + 1)}</AttemptHeading>
          ) : null}
          {(attempts.get(attempt) ?? []).map((step, index) => {
            const duration = formatDuration(step.duration_ms);
            return (
              <StepRow
                key={`${step.kind}-${attempt}-${index}`}
                data-test="explain-step"
              >
                <Dot status={step.status} />
                <StepBody>
                  <StepHeader>
                    <Typography.Text strong>
                      {stepLabel(step.kind)}
                    </Typography.Text>
                    {duration ? <Duration>{duration}</Duration> : null}
                  </StepHeader>
                  <Typography.Paragraph type="secondary" style={{ margin: 0 }}>
                    {step.summary}
                  </Typography.Paragraph>
                  <AgentStepDetail step={step} />
                </StepBody>
              </StepRow>
            );
          })}
        </div>
      ))}
    </Modal>
  );
}
