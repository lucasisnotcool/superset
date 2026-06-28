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

import { useMemo, useState } from 'react';
import { t } from '@apache-superset/core/translation';
import { styled } from '@apache-superset/core/theme';
import {
  Button,
  Dropdown,
  Empty,
  Flex,
  Input,
  type MenuItem,
  Tag,
  Tooltip,
  Typography,
} from '@superset-ui/core/components';
import { Icons } from '@superset-ui/core/components/Icons';

export interface ProjectBrowserProject {
  id: string;
  name: string;
  slug: string;
  primarySchema: string;
  schemaCount: number;
  databaseLabel: string | null;
  permission: 'read' | 'write';
  updatedAt: string;
  /** Latest complete coverage score (0–1); `null` when never audited. */
  coverageScore?: number | null;
}

// Coverage tag color follows the score: a well-covered layer is success, a thin
// one a warning, a poor one an error — the same advisory semantics as the editor
// CoverageBadge, so the browser and the open project agree at a glance.
const coverageColor = (score: number): string => {
  if (score >= 0.8) return 'success';
  if (score >= 0.5) return 'warning';
  return 'error';
};

export interface ProjectBrowserProps {
  projects: ProjectBrowserProject[];
  loading?: boolean;
  activeProjectId?: string | null;
  onOpen: (projectId: string) => void;
  onCreate: () => void;
  onDuplicate: (projectId: string) => void;
  onRename: (projectId: string) => void;
  onDelete: (projectId: string) => void;
}

const UNKNOWN_DATABASE = t('Unknown database');

// Render at most this many rows at once; a "Show more" control reveals the rest.
// Bounds the DOM for databases with many projects without a full virtualization
// rewrite (project counts are realistically small; this is the safety net).
const PAGE_SIZE = 50;

const Container = styled.div`
  ${({ theme }) => `
    display: flex;
    flex-direction: column;
    gap: ${theme.sizeUnit * 2}px;
    height: 100%;
  `}
`;

const Header = styled.div`
  ${({ theme }) => `
    display: flex;
    flex-direction: column;
    gap: ${theme.sizeUnit * 2}px;
    padding: ${theme.sizeUnit * 2}px;
  `}
`;

const GroupList = styled.div`
  ${({ theme }) => `
    display: flex;
    flex-direction: column;
    gap: ${theme.sizeUnit * 3}px;
    overflow-y: auto;
    padding: 0 ${theme.sizeUnit * 2}px ${theme.sizeUnit * 2}px;
  `}
`;

const GroupHeader = styled.div`
  ${({ theme }) => `
    font-size: ${theme.fontSizeSM}px;
    font-weight: ${theme.fontWeightStrong};
    color: ${theme.colorTextSecondary};
    text-transform: uppercase;
    letter-spacing: 0.04em;
    padding: ${theme.sizeUnit}px 0;
  `}
`;

const Row = styled.div<{ active: boolean }>`
  ${({ theme, active }) => `
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: ${theme.sizeUnit * 2}px;
    padding: ${theme.sizeUnit * 2}px;
    border-radius: ${theme.borderRadius}px;
    cursor: pointer;
    background-color: ${active ? theme.colorPrimaryBg : 'transparent'};
    border: 1px solid ${active ? theme.colorPrimaryBorder : 'transparent'};

    &:hover {
      background-color: ${active ? theme.colorPrimaryBg : theme.colorBgTextHover};
    }
  `}
`;

const RowBody = styled.div`
  ${({ theme }) => `
    display: flex;
    flex-direction: column;
    gap: ${theme.sizeUnit}px;
    min-width: 0;
    flex: 1;
  `}
`;

const RowMeta = styled.div`
  ${({ theme }) => `
    display: flex;
    align-items: center;
    gap: ${theme.sizeUnit * 2}px;
    flex-wrap: wrap;
    color: ${theme.colorTextSecondary};
    font-size: ${theme.fontSizeSM}px;
  `}
`;

interface ProjectGroup {
  label: string;
  projects: ProjectBrowserProject[];
}

function groupProjects(projects: ProjectBrowserProject[]): ProjectGroup[] {
  const groups = new Map<string, ProjectBrowserProject[]>();
  projects.forEach(project => {
    const label = project.databaseLabel || UNKNOWN_DATABASE;
    const bucket = groups.get(label);
    if (bucket) {
      bucket.push(project);
    } else {
      groups.set(label, [project]);
    }
  });
  return Array.from(groups.entries())
    .map(([label, groupProjectsList]) => ({
      label,
      projects: [...groupProjectsList].sort((a, b) =>
        b.updatedAt.localeCompare(a.updatedAt),
      ),
    }))
    .sort((a, b) => a.label.localeCompare(b.label));
}

