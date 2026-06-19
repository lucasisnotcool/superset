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
import { ChangeEvent, useEffect, useMemo, useState } from 'react';
import { useSelector } from 'react-redux';
import { t } from '@apache-superset/core/translation';
import { Alert } from '@apache-superset/core/components';
import { css, styled } from '@apache-superset/core/theme';
import {
  Button,
  Flex,
  Input,
  Tooltip,
  Typography,
} from '@superset-ui/core/components';
import { Icons } from '@superset-ui/core/components/Icons';
import { Switch } from '@superset-ui/core/components/Switch';
import { useAppDispatch } from 'src/SqlLab/hooks/useAppDispatch';
import type { QueryEditor, SqlLabRootState } from 'src/SqlLab/types';
import { queryEditorSetSql } from 'src/SqlLab/actions/sqlLab';
import {
  addDangerToast,
  addInfoToast,
  addSuccessToast,
} from 'src/components/MessageToasts/actions';
import copyTextToClipboard from 'src/utils/copy';
import {
  AgentHealthResponse,
  AgentQueryResponse,
  getAgentBaseUrl,
  getAgentHealth,
  queryAgent,
} from './api';

const Panel = styled.div`
  ${({ theme }) => css`
    display: flex;
    flex-direction: column;
    gap: ${theme.sizeUnit * 3}px;
    height: 100%;
    padding: ${theme.sizeUnit * 4}px;
    background: ${theme.colorBgBase};
    color: ${theme.colorText};
    overflow: auto;
  `}
`;

const Section = styled.div`
  ${({ theme }) => css`
    display: flex;
    flex-direction: column;
    gap: ${theme.sizeUnit * 2}px;
  `}
`;

const MetaText = styled(Typography.Text)`
  ${({ theme }) => css`
    color: ${theme.colorTextSecondary};
    font-size: ${theme.fontSizeSM}px;
    overflow-wrap: anywhere;
  `}
`;

const SqlBlock = styled.pre`
  ${({ theme }) => css`
    margin: 0;
    padding: ${theme.sizeUnit * 2}px;
    max-height: 220px;
    overflow: auto;
    white-space: pre-wrap;
    word-break: break-word;
    background: ${theme.colorBgContainer};
    border: 1px solid ${theme.colorBorder};
    border-radius: ${theme.borderRadius}px;
    font-size: ${theme.fontSizeSM}px;
    line-height: 1.5;
  `}
`;

const TraceList = styled.ul`
  ${({ theme }) => css`
    margin: 0;
    padding-left: ${theme.sizeUnit * 4}px;
    color: ${theme.colorTextSecondary};
    font-size: ${theme.fontSizeSM}px;
  `}
`;

const parseDatasetIds = (value: string) =>
  value
    .split(',')
    .map(item => Number.parseInt(item.trim(), 10))
    .filter(Number.isFinite);

const getActiveQueryEditor = ({
  sqlLab: {
    queryEditors,
    tabHistory,
    unsavedQueryEditor,
    lastUpdatedActiveTab,
  },
}: SqlLabRootState): Partial<QueryEditor> | undefined => {
  const activeId = lastUpdatedActiveTab || tabHistory.slice(-1)[0];
  return (
    queryEditors.find(queryEditor => queryEditor.id === activeId) ||
    (unsavedQueryEditor.id === activeId ? unsavedQueryEditor : undefined)
  );
};

const statusType = (status: AgentQueryResponse['status']) => {
  if (status === 'ok') {
    return 'success';
  }
  if (status === 'needs_review') {
    return 'info';
  }
  return 'error';
};

