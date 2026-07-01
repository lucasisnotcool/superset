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
import fetchMock from 'fetch-mock';
import { render, screen, waitFor } from 'spec/helpers/testing-library';
import AiAgentUsage from '.';

const originalAgentUrl = process.env.SUPERSET_AI_AGENT_URL;
const USAGE_URL = 'http://agent.local/agent/admin/llm-usage';

const SUMMARY = {
  total_calls: 42,
  total_failures: 3,
  total_duration_ms: 84000,
  avg_duration_ms: 2000,
  total_prompt_tokens: 1000,
  total_completion_tokens: 200,
  by_day: [
    {
      key: '2026-06-30',
      calls: 42,
      failures: 3,
      total_duration_ms: 84000,
      avg_duration_ms: 2000,
      prompt_tokens: 1000,
      completion_tokens: 200,
    },
  ],
  by_model: [
    {
      key: 'gpt-5.2',
      calls: 42,
      failures: 3,
      total_duration_ms: 84000,
      avg_duration_ms: 2000,
      prompt_tokens: 1000,
      completion_tokens: 200,
    },
  ],
  by_provider: [],
  kinds: ['chat'],
  generated_at: '2026-06-30T00:00:00Z',
};

beforeEach(() => {
  process.env.SUPERSET_AI_AGENT_URL = 'http://agent.local/';
});

afterEach(() => {
  process.env.SUPERSET_AI_AGENT_URL = originalAgentUrl;
  fetchMock.clearHistory().removeRoutes();
});

test('renders usage totals and breakdown tables', async () => {
  fetchMock.get(`begin:${USAGE_URL}`, SUMMARY);

  render(<AiAgentUsage />, { useRedux: true });

  expect(await screen.findByText('Total calls')).toBeInTheDocument();
  // The total (42) appears in the stat card and the per-day/model rows.
  expect((await screen.findAllByText('42')).length).toBeGreaterThan(0);
  // Model breakdown row renders (unique key).
  expect(await screen.findByText('gpt-5.2')).toBeInTheDocument();
  // The time-window selector renders (guards the Select props).
  expect(screen.getAllByLabelText('Time window').length).toBeGreaterThan(0);
});

test('shows a permission error when the API returns 403', async () => {
  fetchMock.get(`begin:${USAGE_URL}`, 403);

  render(<AiAgentUsage />, { useRedux: true });

  expect(
    await screen.findByText('You do not have permission to view LLM usage.'),
  ).toBeInTheDocument();
});

test('shows an empty summary without crashing', async () => {
  fetchMock.get(`begin:${USAGE_URL}`, {
    total_calls: 0,
    total_failures: 0,
    total_duration_ms: 0,
    avg_duration_ms: 0,
    total_prompt_tokens: 0,
    total_completion_tokens: 0,
    by_day: [],
    by_model: [],
    by_provider: [],
    kinds: [],
    generated_at: '2026-06-30T00:00:00Z',
  });

  render(<AiAgentUsage />, { useRedux: true });

  await waitFor(() =>
    expect(screen.getByText('Total calls')).toBeInTheDocument(),
  );
  expect(screen.getByText('By day')).toBeInTheDocument();
});
