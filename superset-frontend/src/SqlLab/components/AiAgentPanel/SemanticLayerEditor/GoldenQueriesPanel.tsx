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
  List,
  Popconfirm,
  Tag,
  Typography,
} from '@superset-ui/core/components';
import { Icons } from '@superset-ui/core/components/Icons';
import {
  addDangerToast,
  addSuccessToast,
} from 'src/components/MessageToasts/actions';
import { GoldenQuery, listMdlFiles, MdlFile, updateMdlFile } from '../api';

const GOLDEN_QUERIES_PATH = 'queries.json';

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

const normalize = (question: string) =>
  question.toLowerCase().split(/\s+/).filter(Boolean).join(' ');

const parseQueries = (file: MdlFile | undefined): GoldenQuery[] => {
  if (!file?.content) {
    return [];
  }
  try {
    const parsed = JSON.parse(file.content);
    return Array.isArray(parsed?.queries) ? parsed.queries : [];
  } catch {
    return [];
  }
};

export interface GoldenQueriesPanelProps {
  projectId: string;
  canWrite: boolean;
}

export default function GoldenQueriesPanel({
  projectId,
  canWrite,
}: GoldenQueriesPanelProps) {
  const dispatch = useDispatch();
  const [file, setFile] = useState<MdlFile | undefined>(undefined);
  const [queries, setQueries] = useState<GoldenQuery[]>([]);
  const [isLoading, setIsLoading] = useState(false);

  const refresh = useCallback(async () => {
    if (!projectId) {
      return;
    }
    setIsLoading(true);
    try {
      const files = await listMdlFiles(projectId);
      const found = files.find(f => f.path === GOLDEN_QUERIES_PATH);
      setFile(found);
      setQueries(parseQueries(found));
    } catch (ex) {
      dispatch(
        addDangerToast(
          ex instanceof Error ? ex.message : t('Unable to load golden queries'),
        ),
      );
    } finally {
      setIsLoading(false);
    }
  }, [projectId, dispatch]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const removeQuery = async (entry: GoldenQuery) => {
    if (!file) {
      return;
    }
    const remaining = queries.filter(
      q => normalize(q.question) !== normalize(entry.question),
    );
    try {
      await updateMdlFile(projectId, file.id, {
        content: JSON.stringify({ queries: remaining }, null, 2),
      });
      dispatch(addSuccessToast(t('Golden query removed.')));
      await refresh();
    } catch (ex) {
      dispatch(
        addDangerToast(
          ex instanceof Error ? ex.message : t('Unable to remove golden query'),
        ),
      );
    }
  };

  return (
    <PanelRoot data-test="semantic-layer-golden-queries">
      <Alert
        type="info"
        showIcon
        data-test="golden-queries-note"
        message={t(
          'Golden queries are curated, verified question→SQL examples shared by ' +
            'everyone on this project. Add them from a chat answer ("Promote to ' +
            'golden") or via the Copilot; they steer future SQL generation.',
        )}
      />
      <List
        loading={isLoading}
        dataSource={queries}
        locale={{
          emptyText: (
            <Empty
              image={Empty.PRESENTED_IMAGE_SIMPLE}
              description={t('No golden queries yet.')}
            />
          ),
        }}
        renderItem={(item: GoldenQuery) => (
          <List.Item
            key={item.name + item.question}
            actions={
              canWrite
                ? [
                    <Popconfirm
                      key="delete"
                      title={t('Remove this golden query?')}
                      okText={t('Remove')}
                      cancelText={t('Cancel')}
                      onConfirm={() => removeQuery(item)}
                    >
                      <Button
                        buttonStyle="link"
                        aria-label={t('Remove golden query')}
                        icon={<Icons.DeleteOutlined iconSize="m" />}
                      />
                    </Popconfirm>,
                  ]
                : undefined
            }
          >
            <Flex vertical gap={0}>
              <Flex gap="small" align="center">
                <Typography.Text strong>{item.question}</Typography.Text>
                <Tag color={item.verified_at ? 'success' : 'default'}>
                  {item.verified_at ? t('Verified') : t('Draft')}
                </Tag>
              </Flex>
              <Typography.Text
                code
                css={css`
                  white-space: pre-wrap;
                `}
              >
                {item.semantic_sql}
              </Typography.Text>
            </Flex>
          </List.Item>
        )}
      />
    </PanelRoot>
  );
}
