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
import { useEffect, useState } from 'react';
import { useSelector } from 'react-redux';
import { t } from '@apache-superset/core/translation';
import { css, useTheme } from '@apache-superset/core/theme';
import { SupersetClient } from '@superset-ui/core';
import { Button, Flex, Modal, Typography } from '@superset-ui/core/components';
import { Icons } from '@superset-ui/core/components/Icons';
import type { UserWithPermissionsAndRoles } from 'src/types/bootstrapTypes';
import type { MyDatabaseGrant } from './types';
import { connectionSignature } from './utils';

/**
 * Persistent post-login notice: an administrator pre-approved this user for
 * one or more database connections. Mounted globally (dashboards, Explore,
 * SQL Lab are one SPA), it fetches the caller's unacknowledged grants on app
 * load — the endpoint also lazily claims pending grants, so a grant issued
 * mid-session becomes live here — and blocks with a non-dismissable modal
 * until the user explicitly acknowledges. Acknowledgment is server-persisted,
 * so the notice follows the user across browsers and devices and never
 * re-nags after "Got it".
 *
 * Every failure path is silent by design: this notice must never break or
 * block app load (e.g. 403 for roles without the self-service grant perms).
 */
export default function DatabaseGrantNotice() {
  const theme = useTheme();
  const userId = useSelector<
    { user: UserWithPermissionsAndRoles | undefined },
    number | string | undefined
  >(state => state.user?.userId);
  const [grants, setGrants] = useState<MyDatabaseGrant[]>([]);
  const [acknowledging, setAcknowledging] = useState<boolean>(false);

  useEffect(() => {
    if (!userId) {
      // Anonymous/embedded sessions have no grants to claim.
      return undefined;
    }
    let cancelled = false;
    SupersetClient.get({ endpoint: '/api/v1/database_grant/mine' })
      .then(({ json }) => {
        if (!cancelled) {
          setGrants((json as { result: MyDatabaseGrant[] }).result ?? []);
        }
      })
      .catch(() => {
        // Fail silent: no permission, endpoint unavailable, network error.
      });
    return () => {
      cancelled = true;
    };
  }, [userId]);

  if (grants.length === 0) {
    return null;
  }

  const acknowledge = async () => {
    setAcknowledging(true);
    try {
      await SupersetClient.post({
        endpoint: '/api/v1/database_grant/acknowledge',
        jsonPayload: { ids: grants.map(grant => grant.id) },
      });
      setGrants([]);
    } catch {
      // Keep the dialog up — it is persistent until acknowledged — but let
      // the user retry.
      setAcknowledging(false);
    }
  };

  return (
    <Modal
      show
      onHide={() => {}}
      closable={false}
      maskClosable={false}
      centered
      title={t('You have been granted database access')}
      name="database-grant-notice"
      footer={
        <Button
          buttonStyle="primary"
          onClick={acknowledge}
          loading={acknowledging}
          data-test="grant-notice-acknowledge"
        >
          {t('Got it')}
        </Button>
      }
    >
      <Flex vertical gap="middle" data-test="database-grant-notice-body">
        <Typography.Text>
          {t(
            'An administrator has granted you access to the following ' +
              'database(s). You can use them in SQL Lab and MDL Lab right ' +
              'away — no credentials needed.',
          )}
        </Typography.Text>
        {grants.map(grant => (
          <Flex
            key={grant.id}
            gap="small"
            align="flex-start"
            data-test={`grant-notice-item-${grant.id}`}
            css={css`
              border: 1px solid ${theme.colorBorderSecondary};
              border-radius: ${theme.borderRadius}px;
              padding: ${theme.sizeUnit * 3}px;
            `}
          >
            <Icons.DatabaseOutlined iconSize="l" iconColor={theme.colorInfo} />
            <Flex vertical>
              <Typography.Text strong>{grant.database_name}</Typography.Text>
              <Typography.Text type="secondary">
                {connectionSignature(grant)}
                {grant.backend ? ` (${grant.backend})` : ''}
              </Typography.Text>
              {grant.granted_on && (
                <Typography.Text type="secondary">
                  {t('Granted on %s', grant.granted_on.slice(0, 10))}
                </Typography.Text>
              )}
            </Flex>
          </Flex>
        ))}
      </Flex>
    </Modal>
  );
}
