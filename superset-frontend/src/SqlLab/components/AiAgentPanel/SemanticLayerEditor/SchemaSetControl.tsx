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
import {
  Button,
  Flex,
  Popover,
  Select,
  Tag,
  Tooltip,
} from '@superset-ui/core/components';
import { Icons } from '@superset-ui/core/components/Icons';
import { useSchemasQuery } from 'src/hooks/apiResources/schemas';

export interface SchemaSetControlProps {
  /** The project's full schema set (primary first). */
  schemaNames: string[];
  /** The primary schema (the wren-core logical namespace), highlighted. */
  primarySchema?: string | null;
  databaseId: number;
  catalogName?: string | null;
  /** Disabled when the user lacks write permission on the project. */
  canEdit?: boolean;
  /** True while an add-schema re-resolve is in flight (shows a spinner). */
  adding?: boolean;
  /** Called with a new schema to add to the project's set. */
  onAddSchema: (schema: string) => void;
}

/**
 * Header control that shows which physical schemas a semantic project covers and
 * lets a user widen the set. A project's models may reference tables in any member
 * schema via their `tableReference.schema`; this surfaces and edits that set.
 *
 * Adding a schema re-resolves the project with the expanded set, which (server-side)
 * proves the user's access to the new schema before it is associated.
 */
export default function SchemaSetControl({
  schemaNames,
  primarySchema,
  databaseId,
  catalogName,
  canEdit = false,
  adding = false,
  onAddSchema,
}: SchemaSetControlProps) {
  const [open, setOpen] = useState(false);
  const { data: schemaOptions = [], isFetching } = useSchemasQuery(
    {
      dbId: databaseId,
      catalog: catalogName || undefined,
      forceRefresh: false,
    },
    { skip: !databaseId || !open },
  );

  const current = useMemo(() => new Set(schemaNames), [schemaNames]);
  const addable = useMemo(
    () => schemaOptions.filter(option => !current.has(option.value)),
    [schemaOptions, current],
  );

  const addControl = (
    <div style={{ width: 240 }} data-test="add-schema-popover">
      <Select
        autoFocus
        showSearch
        loading={isFetching}
        disabled={adding}
        ariaLabel={t('Add schema')}
        placeholder={t('Select a schema to add')}
        options={addable}
        value={null}
        onChange={value => {
          if (typeof value === 'string' && value) {
            onAddSchema(value);
            setOpen(false);
          }
        }}
        notFoundContent={t('No other schemas available')}
      />
    </div>
  );

  return (
    <Flex align="center" gap={4} wrap="wrap" data-test="schema-set-control">
      {schemaNames.map(schema => (
        <Tooltip
          key={schema}
          title={
            schema === primarySchema
              ? t('Primary schema (semantic namespace)')
              : t('Schema in this project')
          }
        >
          <Tag color={schema === primarySchema ? 'blue' : undefined}>
            {schema}
          </Tag>
        </Tooltip>
      ))}
      {canEdit && (
        <Popover
          trigger="click"
          open={open}
          onOpenChange={setOpen}
          content={addControl}
          title={t('Add a schema to this project')}
          placement="bottomLeft"
        >
          <Button
            buttonStyle="link"
            buttonSize="small"
            loading={adding}
            icon={<Icons.PlusOutlined iconSize="s" />}
            data-test="add-schema-button"
            aria-label={t('Add schema to project')}
          >
            {t('Add schema')}
          </Button>
        </Popover>
      )}
    </Flex>
  );
}
