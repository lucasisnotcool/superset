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
import { useTheme } from '@apache-superset/core/theme';
import { Button, Flex, Modal } from '@superset-ui/core/components';
import RecoverySuggestionsContent from './RecoverySuggestionsContent';

export interface RecoverySuggestionsDialogProps {
  projectId: string;
  /** Coverage run whose recovery suggestions to review. */
  runId?: string | null;
  open: boolean;
  canWrite?: boolean;
  onClose: () => void;
  /** Called after suggestions are applied (drafts created) so callers refresh. */
  onApplied?: () => void;
}

/**
 * Standalone dialog for the recovery suggestions, used by the persistent banner
 * entrypoint (when the Coverage report dialog is not already open). When the
 * report dialog IS open, the suggestions render inline as a second pane inside it
 * instead — see CoveragePanel — so the two never stack as nested modals.
 */
const RecoverySuggestionsDialog = ({
  projectId,
  runId,
  open,
  canWrite = true,
  onClose,
  onApplied,
}: RecoverySuggestionsDialogProps) => {
  const theme = useTheme();
  return (
    <Modal
      show={open}
      onHide={onClose}
      title={t('Coverage suggestions')}
      footer={
        <Flex justify="end" gap={theme.sizeUnit * 2}>
          <Button onClick={onClose} data-test="recovery-close">
            {t('Close')}
          </Button>
        </Flex>
      }
      data-test="recovery-dialog"
    >
      <RecoverySuggestionsContent
        projectId={projectId}
        runId={runId}
        active={open}
        canWrite={canWrite}
        onApplied={onApplied}
      />
    </Modal>
  );
};

export default RecoverySuggestionsDialog;
