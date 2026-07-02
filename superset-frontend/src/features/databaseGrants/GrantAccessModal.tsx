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
import { useMemo, useState } from 'react';
import { t } from '@apache-superset/core/translation';
import { css, useTheme } from '@apache-superset/core/theme';
import { Alert } from '@apache-superset/core/components';
import { getClientErrorObject, SupersetClient } from '@superset-ui/core';
import { Flex, Input, Modal, Typography } from '@superset-ui/core/components';
import { DatabaseSelector } from 'src/components';
import type { DatabaseObject } from 'src/components/DatabaseSelector/types';
import type { GrantCreationResult } from './types';
import { parseUsernames } from './utils';

export interface GrantAccessModalProps {
  show: boolean;
  onHide: () => void;
  /** Called after a successful grant so the list can refresh. */
  onGranted: () => void;
  addSuccessToast: (msg: string) => void;
  addDangerToast: (msg: string) => void;
}

/**
 * Admin pre-approval form: pick a database, paste usernames. Any user that
 * signs in (or already exists) with a listed username gains access to the
 * connection and everything database-scoped without entering credentials.
 */
export default function GrantAccessModal({
  show,
  onHide,
  onGranted,
  addSuccessToast,
  addDangerToast,
}: GrantAccessModalProps) {
  const theme = useTheme();
  const [database, setDatabase] = useState<DatabaseObject | null>(null);
  const [rawUsernames, setRawUsernames] = useState<string>('');
  const [submitting, setSubmitting] = useState<boolean>(false);

  const usernames = useMemo(() => parseUsernames(rawUsernames), [rawUsernames]);

  const reset = () => {
    setDatabase(null);
    setRawUsernames('');
  };

  const handleHide = () => {
    reset();
    onHide();
  };

  const handleSubmit = async () => {
    if (!database || usernames.length === 0) {
      return;
    }
    setSubmitting(true);
    try {
      const response = await SupersetClient.post({
        endpoint: '/api/v1/database_grant/',
        jsonPayload: { database_id: database.id, usernames },
      });
      const result = (response.json as { result: GrantCreationResult }).result;
      const parts = [
        t('%s username(s) granted', result.created.length),
        ...(result.skipped.length
          ? [t('%s already granted (skipped)', result.skipped.length)]
          : []),
        ...(result.claimed_usernames.length
          ? [
              t(
                '%s existing account(s) received access immediately',
                result.claimed_usernames.length,
              ),
            ]
          : []),
      ];
      addSuccessToast(parts.join('; '));
      reset();
      onGranted();
      onHide();
    } catch (error) {
      const clientError = await getClientErrorObject(error);
      addDangerToast(
        t(
          'There was an issue granting access: %s',
          clientError.message || clientError.error || '',
        ),
      );
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Modal
      show={show}
      onHide={handleHide}
      title={t('Grant database access')}
      primaryButtonName={t('Grant access')}
      onHandledPrimaryAction={handleSubmit}
      disablePrimaryButton={!database || usernames.length === 0 || submitting}
      primaryButtonLoading={submitting}
      width="600px"
      name="grant-access-modal"
    >
      <Flex vertical gap="middle" data-test="grant-access-modal-body">
        <Alert
          type="info"
          showIcon
          data-test="grant-access-trust-note"
          message={t(
            'Anyone who signs in with a listed username will receive access ' +
              'to this database. Only paste usernames from your identity ' +
              'provider (usernames are email addresses on SSO).',
          )}
        />
        <div>
          <Typography.Text strong>{t('Database')}</Typography.Text>
          <DatabaseSelector
            db={database}
            onDbChange={setDatabase}
            handleError={addDangerToast}
            formMode
          />
        </div>
        <div>
          <Typography.Text strong>{t('Usernames')}</Typography.Text>
          <Input.TextArea
            rows={6}
            value={rawUsernames}
            onChange={event => setRawUsernames(event.target.value)}
            placeholder={t(
              'Paste usernames separated by newlines, commas, or spaces',
            )}
            data-test="grant-access-usernames"
          />
          <div
            css={css`
              margin-top: ${theme.sizeUnit}px;
            `}
          >
            <Typography.Text
              type="secondary"
              data-test="grant-access-username-count"
            >
              {t('%s unique username(s) detected', usernames.length)}
            </Typography.Text>
          </div>
        </div>
      </Flex>
    </Modal>
  );
}
