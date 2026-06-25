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
import { useMemo } from 'react';
import { t } from '@apache-superset/core/translation';
import { useTheme } from '@apache-superset/core/theme';
import {
  Empty,
  Flex,
  Tag,
  Tree,
  Typography,
} from '@superset-ui/core/components';
import { Icons } from '@superset-ui/core/components/Icons';
import { MdlFile, MdlValidationResult, WorkspaceNode } from '../api';

/**
 * Build a workspace tree from the editor's MDL files (folders from path
 * prefixes). Used by the editor so the browser works regardless of the
 * `WREN_COPILOT_ENABLED` flag; the backend `GET /workspace` produces the same
 * shape (plus virtual sibling artifacts) for other consumers.
 */
export const treeFromFiles = (files: MdlFile[]): WorkspaceNode => {
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

  return root;
};

export interface WorkspaceTreeProps {
  root: WorkspaceNode | null;
  activeFileId?: string | null;
  /** Called with the MDL file id when an editable MDL leaf is selected. */
  onSelectFile: (fileId: string) => void;
  /** Optional per-MDL-file trailing actions (e.g. an activate Switch). */
  renderActions?: (node: WorkspaceNode) => React.ReactNode;
}

interface TreeDataNode {
  key: string;
  title: React.ReactNode;
  selectable: boolean;
  isLeaf: boolean;
  fileId?: string | null;
  children?: TreeDataNode[];
}

const kindIcon = (kind: WorkspaceNode['kind']) => {
  if (kind === 'folder') return <Icons.FolderOutlined />;
  if (kind === 'instructions') return <Icons.FileTextOutlined />;
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
    <Flex align="center" gap={4} justify="space-between">
      <Flex align="center" gap={4}>
        <Typography.Text>{node.name}</Typography.Text>
        {node.status === 'draft' ? <Tag>{t('draft')}</Tag> : null}
        {node.status === 'active' ? (
          <Tag color="success">{t('active')}</Tag>
        ) : null}
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
    key: node.file_id || node.path || node.name,
    title: <NodeTitle node={node} renderActions={renderActions} />,
    icon: kindIcon(node.kind),
    selectable: node.kind === 'mdl',
    isLeaf: node.kind !== 'folder',
    fileId: node.file_id,
    children: node.children.length
      ? toTreeData(node.children, renderActions)
      : undefined,
  }));

const WorkspaceTree = ({
  root,
  activeFileId,
  onSelectFile,
  renderActions,
}: WorkspaceTreeProps) => {
  useTheme();
  const treeData = useMemo(
    () => (root ? toTreeData(root.children, renderActions) : []),
    [root, renderActions],
  );

  if (!root || treeData.length === 0) {
    return (
      <Empty
        image={Empty.PRESENTED_IMAGE_SIMPLE}
        description={t('No semantic-layer files yet.')}
      />
    );
  }

  return (
    <Tree
      showIcon
      blockNode
      defaultExpandAll
      treeData={treeData}
      selectedKeys={activeFileId ? [activeFileId] : []}
      data-test="workspace-tree"
      onSelect={(_keys, info) => {
        const fileId = (info.node as unknown as TreeDataNode).fileId;
        if (fileId) {
          onSelectFile(fileId);
        }
      }}
    />
  );
};

export default WorkspaceTree;
