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

import { render, screen, within } from 'spec/helpers/testing-library';
import userEvent from '@testing-library/user-event';
import ProjectBrowser, {
  type ProjectBrowserProject,
  type ProjectBrowserProps,
} from './ProjectBrowser';

const PROJECTS: ProjectBrowserProject[] = [
  {
    id: 'p1',
    name: 'Sales Mart',
    slug: 'sales-mart',
    primarySchema: 'sales',
    schemaCount: 2,
    databaseLabel: 'Warehouse',
    permission: 'write',
    updatedAt: '2026-06-01T00:00:00Z',
  },
  {
    id: 'p2',
    name: 'Marketing Model',
    slug: 'marketing-model',
    primarySchema: 'marketing',
    schemaCount: 1,
    databaseLabel: 'Warehouse',
    permission: 'read',
    updatedAt: '2026-06-10T00:00:00Z',
  },
  {
    id: 'p3',
    name: 'Lake Explorer',
    slug: 'lake-explorer',
    primarySchema: 'raw',
    schemaCount: 3,
    databaseLabel: null,
    permission: 'write',
    updatedAt: '2026-06-05T00:00:00Z',
  },
];

const setup = (props: Partial<ProjectBrowserProps> = {}) => {
  const handlers = {
    onOpen: jest.fn(),
    onCreate: jest.fn(),
    onDuplicate: jest.fn(),
    onRename: jest.fn(),
    onDelete: jest.fn(),
  };
  render(<ProjectBrowser projects={PROJECTS} {...handlers} {...props} />);
  return handlers;
};

test('renders projects grouped by database', () => {
  setup();
  const headers = screen.getAllByTestId('project-group-header');
  expect(headers.map(node => node.textContent)).toEqual([
    'Unknown database',
    'Warehouse',
  ]);
  expect(screen.getByText('Sales Mart')).toBeInTheDocument();
  expect(screen.getByText('Marketing Model')).toBeInTheDocument();
  expect(screen.getByText('Lake Explorer')).toBeInTheDocument();
});

test('shows a coverage badge only for projects that have a score', () => {
  setup({
    projects: [
      { ...PROJECTS[0], coverageScore: 0.83 },
      { ...PROJECTS[1], coverageScore: null },
    ],
  });
  const badges = screen.getAllByTestId('project-coverage');
  expect(badges).toHaveLength(1);
  expect(badges[0]).toHaveTextContent('83% covered');
});

test('search filters by name and slug across groups', async () => {
  setup();
  await userEvent.type(screen.getByTestId('project-search'), 'marketing');
  expect(screen.getByText('Marketing Model')).toBeInTheDocument();
  expect(screen.queryByText('Sales Mart')).not.toBeInTheDocument();
  expect(screen.queryByText('Lake Explorer')).not.toBeInTheDocument();
});

test('clicking a row body fires onOpen with the project id', async () => {
  const { onOpen } = setup();
  await userEvent.click(screen.getByText('Sales Mart'));
  expect(onOpen).toHaveBeenCalledWith('p1');
});

test('actions menu fires Duplicate, Rename and Delete', async () => {
  const { onDuplicate, onRename, onDelete } = setup();
  const rows = screen.getAllByTestId('project-row');
  // First row is in the "Unknown database" group → Lake Explorer (p3, write).
  const actions = within(rows[0]).getByTestId('project-actions');

  await userEvent.click(actions);
  await userEvent.click(await screen.findByTestId('project-duplicate'));
  expect(onDuplicate).toHaveBeenCalledWith('p3');

  await userEvent.click(actions);
  await userEvent.click(await screen.findByTestId('project-rename'));
  expect(onRename).toHaveBeenCalledWith('p3');

  await userEvent.click(actions);
  await userEvent.click(await screen.findByTestId('project-delete'));
  expect(onDelete).toHaveBeenCalledWith('p3');
});

test('Rename and Delete are disabled for read-only projects', async () => {
  const { onRename, onDelete, onDuplicate } = setup();
  // Marketing Model (p2) is read-only.
  const row = screen
    .getByText('Marketing Model')
    .closest('[data-test="project-row"]') as HTMLElement;
  await userEvent.click(within(row).getByTestId('project-actions'));

  const rename = await screen.findByTestId('project-rename');
  const del = screen.getByTestId('project-delete');
  // The disabled state lives on the antd menu item wrapper.
  expect(rename.closest('[role="menuitem"]')).toHaveAttribute(
    'aria-disabled',
    'true',
  );
  expect(del.closest('[role="menuitem"]')).toHaveAttribute(
    'aria-disabled',
    'true',
  );

  await userEvent.click(rename);
  await userEvent.click(del);
  expect(onRename).not.toHaveBeenCalled();
  expect(onDelete).not.toHaveBeenCalled();

  // Duplicate stays enabled for read-only projects.
  await userEvent.click(screen.getByTestId('project-duplicate'));
  expect(onDuplicate).toHaveBeenCalledWith('p2');
});

test('New project button fires onCreate', async () => {
  const { onCreate } = setup();
  await userEvent.click(screen.getByTestId('project-new'));
  expect(onCreate).toHaveBeenCalledTimes(1);
});

test('renders an empty state when there are no projects', () => {
  setup({ projects: [] });
  expect(screen.getByText('No semantic projects yet')).toBeInTheDocument();
});

test('renders an empty state when the search matches nothing', async () => {
  setup();
  await userEvent.type(screen.getByTestId('project-search'), 'nonexistent');
  expect(screen.getByText('No projects match your search')).toBeInTheDocument();
});

test('paginates a large list with a Show more control', async () => {
  // Bound the DOM: with > PAGE_SIZE (50) projects, only the first page renders
  // until "Show more" is clicked.
  const many: ProjectBrowserProject[] = Array.from({ length: 60 }, (_, i) => ({
    id: `big-${i}`,
    name: `Project ${String(i).padStart(2, '0')}`,
    slug: `project-${i}`,
    primarySchema: 'public',
    schemaCount: 1,
    databaseLabel: 'Warehouse',
    permission: 'write' as const,
    // Descending dates so ordering within the first page is deterministic.
    updatedAt: `2026-06-01T00:${String(i).padStart(2, '0')}:00Z`,
  }));
  setup({ projects: many });

  expect(screen.getAllByTestId('project-row')).toHaveLength(50);
  const showMore = screen.getByTestId('project-show-more');
  expect(showMore).toHaveTextContent('10 remaining');

  await userEvent.click(showMore);

  expect(screen.getAllByTestId('project-row')).toHaveLength(60);
  expect(screen.queryByTestId('project-show-more')).not.toBeInTheDocument();
});
