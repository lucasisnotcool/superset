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
}

interface ReviewRow extends AuthoredItem {
  approved: boolean;
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
}: AuthoringPanelProps) {
  const dispatch = useDispatch();
  const [benchmarks, setBenchmarks] = useState<Benchmark[]>([]);
  const [benchmarkId, setBenchmarkId] = useState<string>();
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
  useEffect(() => {
    let cancelled = false;
    listBenchmarks(projectId)
      .then(loaded => {
        if (cancelled) {
          return;
        }
        setBenchmarks(loaded);
        setBenchmarkId(prev => prev ?? loaded[0]?.id);
      })
      .catch((ex: Error) => {
        if (!cancelled) {
          dispatch(
            addDangerToast(t('Could not load benchmarks: %s', ex.message)),
          );
        }
      });
    return () => {
      cancelled = true;
    };
  }, [projectId, dispatch]);

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
        { onProgress: step => setSteps(prev => [...prev, step]) },
      );
      setDraft(result);
      setRows(
        result.items.map(item => ({
          ...item,
          // Verified items pre-approve; flagged ones need an explicit human tick.
          approved: item.validation === 'verified',
        })),
      );
    } catch (ex) {
      dispatch(addDangerToast(t('Authoring failed: %s', (ex as Error).message)));
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
            'Imported %s item(s); %s duplicate(s) skipped.',
            result.created,
            result.skipped_duplicates,
          ),
        ),
      );
      result.errors.forEach(error => dispatch(addDangerToast(error)));
      setRows(prev => prev.filter(row => !row.approved));
    } catch (ex) {
      dispatch(addDangerToast(t('Import failed: %s', (ex as Error).message)));
    } finally {
      setImporting(false);
    }
  }, [projectId, benchmarkId, rows, dispatch]);

  const approvedCount = useMemo(
    () => rows.filter(row => row.approved).length,
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

      <Flex gap={8} wrap="wrap" align="center">
        <Select
          data-test="authoring-benchmark-select"
          placeholder={t('Benchmark')}
          value={benchmarkId}
          onChange={value => setBenchmarkId(value as string)}
          options={benchmarks.map(benchmark => ({
            label: benchmark.name,
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

      {draft && rows.length === 0 && (
        <Empty description={t('The pass produced no importable items.')} />
      )}

      {rows.length > 0 && (
        <>
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
                        value={answerPreview(row)}
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
                              return r;
                            }),
                          )
                        }
                        disabled={row.answer_type === 'expected_values'}
                      />
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
          <Flex gap={8}>
            <Button
              type="primary"
              data-test="authoring-import"
              disabled={!approvedCount}
              loading={importing}
              onClick={onImport}
            >
              {t('Import %s approved item(s)', approvedCount)}
            </Button>
          </Flex>
        </>
      )}
    </PanelRoot>
  );
}
