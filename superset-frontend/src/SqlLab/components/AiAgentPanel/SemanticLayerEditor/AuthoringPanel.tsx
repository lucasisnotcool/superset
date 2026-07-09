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
/**
 * Benchmark Authoring panel (plan_benchmark_authoring_agent_impl.md P3.3/P3.4).
 *
 * Upload a semi-structured CSV (+ optional context .md), stream the authoring
 * agent's progress live, review/edit the drafted items, then import the
 * approved rows into a benchmark. The draft is never persisted server-side —
 * import is the explicit human review gate (R4).
 */
import {
  ChangeEvent,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import { useDispatch } from 'react-redux';
import { t } from '@apache-superset/core/translation';
import { css, styled } from '@apache-superset/core/theme';
import { Alert } from '@apache-superset/core/components';
import {
  Button,
  Checkbox,
  Empty,
  Flex,
  Input,
  List,
  Select,
  Tag,
  Typography,
} from '@superset-ui/core/components';
import {
  addDangerToast,
  addSuccessToast,
} from 'src/components/MessageToasts/actions';
import {
  AgentStep,
  AuthoredItem,
  AuthoringDraft,
  AuthoringMode,
  Benchmark,
  createBenchmark,
  importBenchmarkItems,
  listBenchmarks,
  streamBenchmarkAuthoring,
} from '../api';

const PanelRoot = styled.div`
  ${({ theme }) => css`
    display: flex;
    min-height: 0;
    flex: 1;
    flex-direction: column;
    gap: ${theme.sizeUnit * 3}px;
    padding: ${theme.sizeUnit * 3}px;
    overflow: auto;
  `}
`;

const StepLine = styled.div`
  ${({ theme }) => css`
    font-family: ${theme.fontFamilyCode};
    font-size: ${theme.fontSizeSM}px;
  `}
`;

const VALIDATION_COLORS: Record<AuthoredItem['validation'], string> = {
  verified: 'success',
  needs_review: 'warning',
};

const ORIGIN_COLORS: Record<AuthoredItem['origin'], string> = {
  human: 'default',
  extracted: 'blue',
  generated: 'purple',
};

export interface AuthoringPanelProps {
  projectId: string;
  canWrite: boolean;
  /**
   * Whether the pane is visible. Tabs keep hidden panes mounted, so the
   * benchmark list loaded at mount goes stale when a sibling tab creates or
   * deletes benchmarks — refetch every time the pane becomes active.
   */
  active?: boolean;
}

interface ReviewRow extends AuthoredItem {
  approved: boolean;
  /**
   * Raw editing buffer for expected_values rows: JSON must stay editable
   * through invalid intermediate states, so the text is kept verbatim and
   * only parsed into answer_spec once it is valid JSON again.
   */
  specText: string;
  specInvalid: boolean;
}

const readFileText = (file: File): Promise<string> =>
  new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result ?? ''));
    reader.onerror = () => reject(reader.error);
    reader.readAsText(file);
  });

const answerPreview = (item: AuthoredItem): string => {
  if (item.answer_type === 'gold_sql') {
    return String(item.answer_spec.sql ?? '');
  }
  if (item.answer_type === 'eval_note') {
    return String(item.answer_spec.note ?? '');
  }
  return JSON.stringify(item.answer_spec);
};