const AiAgentPanel = () => {
  const dispatch = useAppDispatch();
  const queryEditor = useSelector(getActiveQueryEditor);
  const [question, setQuestion] = useState('');
  const [datasetIds, setDatasetIds] = useState('');
  const [execute, setExecute] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [response, setResponse] = useState<AgentQueryResponse | null>(null);
  const [health, setHealth] = useState<AgentHealthResponse | null>(null);
  const [isHealthError, setIsHealthError] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const parsedDatasetIds = useMemo(
    () => parseDatasetIds(datasetIds),
    [datasetIds],
  );
  const databaseId = queryEditor?.dbId;
  const canGenerate =
    Boolean(question.trim()) && typeof databaseId === 'number';
  const generatedSql = response?.sql || '';
  const healthLabel = isHealthError
    ? t('offline')
    : health?.status || t('local');

  useEffect(() => {
    let isMounted = true;

    getAgentHealth()
      .then(result => {
        if (isMounted) {
          setHealth(result);
          setIsHealthError(false);
        }
      })
      .catch(() => {
        if (isMounted) {
          setIsHealthError(true);
        }
      });

    return () => {
      isMounted = false;
    };
  }, []);

  const onGenerate = async () => {
    if (!canGenerate || typeof databaseId !== 'number') {
      return;
    }
    setIsLoading(true);
    setError(null);
    try {
      const result = await queryAgent({
        question: question.trim(),
        database_id: databaseId,
        schema_name: queryEditor?.schema || null,
        dataset_ids: parsedDatasetIds,
        execute,
      });
      setResponse(result);
      if (result.status === 'error') {
        dispatch(addDangerToast(t('The agent could not generate SQL.')));
      }
    } catch (ex) {
      const message =
        ex instanceof Error ? ex.message : t('Agent request failed');
      setError(message);
      dispatch(addDangerToast(message));
    } finally {
      setIsLoading(false);
    }
  };

  const onInsertSql = () => {
    if (!generatedSql || !queryEditor?.id) {
      return;
    }
    dispatch(queryEditorSetSql(queryEditor, generatedSql));
    dispatch(addSuccessToast(t('SQL inserted into editor.')));
  };

  const onCopySql = () => {
    if (!generatedSql) {
      return;
    }
    copyTextToClipboard(() => Promise.resolve(generatedSql))
      .then(() => dispatch(addSuccessToast(t('SQL copied.'))))
      .catch(() => dispatch(addInfoToast(t('Copy failed.'))));
  };

  return (
    <Panel data-test="sql-lab-ai-agent-panel">
      <Section>
        <Flex justify="space-between" align="center">
          <Typography.Title level={5} style={{ margin: 0 }}>
            {t('AI SQL')}
          </Typography.Title>
          <Tooltip title={getAgentBaseUrl()}>
            <MetaText>{healthLabel}</MetaText>
          </Tooltip>
        </Flex>
        <MetaText>
          {typeof databaseId === 'number'
            ? t('Database %s', databaseId)
            : t('Select a database')}
          {queryEditor?.schema ? ` / ${queryEditor.schema}` : ''}
        </MetaText>
      </Section>

      <Section>
        <Input.TextArea
          rows={4}
          value={question}
          placeholder={t('Ask for SQL')}
          onChange={(event: ChangeEvent<HTMLTextAreaElement>) =>
            setQuestion(event.target.value)
          }
        />
        <Input
          value={datasetIds}
          placeholder={t('Dataset IDs')}
          onChange={(event: ChangeEvent<HTMLInputElement>) =>
            setDatasetIds(event.target.value)
          }
        />
        <Flex justify="space-between" align="center">
          <MetaText>{t('Execute')}</MetaText>
          <Switch checked={execute} onChange={checked => setExecute(checked)} />
        </Flex>
        <Button
          buttonStyle="primary"
          onClick={onGenerate}
          disabled={!canGenerate || isLoading}
          loading={isLoading}
          icon={<Icons.ThunderboltOutlined iconSize="m" />}
          block
        >
          {t('Generate SQL')}
        </Button>
      </Section>

      {error && <Alert type="error" message={error} />}

      {response && (
        <Section>
          <Alert
            type={statusType(response.status)}
            message={response.explanation || response.status}
          />
          {generatedSql && <SqlBlock>{generatedSql}</SqlBlock>}
          {response.validation.errors.length > 0 && (
            <Alert
              type="warning"
              message={response.validation.errors.join('\n')}
            />
          )}
          <Flex gap="small" wrap="wrap">
            <Button
              onClick={onInsertSql}
              disabled={!generatedSql || !queryEditor?.id}
              icon={<Icons.EditOutlined iconSize="m" />}
            >
              {t('Insert')}
            </Button>
            <Button
              onClick={onCopySql}
              disabled={!generatedSql}
              icon={<Icons.CopyOutlined iconSize="m" />}
            >
              {t('Copy')}
            </Button>
          </Flex>
        </Section>
      )}

      {response?.trace && response.trace.length > 0 && (
        <Section>
          <Typography.Text strong>{t('Trace')}</Typography.Text>
          <TraceList>
            {response.trace.map((event, index) => (
              <li key={`${event.step}-${index}`}>
                {event.step}: {event.summary}
              </li>
            ))}
          </TraceList>
        </Section>
      )}
    </Panel>
  );
};

export default AiAgentPanel;
