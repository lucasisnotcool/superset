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
  Button,
  Flex,
  Input,
  List,
  Loading,
  Popconfirm,
  Tag,
  Typography,
} from '@superset-ui/core/components';
import { getAgentBaseUrl } from 'src/SqlLab/components/AiAgentPanel/api';

interface PromptSummary {
  name: string;
  has_file_default: boolean;
  versions_count: number;
  production_version: number | null;
}

interface PromptVersion {
  id: string;
  name: string;
  version: number;
  content: string;
  comment?: string | null;
  created_by?: string | null;
  created_at: string;
}

interface PromptDetail {
  name: string;
  file_content?: string | null;
  production_version_id?: string | null;
  versions: PromptVersion[];
}

const Container = styled.div`
  ${({ theme }) => `
    padding: ${theme.sizeUnit * 4}px;
    display: flex;
    flex-direction: column;
    gap: ${theme.sizeUnit * 4}px;
  `}
`;

const Columns = styled.div`
  ${({ theme }) => `
    display: grid;
    grid-template-columns: 280px 1fr;
    gap: ${theme.sizeUnit * 4}px;
    align-items: start;
  `}
`;

type DiffLine = { type: 'same' | 'add' | 'del'; text: string };

/** Line-level LCS diff — enough to review a prompt edit before promoting. */
export const diffLines = (before: string, after: string): DiffLine[] => {
  const a = before.split('\n');
  const b = after.split('\n');
  const n = a.length;
  const m = b.length;
  const lcs: number[][] = Array.from({ length: n + 1 }, () =>
    new Array(m + 1).fill(0),
  );
  for (let i = n - 1; i >= 0; i -= 1) {
    for (let j = m - 1; j >= 0; j -= 1) {
      lcs[i][j] =
        a[i] === b[j]
          ? lcs[i + 1][j + 1] + 1
          : Math.max(lcs[i + 1][j], lcs[i][j + 1]);
    }
  }
  const out: DiffLine[] = [];
  let i = 0;
  let j = 0;
  while (i < n && j < m) {
    if (a[i] === b[j]) {
      out.push({ type: 'same', text: a[i] });
      i += 1;
      j += 1;
    } else if (lcs[i + 1][j] >= lcs[i][j + 1]) {
      out.push({ type: 'del', text: a[i] });
      i += 1;
    } else {
      out.push({ type: 'add', text: b[j] });
      j += 1;
    }
  }
  while (i < n) {
    out.push({ type: 'del', text: a[i] });
    i += 1;
  }
  while (j < m) {
    out.push({ type: 'add', text: b[j] });
    j += 1;
  }
  return out;
};

const request = async <T,>(path: string, init?: RequestInit): Promise<T> => {
  const response = await fetch(`${getAgentBaseUrl()}${path}`, {
    ...init,
    credentials: 'include',
    headers: { 'Content-Type': 'application/json', ...(init?.headers || {}) },
  });
  if (!response.ok) {
    throw new Error(
      response.status === 403
        ? t('You do not have permission to manage agent prompts.')
        : t('Prompt registry request failed (HTTP %s).', response.status),
    );
  }
  return response.json() as Promise<T>;
};

