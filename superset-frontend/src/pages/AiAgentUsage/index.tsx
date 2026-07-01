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
import { t } from '@apache-superset/core/translation';
import { styled } from '@apache-superset/core/theme';
import { Alert } from '@apache-superset/core/components';
import {
  Card,
  Flex,
  Loading,
  Select,
  Table,
  TableSize,
  Typography,
} from '@superset-ui/core/components';
import { getAgentBaseUrl } from 'src/SqlLab/components/AiAgentPanel/api';

interface UsageBucket {
  key: string;
  calls: number;
  failures: number;
  total_duration_ms: number;
  avg_duration_ms: number;
  prompt_tokens: number;
  completion_tokens: number;
}

interface UsageSummary {
  total_calls: number;
  total_failures: number;
  total_duration_ms: number;
  avg_duration_ms: number;
  total_prompt_tokens: number;
  total_completion_tokens: number;
  by_day: UsageBucket[];
  by_model: UsageBucket[];
  by_provider: UsageBucket[];
  kinds: string[];
  generated_at: string;
}

const WINDOW_OPTIONS = [
  { value: 0, label: t('All time') },
  { value: 7, label: t('Last 7 days') },
  { value: 30, label: t('Last 30 days') },
  { value: 90, label: t('Last 90 days') },
];

const Container = styled.div`
  ${({ theme }) => `
    padding: ${theme.sizeUnit * 4}px;
    display: flex;
    flex-direction: column;
    gap: ${theme.sizeUnit * 4}px;
  `}
`;

const StatGrid = styled.div`
  ${({ theme }) => `
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
    gap: ${theme.sizeUnit * 4}px;
  `}
`;

const fetchUsage = async (days: number): Promise<UsageSummary> => {
  const query = days > 0 ? `?days=${days}` : '';
  const response = await fetch(
    `${getAgentBaseUrl()}/agent/admin/llm-usage${query}`,
    { credentials: 'include', headers: { 'Content-Type': 'application/json' } },
  );
  if (!response.ok) {
    throw new Error(
      response.status === 403
        ? t('You do not have permission to view LLM usage.')
        : t('Failed to load LLM usage (HTTP %s).', response.status),
    );
  }
  return response.json() as Promise<UsageSummary>;
};

const StatCard = ({
  label,
  value,
}: {
  label: string;
  value: string | number;
}) => (
  <Card padded>
    <Typography.Text type="secondary">{label}</Typography.Text>
    <Typography.Title level={3} style={{ margin: 0 }}>
      {value}
    </Typography.Title>
  </Card>
);

const BUCKET_COLUMNS = (firstLabel: string) => [
  { title: firstLabel, dataIndex: 'key', key: 'key' },
  { title: t('Calls'), dataIndex: 'calls', key: 'calls' },
  { title: t('Failures'), dataIndex: 'failures', key: 'failures' },
  {
    title: t('Avg ms'),
    dataIndex: 'avg_duration_ms',
    key: 'avg_duration_ms',
  },
  {
    title: t('Prompt tokens'),
    dataIndex: 'prompt_tokens',
    key: 'prompt_tokens',
  },
  {
    title: t('Completion tokens'),
    dataIndex: 'completion_tokens',
    key: 'completion_tokens',
  },
];

export default function AiAgentUsage() {
  const [summary, setSummary] = useState<UsageSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [days, setDays] = useState(0);

  const load = useCallback((windowDays: number) => {
    setLoading(true);
    setError(null);
    fetchUsage(windowDays)
      .then(setSummary)
      .catch((ex: Error) => setError(ex.message))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    load(days);
  }, [days, load]);

  return (
    <Container data-test="ai-agent-usage">
      <Flex align="center" justify="space-between">
        <Typography.Title level={2} style={{ margin: 0 }}>
          {t('AI Agent Usage')}
        </Typography.Title>
        <Select
          value={days}
          options={WINDOW_OPTIONS}
          onChange={value => setDays(value as number)}
          css={{ width: 180 }}
          aria-label={t('Time window')}
        />
      </Flex>

      {loading && <Loading position="inline" />}
      {!loading && error && <Alert type="error" message={error} showIcon />}

      {!loading && !error && summary && (
        <>
          <StatGrid>
            <StatCard label={t('Total calls')} value={summary.total_calls} />
            <StatCard label={t('Failures')} value={summary.total_failures} />
            <StatCard
              label={t('Avg duration (ms)')}
              value={summary.avg_duration_ms}
            />
            <StatCard
              label={t('Total tokens')}
              value={
                summary.total_prompt_tokens + summary.total_completion_tokens
              }
            />
          </StatGrid>

          <Typography.Title level={4}>{t('By day')}</Typography.Title>
          <Table
            columns={BUCKET_COLUMNS(t('Day'))}
            data={summary.by_day}
            rowKey="key"
            usePagination={false}
            size={TableSize.Small}
          />

          <Typography.Title level={4}>{t('By model')}</Typography.Title>
          <Table
            columns={BUCKET_COLUMNS(t('Model'))}
            data={summary.by_model}
            rowKey="key"
            usePagination={false}
            size={TableSize.Small}
          />
        </>
      )}
    </Container>
  );
}
