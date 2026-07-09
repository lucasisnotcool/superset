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
import { useState } from 'react';
import { t } from '@apache-superset/core/translation';
import { Alert } from '@apache-superset/core/components';
import {
  Button,
  Checkbox,
  Flex,
  Modal,
  Typography,
} from '@superset-ui/core/components';
import type { RewritePreview } from './api';

export interface RewriteConfirmModalProps {
  open: boolean;
  preview: RewritePreview | null;
  /** Run the rewrite (truncate + resend). `removeLearnedExamples` reflects the
   * checkbox (SQL agent only; the Copilot has no learned examples);
   * `revertApplied` reflects the revert-applied-drafts checkbox (Copilot). */
  onConfirm: (removeLearnedExamples: boolean, revertApplied: boolean) => void;
  /** Leave this thread untouched and continue in a new branch instead. */
  onBranch: () => void;
  onCancel: () => void;
  /** Offer "also revert the applied drafts" (only when the caller can revert —
   * i.e. the manifest's apply groups have recorded before-images). */
  canRevertApplied?: boolean;
}

/**
 * Side-effect confirmation for edit & resend / regenerate.
 *
 * Renders the rewrite-preview manifest verbatim: what the rewrite removes and
 * which durable side effects stay behind (applied MDL drafts). Only shown when
 * the manifest is non-empty — a clean rewrite runs without ceremony, matching
 * consumer chat apps.
 */
const RewriteConfirmModal = ({
  open,
  preview,
  onConfirm,
  onBranch,
  onCancel,
  canRevertApplied = false,
}: RewriteConfirmModalProps) => {
  const [removeLearned, setRemoveLearned] = useState(true);
  const [revertApplied, setRevertApplied] = useState(false);
  if (!preview) {
    return null;
  }
  const appliedItems = preview.applied_changeset_items;
  return (
    <Modal
      show={open}
      onHide={onCancel}
      title={t('Rewrite this conversation?')}
      responsive
      footer={
        <Flex justify="end" gap="small">
          <Button buttonStyle="tertiary" onClick={onCancel}>
            {t('Cancel')}
          </Button>
          <Button
            buttonStyle="secondary"
            onClick={onBranch}
            data-test="rewrite-branch-instead"
          >
            {t('Branch instead')}
          </Button>
          <Button
            buttonStyle="primary"
            onClick={() => onConfirm(removeLearned, revertApplied)}
            data-test="rewrite-confirm"
          >
            {t('Rewrite')}
          </Button>
        </Flex>
      }
    >
      <Flex vertical gap="middle">
        <Typography.Text>
          {t(
            'Resending will remove %s later message(s) from this conversation.',
            preview.removed_message_count,
          )}
        </Typography.Text>
        {appliedItems.length > 0 && (
          <Alert
            type="warning"
            message={t('Applied drafts stay in the project')}
            description={
              <Flex vertical gap="small">
                <Typography.Text>
                  {t(
                    'Turns being removed already applied these changes as ' +
                      'drafts. Removing the messages does not undo them:',
                  )}
                </Typography.Text>
                <ul data-test="rewrite-applied-items">
                  {appliedItems.map(item => (
                    <li key={`${item.op}-${item.path}`}>
                      <Typography.Text code>{item.op}</Typography.Text>{' '}
                      {item.path}
                    </li>
                  ))}
                </ul>
              </Flex>
            }
          />
        )}
        {canRevertApplied && appliedItems.length > 0 && (
          <Checkbox
            checked={revertApplied}
            onChange={event => setRevertApplied(event.target.checked)}
            data-test="rewrite-revert-applied"
          >
            {t(
              'Also revert these applied drafts (activated or since-edited ' +
                'files are kept and reported)',
            )}
          </Checkbox>
        )}
        {preview.unknown_applies && (
          <Alert
            type="warning"
            message={t(
              'Some of the removed turns applied drafts to the project. ' +
                'Those drafts stay applied and cannot be listed here.',
            )}
          />
        )}
        {preview.memory_write_count > 0 && (
          <Checkbox
            checked={removeLearned}
            onChange={event => setRemoveLearned(event.target.checked)}
            data-test="rewrite-remove-learned"
          >
            {t(
              'Also remove %s learned example(s) these turns produced',
              preview.memory_write_count,
            )}
          </Checkbox>
        )}
      </Flex>
    </Modal>
  );
};

export default RewriteConfirmModal;