export default function ProjectBrowser({
  projects,
  loading = false,
  activeProjectId = null,
  onOpen,
  onCreate,
  onDuplicate,
  onRename,
  onDelete,
}: ProjectBrowserProps) {
  const [search, setSearch] = useState('');
  const [openMenuId, setOpenMenuId] = useState<string | null>(null);
  const [visibleCount, setVisibleCount] = useState(PAGE_SIZE);

  const handleSearch = (value: string) => {
    setSearch(value);
    setVisibleCount(PAGE_SIZE); // a new query restarts paging from the top
  };

  // Filter the flat list first so the visible cap spans all databases (the most
  // recently updated projects across the whole list win the first page), then
  // window it, then group what remains.
  const filtered = useMemo(() => {
    const query = search.trim().toLowerCase();
    const matches = query
      ? projects.filter(
          project =>
            project.name.toLowerCase().includes(query) ||
            project.slug.toLowerCase().includes(query),
        )
      : projects;
    return [...matches].sort((a, b) => b.updatedAt.localeCompare(a.updatedAt));
  }, [projects, search]);

  const filteredGroups = useMemo(
    () => groupProjects(filtered.slice(0, visibleCount)),
    [filtered, visibleCount],
  );

  const hasMore = filtered.length > visibleCount;
  const isEmpty = filteredGroups.length === 0;

  const buildMenuItems = (project: ProjectBrowserProject): MenuItem[] => {
    const readOnly = project.permission === 'read';
    return [
      {
        key: 'duplicate',
        label: <span data-test="project-duplicate">{t('Duplicate')}</span>,
        onClick: () => {
          setOpenMenuId(null);
          onDuplicate(project.id);
        },
      },
      {
        key: 'rename',
        disabled: readOnly,
        label: <span data-test="project-rename">{t('Rename')}</span>,
        onClick: () => {
          setOpenMenuId(null);
          onRename(project.id);
        },
      },
      {
        key: 'delete',
        danger: true,
        disabled: readOnly,
        label: <span data-test="project-delete">{t('Delete')}</span>,
        onClick: () => {
          setOpenMenuId(null);
          onDelete(project.id);
        },
      },
    ];
  };

  return (
    <Container data-test="project-browser">
      <Header>
        <Flex align="center" justify="space-between" gap={8}>
          <Typography.Title level={5} style={{ margin: 0 }}>
            {t('MDL Lab')}
          </Typography.Title>
          <Button
            buttonStyle="primary"
            buttonSize="small"
            icon={<Icons.PlusOutlined iconSize="s" />}
            onClick={onCreate}
            loading={loading}
            data-test="project-new"
          >
            {t('New project')}
          </Button>
        </Flex>
        <Input
          allowClear
          value={search}
          onChange={event => handleSearch(event.target.value)}
          placeholder={t('Search projects')}
          prefix={<Icons.SearchOutlined iconSize="s" />}
          data-test="project-search"
          aria-label={t('Search projects')}
        />
      </Header>
      <GroupList>
        {isEmpty ? (
          <Empty
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            description={
              search.trim()
                ? t('No projects match your search')
                : t('No semantic projects yet')
            }
            data-test="project-empty"
          />
        ) : (
          filteredGroups.map(group => (
            <div key={group.label}>
              <GroupHeader data-test="project-group-header">
                {group.label}
              </GroupHeader>
              {group.projects.map(project => {
                const readOnly = project.permission === 'read';
                return (
                  <Row
                    key={project.id}
                    active={project.id === activeProjectId}
                    data-test="project-row"
                    data-test-active={project.id === activeProjectId}
                  >
                    {/* Open is bound on the row body only — binding it on the outer
                        Row too made a single click fire onOpen twice (bubbling). */}
                    <RowBody
                      role="button"
                      tabIndex={0}
                      onClick={() => onOpen(project.id)}
                      data-test="project-open"
                    >
                      <Flex align="center" gap={8}>
                        <Typography.Text strong ellipsis>
                          {project.name}
                        </Typography.Text>
                        {readOnly && (
                          <Tooltip title={t('Read-only access')}>
                            <Tag
                              icon={<Icons.LockOutlined iconSize="s" />}
                              data-test="project-readonly"
                            >
                              {t('Read-only')}
                            </Tag>
                          </Tooltip>
                        )}
                      </Flex>
                      <RowMeta>
                        <Tag data-test="project-schema-count">
                          {t('%s schema(s)', project.schemaCount)}
                        </Tag>
                        <span data-test="project-primary-schema">
                          {project.primarySchema}
                        </span>
                        {typeof project.coverageScore === 'number' ? (
                          <Tooltip
                            title={t('Document coverage of the active model')}
                          >
                            <Tag
                              color={coverageColor(project.coverageScore)}
                              data-test="project-coverage"
                            >
                              {t(
                                '%s%% covered',
                                Math.round(project.coverageScore * 100),
                              )}
                            </Tag>
                          </Tooltip>
                        ) : null}
                      </RowMeta>
                    </RowBody>
                    <div
                      onClick={event => event.stopPropagation()}
                      role="presentation"
                    >
                      <Dropdown
                        trigger={['click']}
                        open={openMenuId === project.id}
                        onOpenChange={open =>
                          setOpenMenuId(open ? project.id : null)
                        }
                        menu={{ items: buildMenuItems(project) }}
                      >
                        <Button
                          buttonStyle="link"
                          buttonSize="small"
                          icon={<Icons.MoreOutlined iconSize="m" />}
                          data-test="project-actions"
                          aria-label={t('Project actions')}
                        />
                      </Dropdown>
                    </div>
                  </Row>
                );
              })}
            </div>
          ))
        )}
        {hasMore && (
          <Button
            buttonStyle="link"
            onClick={() => setVisibleCount(count => count + PAGE_SIZE)}
            data-test="project-show-more"
          >
            {t('Show more (%s remaining)', filtered.length - visibleCount)}
          </Button>
        )}
      </GroupList>
    </Container>
  );
}
