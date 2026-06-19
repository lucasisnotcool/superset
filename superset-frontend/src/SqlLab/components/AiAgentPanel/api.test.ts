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
import { getAgentBaseUrl, getAgentHealth, queryAgent } from './api';

const originalAgentUrl = process.env.SUPERSET_AI_AGENT_URL;

beforeEach(() => {
  process.env.SUPERSET_AI_AGENT_URL = 'http://agent.local/';
});

afterEach(() => {
  process.env.SUPERSET_AI_AGENT_URL = originalAgentUrl;
  fetchMock.clearHistory().removeRoutes();
});

test('getAgentBaseUrl trims trailing slash', () => {
  expect(getAgentBaseUrl()).toBe('http://agent.local');
});

test('getAgentHealth requests health endpoint', async () => {
  fetchMock.get('http://agent.local/health', {
    status: 'ok',
    model_provider: 'ollama',
    base_url: 'http://localhost:11434',
    default_model: 'qwen2.5-coder:7b',
    reachable: true,
  });

  const response = await getAgentHealth();

  expect(response.reachable).toBe(true);
  expect(response.model_provider).toBe('ollama');
  expect(fetchMock.callHistory.calls('http://agent.local/health')).toHaveLength(
    1,
  );
});

test('queryAgent posts typed payload to agent backend', async () => {
  fetchMock.post('http://agent.local/agent/query', {
    status: 'needs_review',
    sql: 'select 1',
    explanation: 'Returns one row.',
    validation: {
      is_valid: true,
      is_read_only: true,
      normalized_sql: 'select 1',
      dialect: 'sqlite',
      errors: [],
    },
    trace: [],
  });

  const response = await queryAgent({
    question: 'show one',
    database_id: 1,
    schema_name: null,
    dataset_ids: [16],
    execute: false,
  });

  const [call] = fetchMock.callHistory.calls('http://agent.local/agent/query');
  expect(response.sql).toBe('select 1');
  expect(JSON.parse(String(call.options.body))).toEqual({
    question: 'show one',
    database_id: 1,
    schema_name: null,
    dataset_ids: [16],
    execute: false,
  });
});