export default function AiAgentPrompts() {
  const [prompts, setPrompts] = useState<PromptSummary[]>([]);
  const [selected, setSelected] = useState<string | undefined>(undefined);
  const [detail, setDetail] = useState<PromptDetail | null>(null);
  const [draft, setDraft] = useState('');
  const [comment, setComment] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [showDiff, setShowDiff] = useState(false);

  const loadList = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const list = await request<PromptSummary[]>('/agent/admin/prompts');
      setPrompts(list);
      setSelected(current => current ?? list[0]?.name);
    } catch (ex) {
      setError(ex instanceof Error ? ex.message : String(ex));
    } finally {
      setLoading(false);
    }
  }, []);

  const loadDetail = useCallback(async (name: string) => {
    setError(null);
    try {
      const loaded = await request<PromptDetail>(
        `/agent/admin/prompts/${name}`,
      );
      setDetail(loaded);
      const production = loaded.versions.find(
        v => v.id === loaded.production_version_id,
      );
      setDraft(production?.content ?? loaded.file_content ?? '');
      setComment('');
    } catch (ex) {
      setError(ex instanceof Error ? ex.message : String(ex));
    }
  }, []);

  useEffect(() => {
    loadList();
  }, [loadList]);

  useEffect(() => {
    if (selected) {
      loadDetail(selected);
    }
  }, [selected, loadDetail]);

  const saveCandidate = async () => {
    if (!selected) {
      return;
    }
    try {
      const version = await request<PromptVersion>(
        `/agent/admin/prompts/${selected}/versions`,
        {
          method: 'POST',
          body: JSON.stringify({ content: draft, comment: comment || null }),
        },
      );
      setNotice(
        t(
          'Saved as candidate v%s. It is NOT live until you promote it.',
          version.version,
        ),
      );
      await loadDetail(selected);
      await loadList();
    } catch (ex) {
      setError(ex instanceof Error ? ex.message : String(ex));
    }
  };

  const promote = async (versionId: string, versionNumber: number) => {
    if (!selected) {
      return;
    }
    try {
      await request<PromptVersion>(`/agent/admin/prompts/${selected}/promote`, {
        method: 'POST',
        body: JSON.stringify({ version_id: versionId }),
      });
      setNotice(t('v%s is now live in production.', versionNumber));
      await loadDetail(selected);
      await loadList();
    } catch (ex) {
      setError(ex instanceof Error ? ex.message : String(ex));
    }
  };

  const resetToFile = async () => {
    if (!selected) {
      return;
    }
    try {
      await request<{ reset: boolean }>(
        `/agent/admin/prompts/${selected}/promotion`,
        { method: 'DELETE' },
      );
      setNotice(t('Reset — the built-in default prompt is live again.'));
      await loadDetail(selected);
      await loadList();
    } catch (ex) {
      setError(ex instanceof Error ? ex.message : String(ex));
    }
  };

  const selectedSummary = prompts.find(p => p.name === selected);
  const liveContent =
    detail?.versions.find(v => v.id === detail.production_version_id)
      ?.content ??
    detail?.file_content ??
    '';
  const diff = showDiff ? diffLines(liveContent, draft) : [];

  return (
    <Container data-test="ai-agent-prompts">
      <Typography.Title level={2} style={{ margin: 0 }}>
        {t('AI Agent Prompts')}
      </Typography.Title>
      <Alert
        type="info"
        showIcon
        message={t(
          'Edits are saved as candidate versions and only take effect when ' +
            'promoted to production. Resetting returns the agent to the ' +
            'built-in default. Measure prompt changes with project benchmarks ' +
            'before promoting.',
        )}
      />
      {error && <Alert type="error" showIcon message={error} />}
      {notice && (
        <Alert
          type="success"
          showIcon
          closable
          onClose={() => setNotice(null)}
          message={notice}
          data-test="prompt-notice"
        />
      )}
      {loading && <Loading position="inline" />}
      {!loading && (
        <Columns>
          <List
            data-test="prompt-list"
            dataSource={prompts}
            renderItem={(prompt: PromptSummary) => (
              <List.Item
                key={prompt.name}
                onClick={() => setSelected(prompt.name)}
                style={{ cursor: 'pointer' }}
              >
                <Flex gap="small" align="center" wrap>
                  <Typography.Text strong={prompt.name === selected}>
                    {prompt.name}
                  </Typography.Text>
                  {prompt.production_version !== null ? (
                    <Tag color="success">
                      {t('override v%s', prompt.production_version)}
                    </Tag>
                  ) : (
                    <Tag>{t('default')}</Tag>
                  )}
                </Flex>
              </List.Item>
            )}
          />
          {detail && (
            <Flex vertical gap="small">
              <Flex gap="small" align="center" wrap>
                <Typography.Title level={4} style={{ margin: 0 }}>
                  {detail.name}
                </Typography.Title>
                {selectedSummary?.production_version !== null &&
                selectedSummary?.production_version !== undefined ? (
                  <Tag color="success">
                    {t(
                      'Live: override v%s',
                      selectedSummary.production_version,
                    )}
                  </Tag>
                ) : (
                  <Tag>{t('Live: built-in default')}</Tag>
                )}
              </Flex>
              <Input.TextArea
                data-test="prompt-editor"
                rows={16}
                value={draft}
                onChange={event => setDraft(event.target.value)}
              />
              <Flex gap="small" align="center" wrap>
                <Input
                  data-test="prompt-comment"
                  placeholder={t('Change note (optional)')}
                  value={comment}
                  onChange={event => setComment(event.target.value)}
                  style={{ width: 280 }}
                />
                <Button
                  data-test="save-candidate"
                  buttonStyle="primary"
                  disabled={!draft.trim()}
                  onClick={saveCandidate}
                >
                  {t('Save as candidate')}
                </Button>
                <Button
                  data-test="toggle-diff"
                  buttonStyle="secondary"
                  onClick={() => setShowDiff(current => !current)}
                >
                  {showDiff ? t('Hide diff') : t('Diff vs live')}
                </Button>
                <Popconfirm
                  title={t('Reset to the built-in default prompt?')}
                  okText={t('Reset')}
                  cancelText={t('Cancel')}
                  onConfirm={resetToFile}
                >
                  <Button
                    data-test="reset-default"
                    buttonStyle="secondary"
                    disabled={!detail.production_version_id}
                  >
                    {t('Reset to default')}
                  </Button>
                </Popconfirm>
              </Flex>
              {showDiff && (
                <pre
                  data-test="prompt-diff"
                  style={{
                    maxHeight: 320,
                    overflow: 'auto',
                    margin: 0,
                    fontSize: 12,
                  }}
                >
                  {diff.map((line, index) => (
                    <div
                      // eslint-disable-next-line react/no-array-index-key
                      key={index}
                      style={{
                        background:
                          line.type === 'add'
                            ? 'rgba(0, 160, 60, 0.15)'
                            : line.type === 'del'
                              ? 'rgba(220, 40, 40, 0.15)'
                              : undefined,
                      }}
                    >
                      {line.type === 'add'
                        ? '+'
                        : line.type === 'del'
                          ? '-'
                          : ' '}{' '}
                      {line.text}
                    </div>
                  ))}
                </pre>
              )}
              <Typography.Title level={5}>{t('Versions')}</Typography.Title>
              <List
                data-test="prompt-versions"
                dataSource={detail.versions}
                locale={{ emptyText: t('No candidate versions yet.') }}
                renderItem={(version: PromptVersion) => (
                  <List.Item
                    key={version.id}
                    actions={[
                      version.id === detail.production_version_id ? (
                        <Tag key="live" color="success">
                          {t('live')}
                        </Tag>
                      ) : (
                        <Button
                          key="promote"
                          buttonStyle="link"
                          onClick={() => promote(version.id, version.version)}
                        >
                          {t('Promote')}
                        </Button>
                      ),
                    ]}
                  >
                    <Flex vertical gap={0}>
                      <Typography.Text strong>
                        {t('v%s', version.version)}{' '}
                        {version.created_by ? `— ${version.created_by}` : ''}
                      </Typography.Text>
                      {version.comment && (
                        <Typography.Text type="secondary">
                          {version.comment}
                        </Typography.Text>
                      )}
                    </Flex>
                  </List.Item>
                )}
              />
            </Flex>
          )}
        </Columns>
      )}
    </Container>
  );
}
