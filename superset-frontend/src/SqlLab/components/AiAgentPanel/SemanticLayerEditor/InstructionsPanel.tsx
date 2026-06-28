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
import { useCallback, useEffect, useState } from 'react';
import { useDispatch } from 'react-redux';
import { t } from '@apache-superset/core/translation';
import { css, styled } from '@apache-superset/core/theme';
import { Alert } from '@apache-superset/core/components';
import {
  Button,
  Empty,
  Flex,
  Input,
  List,
  Popconfirm,
  Switch,
  Tooltip,
  Typography,
} from '@superset-ui/core/components';
import { Icons } from '@superset-ui/core/components/Icons';
import {
  addDangerToast,
  addSuccessToast,
} from 'src/components/MessageToasts/actions';
import {
  ConversationScope,
  createInstruction,
  deleteInstruction,
  Instruction,
  listInstructions,
} from '../api';

const PanelRoot = styled.div`
  ${({ theme }) => css`
    display: flex;
    min-height: 0;
    flex: 1;
    flex-direction: column;
    gap: ${theme.sizeUnit * 3}px;
    padding: ${theme.sizeUnit * 3}px;
    overflow: auto;
  `}
`;

// "Always apply" instructions are injected for every question in this schema;
// the rest are recalled only when similar to the question (see backend
// instructions store). The copy avoids the word "global" because the flag is
// scope-bound, not cross-database.
const ALWAYS_APPLY_HELP = t(
  'Always-apply instructions are injected into every question for this schema. ' +
    'Others are used only when relevant to the question.',
);

export interface InstructionsPanelProps {
  scope: ConversationScope;
  canWrite: boolean;
}

export default function InstructionsPanel({
  scope,
  canWrite,
}: InstructionsPanelProps) {
  const dispatch = useDispatch();
  const [instructions, setInstructions] = useState<Instruction[]>([]);
  const [draft, setDraft] = useState('');
  const [isGlobal, setIsGlobal] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [isSaving, setIsSaving] = useState(false);

  const hasScope = Boolean(scope.schema_name);

  const refresh = useCallback(async () => {
    if (!scope.schema_name) {
      setInstructions([]);
      return;
    }
    setIsLoading(true);
    try {
      setInstructions(await listInstructions(scope));
    } catch (ex) {
      dispatch(
        addDangerToast(
          ex instanceof Error ? ex.message : t('Unable to load instructions'),
        ),
      );
    } finally {
      setIsLoading(false);
    }
  }, [scope, dispatch]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const addInstruction = async () => {
    const text = draft.trim();
    if (!text) {
      return;
    }
    setIsSaving(true);
    try {
      await createInstruction(scope, text, isGlobal);
      setDraft('');
      setIsGlobal(false);
      dispatch(addSuccessToast(t('Instruction added.')));
      await refresh();
    } catch (ex) {
      dispatch(
        addDangerToast(
          ex instanceof Error ? ex.message : t('Unable to add instruction'),
        ),
      );
    } finally {
      setIsSaving(false);
    }
  };

  const removeInstruction = async (instruction: Instruction) => {
    try {
      await deleteInstruction(instruction.id);
      dispatch(addSuccessToast(t('Instruction deleted.')));
      await refresh();
    } catch (ex) {
      dispatch(
        addDangerToast(
          ex instanceof Error ? ex.message : t('Unable to delete instruction'),
        ),
      );
    }
  };

  if (!hasScope) {
    return (
      <PanelRoot data-test="semantic-layer-instructions">
        <Alert type="warning" message={t('Select a database and schema.')} />
      </PanelRoot>
    );
  }

  return (
    <PanelRoot data-test="semantic-layer-instructions">
      {/*
        DP-NEW: a project's documents, models, and history are shared with everyone
        who has access to its database, but instructions are deliberately personal
        (like the agent's SQL memory). Say so plainly so a shared-project user is
        never surprised that teammates can't see — or aren't steered by — these.
      */}
      <Alert
        type="info"
        showIcon
        data-test="instructions-personal-note"
        message={t(
          'Your instructions are personal. They steer SQL generation for this ' +
            'schema for you only — other users with access to this database ' +
            "don't see or share them (unlike the project's documents and models).",
        )}
      />
      <Typography.Paragraph type="secondary" style={{ margin: 0 }}>
        {t('Only your own instructions are listed below.')}
      </Typography.Paragraph>
      {canWrite && (
        <Flex vertical gap="small">
          <Input.TextArea
            data-test="instruction-input"
            value={draft}
            disabled={isSaving}
            autoSize={{ minRows: 2, maxRows: 6 }}
            placeholder={t(
              'e.g. Always filter out test accounts (is_test = false).',
            )}
            onChange={event => setDraft(event.target.value)}
          />
          <Flex justify="space-between" align="center" gap="small" wrap="wrap">
            <Tooltip title={ALWAYS_APPLY_HELP}>
              <Flex gap="small" align="center">
                <Switch
                  size="small"
                  checked={isGlobal}
                  disabled={isSaving}
                  aria-label={t('Always apply')}
                  onChange={setIsGlobal}
                />
                <Typography.Text>{t('Always apply')}</Typography.Text>
              </Flex>
            </Tooltip>
            <Button
              buttonStyle="primary"
              loading={isSaving}
              disabled={isSaving || !draft.trim()}
              onClick={addInstruction}
              icon={<Icons.PlusOutlined iconSize="m" />}
            >
              {t('Add instruction')}
            </Button>
          </Flex>
        </Flex>
      )}
      <List
        loading={isLoading}
        dataSource={instructions}
        locale={{
          emptyText: (
            <Empty
              image={Empty.PRESENTED_IMAGE_SIMPLE}
              description={t('No instructions yet.')}
            />
          ),
        }}
        renderItem={(item: Instruction) => (
          <List.Item
            key={item.id}
            actions={
              canWrite
                ? [
                    <Popconfirm
                      key="delete"
                      title={t('Delete this instruction?')}
                      okText={t('Delete')}
                      cancelText={t('Cancel')}
                      onConfirm={() => removeInstruction(item)}
                    >
                      <Button
                        buttonStyle="link"
                        aria-label={t('Delete instruction')}
                        icon={<Icons.DeleteOutlined iconSize="m" />}
                      />
                    </Popconfirm>,
                  ]
                : undefined
            }
          >
            <Flex vertical gap={0}>
              <Typography.Text>{item.instruction}</Typography.Text>
              {item.is_global && (
                <Typography.Text type="secondary">
                  {t('Always applied')}
                </Typography.Text>
              )}
            </Flex>
          </List.Item>
        )}
      />
    </PanelRoot>
  );
}
