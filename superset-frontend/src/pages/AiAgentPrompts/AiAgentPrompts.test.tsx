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
import {
  render,
  screen,
  userEvent,
  waitFor,
} from 'spec/helpers/testing-library';
import AiAgentPrompts from './index';

const originalAgentUrl = process.env.SUPERSET_AI_AGENT_URL;

const LIST_URL = 'http://agent.local/agent/admin/prompts';
const DETAIL_URL = 'http://agent.local/agent/admin/prompts/text_to_sql';

const SUMMARY = {
  name: 'text_to_sql',
  has_file_default: true,
  versions_count: 1,
  production_version: null,
};

const VERSION = {
  id: 'pv-1',
  name: 'text_to_sql',
  version: 1,
  content: 'Candidate prompt body',
  comment: 'tighter instructions',
  created_by: 'admin',
  created_at: '2026-07-03T00:00:00Z',
};

beforeEach(() => {
  process.env.SUPERSET_AI_AGENT_URL = 'http://agent.local/';
  fetchMock.get(LIST_URL, [SUMMARY]);
  fetchMock.get(DETAIL_URL, {
    name: 'text_to_sql',
    file_content: 'File default body',
    production_version_id: null,
    versions: [VERSION],
  });
});

afterEach(() => {
  process.env.SUPERSET_AI_AGENT_URL = originalAgentUrl;
  fetchMock.clearHistory().removeRoutes();
});

test('lists prompts and loads the editor with the live content', async () => {
  render(<AiAgentPrompts />);
  expect(await screen.findByText('text_to_sql')).toBeInTheDocument();
  // No production override -> file default is shown in the editor.
  expect(await screen.findByTestId('prompt-editor')).toHaveValue(
    'File default body',
  );
  expect(screen.getByText('Live: built-in default')).toBeInTheDocument();
  expect(screen.getByText('tighter instructions')).toBeInTheDocument();
});

test('saving creates a candidate version without promoting', async () => {
  fetchMock.post(`${DETAIL_URL}/versions`, {
    ...VERSION,
    id: 'pv-2',
    version: 2,
  });
  render(<AiAgentPrompts />);
  const editor = await screen.findByTestId('prompt-editor');
  await userEvent.clear(editor);
  await userEvent.type(editor, 'New body');
  await userEvent.click(screen.getByTestId('save-candidate'));

  await waitFor(() =>
    expect(
      fetchMock.callHistory.calls(`${DETAIL_URL}/versions`, { method: 'POST' }),
    ).toHaveLength(1),
  );
  expect(await screen.findByTestId('prompt-notice')).toHaveTextContent(
    /NOT live until you promote/,
  );
});

test('promote posts the version id', async () => {
  fetchMock.post(`${DETAIL_URL}/promote`, VERSION);
  render(<AiAgentPrompts />);
  await screen.findByTestId('prompt-editor');
  await userEvent.click(screen.getByRole('button', { name: 'Promote' }));
  await waitFor(() =>
    expect(
      fetchMock.callHistory.calls(`${DETAIL_URL}/promote`, { method: 'POST' }),
    ).toHaveLength(1),
  );
  const [call] = fetchMock.callHistory.calls(`${DETAIL_URL}/promote`, {
    method: 'POST',
  });
  expect(JSON.parse(String(call.options.body))).toEqual({
    version_id: 'pv-1',
  });
});

test('permission errors surface as a readable alert', async () => {
  fetchMock.removeRoutes({ names: undefined });
  fetchMock.clearHistory();
  fetchMock.get(LIST_URL, 403);
  render(<AiAgentPrompts />);
  expect(
    await screen.findByText(/do not have permission to manage agent prompts/),
  ).toBeInTheDocument();
});
