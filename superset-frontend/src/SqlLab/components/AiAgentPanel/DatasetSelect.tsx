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
import { useCallback, useEffect, useMemo, useState } from 'react';
import rison from 'rison';
import { SupersetClient } from '@superset-ui/core';
import { t } from '@apache-superset/core/translation';
import {
  AsyncSelect,
  type SelectOptionsPagePromise,
} from '@superset-ui/core/components';

interface DatasetSelectProps {
  databaseId?: number;
  schema?: string | null;
  value: number[];
  onChange: (datasetIds: number[]) => void;
}

interface DatasetResult {
  id: number;
  table_name: string;
}

const PAGE_SIZE = 25;

/**
 * Multi-select for scoping the agent conversation to specific datasets. Queries
 * Superset's own dataset API (not the agent service), filtered by the active
 * database and schema, and keeps a label cache so already-selected ids render
 * as names when an existing conversation is reopened.
 */
const DatasetSelect = ({
  databaseId,
  schema,
  value,
  onChange,
}: DatasetSelectProps) => {
  const [labelById, setLabelById] = useState<Record<number, string>>({});

  const cacheLabels = useCallback((results: DatasetResult[]) => {
    if (!results.length) {
      return;
    }
    setLabelById(previous => {
      const next = { ...previous };
      results.forEach(result => {
        next[result.id] = result.table_name;
      });
      return next;
    });
  }, []);

  const fetchDatasets: SelectOptionsPagePromise = useCallback(
    async (search, page, pageSize) => {
      if (typeof databaseId !== 'number') {
        return { data: [], totalCount: 0 };
      }
      const filters: { col: string; opr: string; value: unknown }[] = [
        { col: 'database', opr: 'rel_o_m', value: databaseId },
      ];
      if (schema) {
        filters.push({ col: 'schema', opr: 'eq', value: schema });
      }
      if (search) {
        filters.push({ col: 'table_name', opr: 'ct', value: search });
      }
      const query = rison.encode({
        filters,
        order_column: 'table_name',
        order_direction: 'asc',
        page,
        page_size: pageSize,
      });
      const response = await SupersetClient.get({
        endpoint: `/api/v1/dataset/?q=${query}`,
      });
      const results = (response.json.result as DatasetResult[]) || [];
      cacheLabels(results);
      return {
        data: results.map(result => ({
          value: result.id,
          label: result.table_name,
        })),
        totalCount: response.json.count as number,
      };
    },
    [databaseId, schema, cacheLabels],
  );

  // Hydrate labels for ids that are selected but not yet known (e.g. when an
  // existing conversation is reopened), so chips show names rather than ids.
  useEffect(() => {
    const missing = value.filter(id => !(id in labelById));
    if (!missing.length) {
      return;
    }
    let isMounted = true;
    Promise.all(
      missing.map(id =>
        SupersetClient.get({ endpoint: `/api/v1/dataset/${id}` })
          .then(response => response.json.result as DatasetResult)
          .catch(() => null),
      ),
    ).then(results => {
      if (isMounted) {
        cacheLabels(results.filter((r): r is DatasetResult => r !== null));
      }
    });
    return () => {
      isMounted = false;
    };
  }, [value, labelById, cacheLabels]);

  const labeledValue = useMemo(
    () =>
      value.map(id => ({
        value: id,
        label: labelById[id] ?? t('Dataset %s', id),
      })),
    [value, labelById],
  );

  return (
    <AsyncSelect
      ariaLabel={t('Datasets')}
      mode="multiple"
      allowClear
      placeholder={t('All datasets in scope')}
      value={labeledValue}
      options={fetchDatasets}
      pageSize={PAGE_SIZE}
      // Refetch when the database/schema scope changes.
      key={`${databaseId ?? 'none'}:${schema ?? 'none'}`}
      disabled={typeof databaseId !== 'number'}
      onChange={selected => {
        const items = Array.isArray(selected) ? selected : [];
        onChange(
          items
            .map(item =>
              typeof item === 'object' && item !== null
                ? (item as { value: number }).value
                : (item as number),
            )
            .filter((id): id is number => typeof id === 'number'),
        );
      }}
    />
  );
};

export default DatasetSelect;