export default function AuthoringPanel({
  projectId,
  canWrite,
  active = true,
}: AuthoringPanelProps) {
  const dispatch = useDispatch();
  const [benchmarks, setBenchmarks] = useState<Benchmark[]>([]);
  const [benchmarkId, setBenchmarkId] = useState<string>();
  const [newBenchmarkName, setNewBenchmarkName] = useState('');
  const [creating, setCreating] = useState(false);
  const [csvText, setCsvText] = useState('');
  const [csvName, setCsvName] = useState<string>();
  const [contextText, setContextText] = useState('');
  const [contextName, setContextName] = useState<string>();
  const [mode, setMode] = useState<AuthoringMode>('extract');
  const [running, setRunning] = useState(false);
  const [importing, setImporting] = useState(false);
  const [steps, setSteps] = useState<AgentStep[]>([]);
  const [draft, setDraft] = useState<AuthoringDraft>();
  const [rows, setRows] = useState<ReviewRow[]>([]);
  // A hidden <input type="file"> cannot be opened by a nested antd <Button>
  // inside a <label> — an interactive element swallows the label activation
  // (HTML spec). Follow the AttachDocumentDialog idiom: a ref-held sibling
  // input opened programmatically from the button's onClick.
  const csvInputRef = useRef<HTMLInputElement>(null);
  const contextInputRef = useRef<HTMLInputElement>(null);
  // Lets a hung or failed streaming request be cancelled so the Author
  // button never stays stuck in its loading state (the retry path).
  const abortRef = useRef<AbortController>();
  useEffect(() => () => abortRef.current?.abort(), []);

  const refreshBenchmarks = useCallback(async (): Promise<void> => {
    try {
      const loaded = await listBenchmarks(projectId);
      setBenchmarks(loaded);
      // Keep the selection only while it still exists (it may have been
      // deleted elsewhere, or belong to a previously selected project).
      setBenchmarkId(prev =>
        prev && loaded.some(benchmark => benchmark.id === prev)
          ? prev
          : loaded[0]?.id,
      );
    } catch (ex) {
      dispatch(
        addDangerToast(
          t('Could not load benchmarks: %s', (ex as Error).message),
        ),
      );
    }
  }, [projectId, dispatch]);

  useEffect(() => {
    if (active) {
      refreshBenchmarks();
    }
  }, [active, refreshBenchmarks]);

  const onCreateBenchmark = useCallback(async () => {
    const name = newBenchmarkName.trim();
    if (!name) {
      return;
    }
    setCreating(true);
    try {
      const created = await createBenchmark(projectId, { name });
      setNewBenchmarkName('');
      setBenchmarks(prev => [...prev, created]);
      setBenchmarkId(created.id);
    } catch (ex) {
      dispatch(
        addDangerToast(
          t('Could not create benchmark: %s', (ex as Error).message),
        ),
      );
    } finally {
      setCreating(false);
    }
  }, [projectId, newBenchmarkName, dispatch]);

  const onFile = useCallback(
    async (
      event: ChangeEvent<HTMLInputElement>,
      kind: 'csv' | 'context',
    ): Promise<void> => {
      const input = event.target;
      const file = input.files?.[0];
      // Reset so re-selecting the SAME file still fires onChange next time.
      input.value = '';
      if (!file) {
        return;
      }
      try {
        const text = await readFileText(file);
        if (kind === 'csv') {
          setCsvText(text);
          setCsvName(file.name);
        } else {
          setContextText(text);
          setContextName(file.name);
        }
      } catch {
        dispatch(addDangerToast(t('Could not read %s', file.name)));
      }
    },
    [dispatch],
  );

  const onAuthor = useCallback(async () => {
    if (!benchmarkId || !csvText) {
      return;
    }
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    setRunning(true);
    setSteps([]);
    setDraft(undefined);
    setRows([]);
    try {
      const result = await streamBenchmarkAuthoring(
        projectId,
        benchmarkId,
        {
          csv_text: csvText,
          context_text: contextText || undefined,
          mode,
        },
        {
          onProgress: step => setSteps(prev => [...prev, step]),
          signal: controller.signal,
        },
      );
      setDraft(result);
      setRows(
        result.items.map(item => ({
          ...item,
          // Verified items pre-approve; flagged ones need an explicit human tick.
          approved: item.validation === 'verified',
          specText: answerPreview(item),
          specInvalid: false,
        })),
      );
    } catch (ex) {
      if ((ex as Error).name !== 'AbortError') {
        dispatch(
          addDangerToast(
            t(
              'Authoring failed: %s — click "Author draft" to retry.',
              (ex as Error).message,
            ),
          ),
        );
      }
    } finally {
      setRunning(false);
    }
  }, [projectId, benchmarkId, csvText, contextText, mode, dispatch]);

  const onImport = useCallback(async () => {
    if (!benchmarkId) {
      return;
    }
    const approved = rows.filter(row => row.approved);
    if (!approved.length) {
      return;
    }
    setImporting(true);
    try {
      const result = await importBenchmarkItems(
        projectId,
        benchmarkId,
        approved.map(row => ({
          question: row.question,
          answer_type: row.answer_type,
          answer_spec: row.answer_spec,
          capability_tags: row.capability_tags,
          // Import IS the review approval (P3.4): stamp verified_by.
          verified: true,
        })),
      );
      dispatch(
        addSuccessToast(
          t(
            'Imported %s item(s); %s duplicate(s) skipped. ' +
              'Review and run them in the Benchmarks tab.',
            result.created,
            result.skipped_duplicates,
          ),
        ),
      );
      result.errors.forEach(error => dispatch(addDangerToast(error)));
      setRows(prev => prev.filter(row => !row.approved));
      // Keep the dropdown's item counts honest after the bulk insert.
      await refreshBenchmarks();
    } catch (ex) {
      dispatch(addDangerToast(t('Import failed: %s', (ex as Error).message)));
    } finally {
      setImporting(false);
    }
  }, [projectId, benchmarkId, rows, dispatch, refreshBenchmarks]);

  const approvedCount = useMemo(
    () => rows.filter(row => row.approved).length,
    [rows],
  );
  const approvedInvalidCount = useMemo(
    () => rows.filter(row => row.approved && row.specInvalid).length,
    [rows],
  );

  if (!canWrite) {
    return (
      <PanelRoot>
        <Alert
          type="info"
          message={t('You need write access to author benchmark items.')}
        />
      </PanelRoot>
    );
  }

  return (
    <PanelRoot data-test="authoring-panel">
      <Typography.Text type="secondary">
        {t(
          'Upload a question/context CSV; the authoring agent drafts benchmark ' +
            'items (validating gold SQL against the live database). Review, ' +
            'then import the approved rows.',
        )}
      </Typography.Text>

      {benchmarks.length === 0 && (
        <Flex gap={8} wrap="wrap" align="center">
          <Alert
            type="info"
            message={t(
              'Authored items are imported into a benchmark — create one first.',
            )}
          />
          <Input
            data-test="authoring-new-benchmark-name"
            placeholder={t('New benchmark name')}
            value={newBenchmarkName}
            onChange={event => setNewBenchmarkName(event.target.value)}
            onPressEnter={onCreateBenchmark}
            css={css`
              width: 200px;
            `}
          />
          <Button
            data-test="authoring-create-benchmark"
            loading={creating}
            disabled={!newBenchmarkName.trim()}
            onClick={onCreateBenchmark}
          >
            {t('Create benchmark')}
          </Button>
        </Flex>
      )}

      <Flex gap={8} wrap="wrap" align="center">
        <Select
          data-test="authoring-benchmark-select"
          placeholder={t('Benchmark')}
          value={benchmarkId}
          onChange={value => setBenchmarkId(value as string)}
          options={benchmarks.map(benchmark => ({
            label: t('%s (%s items)', benchmark.name, benchmark.item_count),
            value: benchmark.id,
          }))}
          css={css`
            min-width: 180px;
          `}
        />
        <Select
          value={mode}
          onChange={value => setMode(value as AuthoringMode)}
          options={[
            { label: t('Extract (answer my questions)'), value: 'extract' },
            { label: t('Generate (invent questions)'), value: 'generate' },
            { label: t('Both'), value: 'both' },
          ]}
          css={css`
            min-width: 210px;
          `}
        />
        <input
          ref={csvInputRef}
          data-test="authoring-csv-input"
          type="file"
          accept=".csv,text/csv"
          css={css`
            display: none;
          `}
          onChange={event => onFile(event, 'csv')}
        />
        <Button
          size="small"
          data-test="authoring-csv-choose"
          onClick={() => csvInputRef.current?.click()}
        >
          {csvName ? t('CSV: %s', csvName) : t('Choose CSV…')}
        </Button>
        <input
          ref={contextInputRef}
          data-test="authoring-context-input"
          type="file"
          accept=".md,.txt,text/markdown,text/plain"
          css={css`
            display: none;
          `}
          onChange={event => onFile(event, 'context')}
        />
        <Button
          size="small"
          data-test="authoring-context-choose"
          onClick={() => contextInputRef.current?.click()}
        >
          {contextName ? t('Context: %s', contextName) : t('Context .md (optional)')}
        </Button>
        <Button
          type="primary"
          data-test="authoring-run"
          disabled={!benchmarkId || !csvText}
          loading={running}
          onClick={onAuthor}
        >
          {t('Author draft')}
        </Button>
        {running && (
          <Button
            data-test="authoring-cancel"
            onClick={() => abortRef.current?.abort()}
          >
            {t('Cancel')}
          </Button>
        )}
      </Flex>

      {steps.length > 0 && (
        <List
          data-test="authoring-steps"
          size="small"
          bordered
          dataSource={steps}
          renderItem={step => (
            <List.Item>
              <StepLine>
                <Tag
                  color={
                    step.status === 'error'
                      ? 'error'
                      : step.status === 'warning'
                        ? 'warning'
                        : 'default'
                  }
                >
                  {step.kind}
                </Tag>{' '}
                {step.summary}
              </StepLine>
            </List.Item>
          )}
        />
      )}

      {draft?.warnings.map(warning => (
        <Alert key={warning} type="warning" message={warning} closable />
      ))}
      {draft?.model_failed && (
        <Alert
          type="error"
          message={t(
            'The model failed mid-pass; only validated human rows are shown.',
          )}
        />
      )}

      {draft?.context_doc && (
        <Typography.Text type="secondary" data-test="authoring-context-note">
          {t(
            'The context document grounded this authoring pass only. To make ' +
              'it available to the SQL agent at query time, upload it under ' +
              "the project's Documents.",
          )}
        </Typography.Text>
      )}

      {draft && rows.length === 0 && (
        <Empty description={t('The pass produced no importable items.')} />
      )}

      {rows.length > 0 && (
        <>
          <Flex gap={8} align="center" wrap="wrap">
            <Button
              size="small"
              data-test="authoring-approve-all"
              disabled={approvedCount === rows.length}
              onClick={() =>
                setRows(prev => prev.map(row => ({ ...row, approved: true })))
              }
            >
              {t('Approve all (%s)', rows.length)}
            </Button>
            <Button
              size="small"
              data-test="authoring-approve-none"
              disabled={!approvedCount}
              onClick={() =>
                setRows(prev => prev.map(row => ({ ...row, approved: false })))
              }
            >
              {t('Clear approvals')}
            </Button>
            <Typography.Text type="secondary">
              {t('%s of %s approved', approvedCount, rows.length)}
            </Typography.Text>
          </Flex>
          <List
            data-test="authoring-draft-rows"
            bordered
            dataSource={rows}
            renderItem={(row, index) => (
              <List.Item
                actions={[
                  <Checkbox
                    key="approve"
                    data-test={`authoring-approve-${index}`}
                    checked={row.approved}
                    onChange={event =>
                      setRows(prev =>
                        prev.map((r, i) =>
                          i === index
                            ? { ...r, approved: event.target.checked }
                            : r,
                        ),
                      )
                    }
                  >
                    {t('Approve')}
                  </Checkbox>,
                ]}
              >
                <List.Item.Meta
                  title={
                    <Flex gap={8} align="center" wrap="wrap">
                      <Typography.Text strong>{row.question}</Typography.Text>
                      <Tag color={VALIDATION_COLORS[row.validation]}>
                        {row.validation}
                      </Tag>
                      <Tag color={ORIGIN_COLORS[row.origin]}>{row.origin}</Tag>
                      <Tag>{row.answer_type}</Tag>
                      {row.capability_tags.map(tag => (
                        <Tag key={tag}>{tag}</Tag>
                      ))}
                    </Flex>
                  }
                  description={
                    <>
                      <Input.TextArea
                        data-test={`authoring-spec-${index}`}
                        autoSize={{ minRows: 1, maxRows: 6 }}
                        value={
                          row.answer_type === 'expected_values'
                            ? row.specText
                            : answerPreview(row)
                        }
                        onChange={event =>
                          setRows(prev =>
                            prev.map((r, i) => {
                              if (i !== index) {
                                return r;
                              }
                              const value = event.target.value;
                              if (r.answer_type === 'gold_sql') {
                                return {
                                  ...r,
                                  answer_spec: { sql: value },
                                };
                              }
                              if (r.answer_type === 'eval_note') {
                                return {
                                  ...r,
                                  answer_spec: { note: value },
                                };
                              }
                              let parsed:
                                | Record<string, unknown>
                                | undefined;
                              try {
                                const candidate = JSON.parse(value);
                                if (
                                  candidate &&
                                  typeof candidate === 'object' &&
                                  !Array.isArray(candidate)
                                ) {
                                  parsed = candidate;
                                }
                              } catch {
                                // keep editing through invalid states
                              }
                              return {
                                ...r,
                                specText: value,
                                specInvalid: !parsed,
                                answer_spec: parsed ?? r.answer_spec,
                              };
                            }),
                          )
                        }
                      />
                      {row.specInvalid && (
                        <Typography.Text
                          type="danger"
                          data-test={`authoring-spec-invalid-${index}`}
                        >
                          {t(
                            'Expected values must be a JSON object, e.g. ' +
                              '{"nums": [42], "names": ["EMEA"]}',
                          )}
                        </Typography.Text>
                      )}
                      {row.problems.map(problem => (
                        <Typography.Text key={problem} type="warning">
                          {problem}
                        </Typography.Text>
                      ))}
                    </>
                  }
                />
              </List.Item>
            )}
          />
          <Flex gap={8} align="center">
            <Button
              type="primary"
              data-test="authoring-import"
              disabled={!approvedCount || approvedInvalidCount > 0}
              loading={importing}
              onClick={onImport}
            >
              {t('Import %s approved item(s)', approvedCount)}
            </Button>
            {approvedInvalidCount > 0 && (
              <Typography.Text type="danger">
                {t(
                  '%s approved row(s) have invalid JSON — fix them to import.',
                  approvedInvalidCount,
                )}
              </Typography.Text>
            )}
          </Flex>
        </>
      )}
    </PanelRoot>
  );
}
