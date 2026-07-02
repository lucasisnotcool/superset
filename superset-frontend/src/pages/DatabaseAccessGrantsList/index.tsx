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
import { SupersetClient } from '@superset-ui/core';
import { useCallback, useMemo, useState } from 'react';
import {
  ConfirmStatusChange,
  Tag,
  Tooltip,
} from '@superset-ui/core/components';
import {
  ListView,
  ListViewFilterOperator as FilterOperator,
  type ListViewProps,
  type ListViewFilters,
} from 'src/components';
import { Icons } from '@superset-ui/core/components/Icons';
import withToasts from 'src/components/MessageToasts/withToasts';
import SubMenu, { SubMenuProps } from 'src/features/home/SubMenu';
import rison from 'rison';
import { useListViewResource } from 'src/views/CRUD/hooks';
import GrantAccessModal from 'src/features/databaseGrants/GrantAccessModal';
import type {
  DatabaseGrant,
  GrantStatus,
} from 'src/features/databaseGrants/types';
import { grantStatus } from 'src/features/databaseGrants/utils';
import { createErrorHandler, createFetchRelated } from 'src/views/CRUD/utils';

const PAGE_SIZE = 25;

const STATUS_LABELS: Record<GrantStatus, string> = {
  pending: t('Pending'),
  claimed: t('Claimed'),
  acknowledged: t('Acknowledged'),
};

const STATUS_COLORS: Record<GrantStatus, string> = {
  pending: 'default',
  claimed: 'processing',
  acknowledged: 'success',
};

interface DatabaseAccessGrantsListProps {
  addDangerToast: (msg: string) => void;
  addSuccessToast: (msg: string) => void;
  user: {
    userId: string | number;
    firstName: string;
    lastName: string;
  };
}

