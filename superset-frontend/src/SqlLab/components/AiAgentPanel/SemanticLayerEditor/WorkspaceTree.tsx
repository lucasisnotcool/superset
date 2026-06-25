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
import { useEffect, useMemo, useState } from 'react';
import { t } from '@apache-superset/core/translation';
import { css, styled } from '@apache-superset/core/theme';
import {
  Dropdown,
  Empty,
  Flex,
  Tag,
  Tree,
  Typography,
} from '@superset-ui/core/components';
import { Icons } from '@superset-ui/core/components/Icons';
import type { MenuProps } from 'antd';
import {
  MdlFile,
  MdlValidationResult,
  SemanticDocument,
  WorkspaceNode,
} from '../api';

/**
 * Build a workspace tree from the editor's MDL files (folders from path
 * prefixes) plus any uploaded `raw/` documents. Used by the editor so the
 * browser works regardless of the `WREN_COPILOT_ENABLED` flag; the backend
 * `GET /workspace` produces the same shape for other consumers.
 */
export const treeFromFiles = (
  files: MdlFile[],
  documents: SemanticDocument[] = [],
): WorkspaceNode => {
  const root: WorkspaceNode = {
    path: '',
    name: 'workspace',
    kind: 'folder',
    editable: false,
    children: [],
  };
  const folders = new Map<string, WorkspaceNode>([['', root]]);

  const ensureFolder = (path: string): WorkspaceNode => {
    const existing = folders.get(path);
    if (existing) {
      return existing;
    }
    const slash = path.lastIndexOf('/');
    const parentPath = slash === -1 ? '' : path.slice(0, slash);
    const name = slash === -1 ? path : path.slice(slash + 1);
    const parent = ensureFolder(parentPath);
    const node: WorkspaceNode = {
      path,
      name,
      kind: 'folder',
      editable: false,
      children: [],
    };
    parent.children.push(node);
    folders.set(path, node);
    return node;
  };

  files
    .filter(file => file.status !== 'deleted')
    .slice()
    .sort((a, b) => a.path.localeCompare(b.path))
    .forEach(file => {
      const slash = file.path.lastIndexOf('/');
      const parent = ensureFolder(
        slash === -1 ? '' : file.path.slice(0, slash),
      );
      parent.children.push({
        path: file.path,
        name: slash === -1 ? file.path : file.path.slice(slash + 1),
        kind: 'mdl',
        editable: true,
        status: file.status,
        file_id: file.id,
        validation: file.validation as MdlValidationResult | null,
        children: [],
      });
    });

  if (documents.length) {
    const rawFolder: WorkspaceNode = {
      path: 'raw',
      name: 'raw',
      kind: 'folder',
      editable: false,
      status: t('%s document(s)', documents.length),
      children: documents
        .slice()
        .sort((a, b) => a.filename.localeCompare(b.filename))
        .map(document => ({
          path: `raw/${document.id}`,
          name: document.filename,
          kind: 'document' as const,
          editable: false,
          status: document.status,
          document_id: document.id,
          children: [],
        })),
    };
    root.children.push(rawFolder);
  }

  return root;
};

export interface WorkspaceTreeProps {
  root: WorkspaceNode | null;
  activeFileId?: string | null;
  /** The selected document id (raw/ node), if a document is open. */
  activeDocumentId?: string | null;
  /** Called with the MDL file id when an editable MDL leaf is opened. */
  onSelectFile: (fileId: string) => void;
  /** Called with the document id when a raw/ document node is opened. */
  onSelectDocument?: (documentId: string) => void;
  /** Duplicate an MDL file (context menu). */
  onDuplicateFile?: (fileId: string) => void;
  /** Delete one or more MDL files (context menu / multi-select). */
  onDeleteFiles?: (fileIds: string[]) => void;
  /** Optional per-MDL-file trailing actions (e.g. an activate Switch). */
  renderActions?: (node: WorkspaceNode) => React.ReactNode;
}

interface TreeDataNode {
  key: string;
  title: React.ReactNode;
  selectable: boolean;
  isLeaf: boolean;
  fileId?: string | null;
  documentId?: string | null;
  children?: TreeDataNode[];
}

// Tree node row: icon and title inline (not stacked), with the file name
// truncated by ellipsis instead of wrapping when the panel is narrow.
const TreeWrapper = styled.div`
  ${({ theme }) => css`
    min-width: 0;
    .ant-tree .ant-tree-node-content-wrapper {
      display: inline-flex;
      align-items: center;
      gap: ${theme.sizeUnit}px;
      min-width: 0;
      flex: 1;
    }
    .ant-tree .ant-tree-node-content-wrapper .ant-tree-iconEle {
      display: inline-flex;
      align-items: center;
      vertical-align: middle;
    }
    .ant-tree .ant-tree-node-content-wrapper .ant-tree-title {
      flex: 1;
      min-width: 0;
    }
  `}
`;

const NodeName = styled(Typography.Text)`
  flex: 1;
  min-width: 0;
`;

const kindIcon = (kind: WorkspaceNode['kind']) => {
  if (kind === 'folder') return <Icons.FolderOutlined />;
  if (kind === 'instructions') return <Icons.FileTextOutlined />;
  if (kind === 'document') return <Icons.FileTextOutlined />;
  if (kind === 'compiled' || kind === 'memory') return <Icons.LockOutlined />;
  return <Icons.FileOutlined />;
};

