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
import { ConversationScope } from '../api';
import InstructionsPanel from './InstructionsPanel';

const originalAgentUrl = process.env.SUPERSET_AI_AGENT_URL;

const LIST_URL =
  'http://agent.local/agent/semantic-layer/instructions?database_id=1&schema_name=public';
const CREATE_URL = 'http://agent.local/agent/semantic-layer/instructions';
const DELETE_URL =
  'http://agent.local/agent/semantic-layer/instructions/inst-1';

const scope: ConversationScope = {
  database_id: 1,
  catalog_name: null,
  schema_name: 'public',
  dataset_ids: [],
};

const makeInstruction = (overrides: Record<string, unknown> = {}) => ({
  id: 'inst-1',
  instruction: 'Always filter out test accounts',
  is_global: false,
  project_id: null,
  created_at: '2026-06-23T00:00:00Z',
  ...overrides,
});

beforeEach(() => {
  process.env.SUPERSET_AI_AGENT_URL = 'http://agent.local/';
});

afterEach(() => {
  process.env.SUPERSET_AI_AGENT_URL = originalAgentUrl;
  fetchMock.clearHistory().removeRoutes();
});

const renderPanel = (props: Partial<{ canWrite: boolean }> = {}) =>
  render(<InstructionsPanel scope={scope} canWrite={props.canWrite ?? true} />, {
    useRedux: true,
  });

test('lists existing instructions for the scope', async () => {
  fetchMock.get(LIST_URL, [makeInstruction()]);
  renderPanel();
  expect(
    await screen.findByText('Always filter out test accounts'),
  ).toBeInTheDocument();
});

test('renders an empty state when there are no instructions', async () => {
  fetchMock.get(LIST_URL, []);
  renderPanel();
  expect(await screen.findByText('No instructions yet.')).toBeInTheDocument();
});

test('creates an instruction and refreshes the list', async () => {
  // The list starts empty and reflects the new row once the POST has run.
  let rows: ReturnType<typeof makeInstruction>[] = [];
  fetchMock.get(LIST_URL, () => rows);
  fetchMock.post(CREATE_URL, () => {
    rows = [makeInstruction()];
    return makeInstruction();
  });

  renderPanel();
  await screen.findByText('No instructions yet.');

  await userEvent.type(
    screen.getByTestId('instruction-input'),
    'Always filter out test accounts',
  );
  await userEvent.click(screen.getByRole('button', { name: /Add instruction/ }));

  await waitFor(() =>
    expect(fetchMock.callHistory.calls(CREATE_URL)).toHaveLength(1),
  );
  const [call] = fetchMock.callHistory.calls(CREATE_URL);
  expect(JSON.parse(String(call.options.body))).toEqual({
    scope,
    instruction: 'Always filter out test accounts',
    is_global: false,
  });
  expect(
    await screen.findByText('Always filter out test accounts'),
  ).toBeInTheDocument();
});

test('sends is_global when "Always apply" is toggled on', async () => {
  fetchMock.get(LIST_URL, []);
  fetchMock.post(CREATE_URL, makeInstruction({ is_global: true }));

  renderPanel();
  await screen.findByText('No instructions yet.');

  await userEvent.type(screen.getByTestId('instruction-input'), 'Use UTC');
  await userEvent.click(screen.getByRole('switch'));
  await userEvent.click(screen.getByRole('button', { name: /Add instruction/ }));

  await waitFor(() =>
    expect(fetchMock.callHistory.calls(CREATE_URL)).toHaveLength(1),
  );
  const [call] = fetchMock.callHistory.calls(CREATE_URL);
  expect(JSON.parse(String(call.options.body)).is_global).toBe(true);
});

test('deletes an instruction after confirmation', async () => {
  fetchMock.get(LIST_URL, [makeInstruction()]);
  fetchMock.delete(DELETE_URL, { deleted: true });

  renderPanel();
  await screen.findByText('Always filter out test accounts');

  await userEvent.click(
    screen.getByRole('button', { name: 'Delete instruction' }),
  );
  // Confirm in the Popconfirm popover (its confirm button is labeled "Delete").
  await userEvent.click(await screen.findByRole('button', { name: 'Delete' }));

  await waitFor(() =>
    expect(fetchMock.callHistory.calls(DELETE_URL)).toHaveLength(1),
  );
});

test('hides the authoring form and delete actions when read-only', async () => {
  fetchMock.get(LIST_URL, [makeInstruction()]);
  renderPanel({ canWrite: false });

  await screen.findByText('Always filter out test accounts');
  expect(screen.queryByTestId('instruction-input')).not.toBeInTheDocument();
  expect(
    screen.queryByRole('button', { name: 'Delete instruction' }),
  ).not.toBeInTheDocument();
});

test('shows a guard when no schema is selected and does not fetch', async () => {
  fetchMock.get(LIST_URL, []);
  render(
    <InstructionsPanel scope={{ ...scope, schema_name: undefined }} canWrite />,
    { useRedux: true },
  );
  expect(screen.getByText('Select a database and schema.')).toBeInTheDocument();
  expect(fetchMock.callHistory.calls(LIST_URL)).toHaveLength(0);
});

test('surfaces a load error without crashing', async () => {
  fetchMock.get(LIST_URL, { status: 500, body: { detail: 'boom' } });
  renderPanel();
  // The panel renders its static help copy even when the list load fails.
  expect(
    await screen.findByText(/Instructions steer SQL generation/),
  ).toBeInTheDocument();
});