function DatabaseAccessGrantsList({
  addDangerToast,
  addSuccessToast,
  user,
}: DatabaseAccessGrantsListProps) {
  const [grantModalOpen, setGrantModalOpen] = useState<boolean>(false);

  const {
    state: {
      loading,
      resourceCount: grantsCount,
      resourceCollection: grants,
      bulkSelectEnabled,
    },
    hasPerm,
    fetchData,
    refreshData,
    toggleBulkSelect,
  } = useListViewResource<DatabaseGrant>(
    'database_grant',
    t('Database Access Grants'),
    addDangerToast,
    true,
    undefined,
    undefined,
    true,
  );

  const canWrite = hasPerm('can_write');

  const handleRevoke = useCallback(
    (grant: DatabaseGrant) =>
      SupersetClient.delete({
        endpoint: `/api/v1/database_grant/${grant.id}`,
      }).then(
        () => {
          refreshData();
          addSuccessToast(t('Revoked access for %s', grant.username));
        },
        createErrorHandler(errMsg =>
          addDangerToast(
            t('There was an issue revoking the grant: %s', errMsg),
          ),
        ),
      ),
    [refreshData, addSuccessToast, addDangerToast],
  );

  const handleBulkRevoke = (grantsToRevoke: DatabaseGrant[]) => {
    const ids = grantsToRevoke.map(({ id }) => id);
    return SupersetClient.delete({
      endpoint: `/api/v1/database_grant/?q=${rison.encode(ids)}`,
    }).then(
      () => {
        refreshData();
        addSuccessToast(t('Revoked %s grant(s)', ids.length));
      },
      createErrorHandler(errMsg =>
        addDangerToast(t('There was an issue revoking grants: %s', errMsg)),
      ),
    );
  };

  const columns = useMemo(
    () => [
      {
        accessor: 'username',
        Header: t('Username'),
        size: 'xl',
        id: 'username',
      },
      {
        Cell: ({
          row: {
            original: { database },
          },
        }: {
          row: { original: DatabaseGrant };
        }) => <span>{database?.database_name ?? ''}</span>,
        Header: t('Database'),
        accessor: 'database_id',
        size: 'xl',
        id: 'database_id',
      },
      {
        Cell: ({ row: { original } }: { row: { original: DatabaseGrant } }) => {
          const status = grantStatus(original);
          return (
            <Tag color={STATUS_COLORS[status]} data-test="grant-status-tag">
              {STATUS_LABELS[status]}
            </Tag>
          );
        },
        Header: t('Status'),
        id: 'status',
        disableSortBy: true,
        size: 'lg',
      },
      {
        Cell: ({
          row: {
            original: { created_by: createdBy },
          },
        }: {
          row: { original: DatabaseGrant };
        }) => (
          <span>
            {createdBy ? `${createdBy.first_name} ${createdBy.last_name}` : ''}
          </span>
        ),
        Header: t('Granted by'),
        id: 'created_by',
        disableSortBy: true,
        size: 'lg',
      },
      {
        accessor: 'changed_on_delta_humanized',
        Header: t('Last modified'),
        size: 'lg',
        id: 'changed_on_delta_humanized',
      },
      {
        Cell: ({ row: { original } }: { row: { original: DatabaseGrant } }) => (
          <div className="actions">
            {canWrite && (
              <ConfirmStatusChange
                title={t('Please confirm')}
                description={
                  <>
                    {t('Are you sure you want to revoke access for')}{' '}
                    <b>{original.username}</b>?
                  </>
                }
                onConfirm={() => handleRevoke(original)}
              >
                {confirmRevoke => (
                  <Tooltip
                    id="revoke-action-tooltip"
                    title={t('Revoke')}
                    placement="bottom"
                  >
                    <span
                      role="button"
                      tabIndex={0}
                      className="action-button"
                      onClick={confirmRevoke}
                    >
                      <Icons.DeleteOutlined
                        data-test="grant-revoke-icon"
                        iconSize="l"
                      />
                    </span>
                  </Tooltip>
                )}
              </ConfirmStatusChange>
            )}
          </div>
        ),
        Header: t('Actions'),
        id: 'actions',
        hidden: !canWrite,
        disableSortBy: true,
        size: 'lg',
      },
      // Hidden column backing the "Database" relation filter (ListView
      // requires every filter id to exist among column accessors).
      {
        accessor: 'database',
        hidden: true,
        id: 'database',
      },
    ],
    [canWrite, handleRevoke],
  );

  const filters: ListViewFilters = useMemo(
    () => [
      {
        Header: t('Username'),
        key: 'search',
        id: 'username',
        input: 'search',
        operator: FilterOperator.Contains,
        inputName: 'grant_list_search',
      },
      {
        Header: t('Database'),
        key: 'database',
        id: 'database',
        input: 'select',
        operator: FilterOperator.RelationOneMany,
        unfilteredLabel: t('All'),
        fetchSelects: createFetchRelated(
          'database_grant',
          'database',
          createErrorHandler(errMsg =>
            t('An error occurred while fetching databases: %s', errMsg),
          ),
          user,
        ),
        paginate: true,
      },
    ],
    [user],
  );

  const emptyState = {
    title: t('No grants yet'),
    image: 'filter-results.svg',
    buttonAction: () => setGrantModalOpen(true),
    buttonIcon: canWrite ? (
      <Icons.PlusOutlined iconSize="m" data-test="add-grant-empty" />
    ) : undefined,
    buttonText: canWrite ? t('Grant access') : null,
  };

  const initialSort = [{ id: 'changed_on_delta_humanized', desc: true }];

  const subMenuButtons: SubMenuProps['buttons'] = [];
  if (canWrite) {
    subMenuButtons.push({
      name: t('Bulk select'),
      buttonStyle: 'secondary',
      'data-test': 'bulk-select',
      onClick: toggleBulkSelect,
    });
    subMenuButtons.push({
      name: t('Grant access'),
      icon: <Icons.PlusOutlined iconSize="m" data-test="add-grant" />,
      buttonStyle: 'primary',
      onClick: () => setGrantModalOpen(true),
    });
  }

  return (
    <>
      <SubMenu name={t('Database Access Grants')} buttons={subMenuButtons} />
      <ConfirmStatusChange
        title={t('Please confirm')}
        description={t(
          'Are you sure you want to revoke the selected grants? Claimed ' +
            'users lose access to the database.',
        )}
        onConfirm={handleBulkRevoke}
      >
        {confirmRevoke => {
          const bulkActions: ListViewProps['bulkActions'] = [];
          if (canWrite) {
            bulkActions.push({
              key: 'delete',
              name: t('Revoke'),
              type: 'danger',
              onSelect: confirmRevoke,
            });
          }
          return (
            <>
              <GrantAccessModal
                show={grantModalOpen}
                onHide={() => setGrantModalOpen(false)}
                onGranted={refreshData}
                addSuccessToast={addSuccessToast}
                addDangerToast={addDangerToast}
              />
              <ListView<DatabaseGrant>
                className="grants-list-view"
                bulkActions={bulkActions}
                bulkSelectEnabled={bulkSelectEnabled}
                disableBulkSelect={toggleBulkSelect}
                columns={columns}
                count={grantsCount}
                data={grants}
                emptyState={emptyState}
                fetchData={fetchData}
                filters={filters}
                initialSort={initialSort}
                loading={loading}
                addDangerToast={addDangerToast}
                addSuccessToast={addSuccessToast}
                refreshData={() => {}}
                pageSize={PAGE_SIZE}
              />
            </>
          );
        }}
      </ConfirmStatusChange>
    </>
  );
}

export default withToasts(DatabaseAccessGrantsList);
