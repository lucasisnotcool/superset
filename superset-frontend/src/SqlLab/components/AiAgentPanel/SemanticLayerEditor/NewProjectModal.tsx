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
import { t } from '@apache-superset/core/translation';
import {
  Button,
  Flex,
  Input,
  Modal,
  Select,
  Typography,
} from '@superset-ui/core/components';
import { useSchemasQuery } from 'src/hooks/apiResources/schemas';

export interface NewProjectModalProps {
  open: boolean;
  databaseId: number;
  catalogName: string | null;
  /** Called with the chosen name (may be blank) + schema set (first = primary). */
  onSubmit: (params: { name: string; schemaNames: string[] }) => void;
  onCancel: () => void;
  /** True while the project is being created — spins Create, locks the form. */
  creating?: boolean;
}

/**
 * Create-a-project dialog (MDL Lab "New project"). A project covers one database
 * but may span several of its schemas, so creation collects the schema set up front
 * (the first selected schema is the primary/semantic namespace). The server proves
 * the caller's access to every chosen schema before the project is created.
 */
export default function NewProjectModal({
  open,
  databaseId,
  catalogName,
  onSubmit,
  onCancel,
  creating = false,
}: NewProjectModalProps) {
  const [name, setName] = useState('');
  const [schemaNames, setSchemaNames] = useState<string[]>([]);

  // Reset the form whenever the dialog is (re)opened.
  useEffect(() => {
    if (open) {
      setName('');
      setSchemaNames([]);
    }
  }, [open]);

  const { data: schemaOptions = [] } = useSchemasQuery(
    {
      dbId: databaseId,
      catalog: catalogName || undefined,
      forceRefresh: false,
    },
    { skip: !databaseId || !open },
  );

  const canCreate = schemaNames.length > 0;

  return (
    <Modal
      show={open}
      onHide={onCancel}
      title={t('New project')}
      footer={
        <>
          <Button
            buttonStyle="secondary"
            disabled={creating}
            onClick={onCancel}
          >
            {t('Cancel')}
          </Button>
          <Button
            buttonStyle="primary"
            loading={creating}
            disabled={!canCreate || creating}
            data-test="new-project-create"
            onClick={() => onSubmit({ name: name.trim(), schemaNames })}
          >
            {t('Create')}
          </Button>
        </>
      }
    >
      <Flex vertical gap="middle">
        <Flex vertical gap="small">
          <Typography.Text type="secondary">
            {t('Name (optional)')}
          </Typography.Text>
          <Input
            data-test="new-project-name"
            value={name}
            onChange={event => setName(event.target.value)}
            placeholder={t('Derived from the schema if left blank')}
          />
        </Flex>
        <Flex vertical gap="small">
          <Typography.Text type="secondary">
            {t('Schemas (the first is the primary)')}
          </Typography.Text>
          <Select
            mode="multiple"
            showSearch
            ariaLabel={t('Project schemas')}
            data-test="new-project-schemas"
            placeholder={t('Select one or more schemas')}
            options={schemaOptions}
            value={schemaNames}
            onChange={value =>
              setSchemaNames(Array.isArray(value) ? (value as string[]) : [])
            }
          />
        </Flex>
      </Flex>
    </Modal>
  );
}