const NodeTitle = ({
  node,
  renderActions,
}: {
  node: WorkspaceNode;
  renderActions?: (node: WorkspaceNode) => React.ReactNode;
}) => {
  const invalid = node.validation?.valid === false;
  const actions = node.kind === 'mdl' ? renderActions?.(node) : null;
  return (
    <Flex
      align="center"
      gap={4}
      justify="space-between"
      css={css`
        min-width: 0;
      `}
    >
      <Flex
        align="center"
        gap={4}
        css={css`
          min-width: 0;
          flex: 1;
        `}
      >
        {/* Name truncates with an ellipsis rather than wrapping (item 5). The
            redundant active/draft status badge is intentionally omitted for MDL
            files — the Active/Draft toggle already shows that state (item 2). */}
        <NodeName ellipsis={{ tooltip: node.name }}>{node.name}</NodeName>
        {invalid ? <Tag color="error">{t('invalid')}</Tag> : null}
      </Flex>
      {actions ? (
        // Keep clicks on the action control from toggling tree selection.
        <span
          role="presentation"
          onClick={event => event.stopPropagation()}
          onKeyDown={event => event.stopPropagation()}
        >
          {actions}
        </span>
      ) : null}
    </Flex>
  );
};

const toTreeData = (
  nodes: WorkspaceNode[],
  renderActions?: (node: WorkspaceNode) => React.ReactNode,
): TreeDataNode[] =>
  nodes.map(node => ({
    key: node.file_id || node.document_id || node.path || node.name,
    title: <NodeTitle node={node} renderActions={renderActions} />,
    icon: kindIcon(node.kind),
    selectable: node.kind === 'mdl' || node.kind === 'document',
    isLeaf: node.kind !== 'folder',
    fileId: node.file_id,
    documentId: node.document_id,
    children: node.children.length
      ? toTreeData(node.children, renderActions)
      : undefined,
  }));

const WorkspaceTree = ({
  root,
  activeFileId,
  activeDocumentId,
  onSelectFile,
  onSelectDocument,
  onDuplicateFile,
  onDeleteFiles,
  renderActions,
}: WorkspaceTreeProps) => {
  const treeData = useMemo(
    () => (root ? toTreeData(root.children, renderActions) : []),
    [root, renderActions],
  );
  // Multi-selection (shift/ctrl-click) is owned here; the active open file/doc
  // seeds the highlight so programmatic selection (after open/delete) stays in sync.
  const [selectedKeys, setSelectedKeys] = useState<string[]>([]);
  useEffect(() => {
    const active = activeFileId || activeDocumentId;
    setSelectedKeys(active ? [active] : []);
  }, [activeFileId, activeDocumentId]);

  // The set of currently-selected MDL file ids (documents excluded from bulk ops).
  const selectedFileIds = useMemo(() => {
    const fileKeys = new Set<string>();
    const walk = (nodes: TreeDataNode[]) =>
      nodes.forEach(node => {
        if (node.fileId && selectedKeys.includes(node.key)) {
          fileKeys.add(node.fileId);
        }
        if (node.children) walk(node.children);
      });
    walk(treeData);
    return Array.from(fileKeys);
  }, [selectedKeys, treeData]);

  if (!root || treeData.length === 0) {
    return (
      <Empty
        image={Empty.PRESENTED_IMAGE_SIMPLE}
        description={t('No semantic-layer files yet.')}
      />
    );
  }

  const contextMenu = (node: TreeDataNode): MenuProps | undefined => {
    if (!node.fileId) {
      return undefined;
    }
    const targetIds =
      selectedFileIds.length > 1 && selectedFileIds.includes(node.fileId)
        ? selectedFileIds
        : [node.fileId];
    return {
      items: [
        { key: 'open', label: t('Open') },
        { key: 'duplicate', label: t('Duplicate') },
        { type: 'divider' },
        {
          key: 'delete',
          danger: true,
          label:
            targetIds.length > 1
              ? t('Delete %s files', targetIds.length)
              : t('Delete'),
        },
      ],
      onClick: ({ key, domEvent }) => {
        domEvent.stopPropagation();
        if (key === 'open' && node.fileId) onSelectFile(node.fileId);
        if (key === 'duplicate' && node.fileId) onDuplicateFile?.(node.fileId);
        if (key === 'delete') onDeleteFiles?.(targetIds);
      },
    };
  };

  return (
    <TreeWrapper>
      <Tree
        showIcon
        blockNode
        multiple
        defaultExpandAll
        treeData={treeData}
        selectedKeys={selectedKeys}
        data-test="workspace-tree"
        titleRender={(node: unknown) => {
          const typed = node as TreeDataNode;
          const menu = contextMenu(typed);
          if (!menu) {
            return <>{typed.title}</>;
          }
          return (
            <Dropdown menu={menu} trigger={['contextMenu']}>
              <div
                css={css`
                  min-width: 0;
                `}
              >
                {typed.title}
              </div>
            </Dropdown>
          );
        }}
        onSelect={(keys, info) => {
          setSelectedKeys(keys as string[]);
          const clicked = info.node as unknown as TreeDataNode;
          if (clicked.documentId) {
            onSelectDocument?.(clicked.documentId);
          } else if (clicked.fileId) {
            onSelectFile(clicked.fileId);
          }
        }}
      />
    </TreeWrapper>
  );
};

export default WorkspaceTree;
