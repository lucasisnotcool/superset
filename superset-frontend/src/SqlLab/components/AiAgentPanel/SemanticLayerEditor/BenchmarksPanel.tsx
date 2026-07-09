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
import { useCallback, useEffect, useRef, useState } from 'react';
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
  Modal,
  Popconfirm,
  Select,
  Tag,
  Typography,
} from '@superset-ui/core/components';
import { Icons } from '@superset-ui/core/components/Icons';
import {
  addDangerToast,
  addSuccessToast,
} from 'src/components/MessageToasts/actions';
import {
  analyzeBenchmarkRun,
  CopilotHandoffResponse,
  handoffBenchmarkRunToCopilot,
  Benchmark,
  BenchmarkAnswerType,
  BenchmarkItem,
  BenchmarkResult,
  BenchmarkRun,
  BenchmarkRunComparison,
  BenchmarkVerdict,
  compareBenchmarkRuns,
  createBenchmark,
  createBenchmarkItem,
  deleteBenchmarkItem,
  dryRunBenchmarkItem,
  getBenchmarkRun,
  importGoldenAsBenchmarkItems,
  listBenchmarkItems,
  listBenchmarkRunResults,
  listBenchmarkRuns,
  listBenchmarks,
  overrideBenchmarkResult,
  promoteBenchmarkItem,
  ScientistReport,
  startBenchmarkRun,
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

const VERDICT_COLORS: Record<BenchmarkVerdict, string> = {
  pass: 'success',
  fail: 'error',
  needs_review: 'warning',
  error: 'default',
};

const RUN_POLL_MS = 3000;

const verdictTag = (verdict: BenchmarkVerdict) => (
  <Tag color={VERDICT_COLORS[verdict]}>{verdict}</Tag>
);

const rowsPreviewText = (rows?: Record<string, unknown>[] | null) => {
  if (!rows || rows.length === 0) {
    return t('(no rows)');
  }
  return JSON.stringify(rows.slice(0, 5), null, 1);
};

export interface BenchmarksPanelProps {
  projectId: string;
  canWrite: boolean;
}

export default function BenchmarksPanel({
  projectId,
  canWrite,
}: BenchmarksPanelProps) {
  const dispatch = useDispatch();
  const [benchmarks, setBenchmarks] = useState<Benchmark[]>([]);
  const [benchmarkId, setBenchmarkId] = useState<string | undefined>(undefined);
  const [items, setItems] = useState<BenchmarkItem[]>([]);
  const [runs, setRuns] = useState<BenchmarkRun[]>([]);
  const [isLoading, setIsLoading] = useState(false);

  const [newBenchmarkName, setNewBenchmarkName] = useState('');
  const [addOpen, setAddOpen] = useState(false);
  const [question, setQuestion] = useState('');
  const [answerType, setAnswerType] = useState<BenchmarkAnswerType>('gold_sql');
  const [answerText, setAnswerText] = useState('');
  const [tagsText, setTagsText] = useState('');
  const [useAsExample, setUseAsExample] = useState(false);

  const [trials, setTrials] = useState(1);
  const [activeRun, setActiveRun] = useState<BenchmarkRun | undefined>(
    undefined,
  );
  const [openRun, setOpenRun] = useState<BenchmarkRun | undefined>(undefined);
  const [openRunResults, setOpenRunResults] = useState<BenchmarkResult[]>([]);
  const [compareWith, setCompareWith] = useState<string | undefined>(undefined);
  const [comparison, setComparison] = useState<
    BenchmarkRunComparison | undefined
  >(undefined);
  const [dryRunText, setDryRunText] = useState<string | undefined>(undefined);
  const [analysis, setAnalysis] = useState<ScientistReport | undefined>(
    undefined,
  );
  const [analyzing, setAnalyzing] = useState(false);
  const [handoff, setHandoff] = useState<CopilotHandoffResponse | undefined>(
    undefined,
  );
  const [handingOff, setHandingOff] = useState(false);
  const pollRef = useRef<ReturnType<typeof setTimeout>>();

  const toastError = useCallback(
    (ex: unknown, fallback: string) => {
      dispatch(addDangerToast(ex instanceof Error ? ex.message : fallback));
    },
    [dispatch],
  );

  const refreshBenchmarks = useCallback(async () => {
    if (!projectId) {
      return;
    }
    setIsLoading(true);
    try {
      const loaded = await listBenchmarks(projectId);
      setBenchmarks(loaded);
      setBenchmarkId(current => current ?? loaded[0]?.id);
    } catch (ex) {
      toastError(ex, t('Unable to load benchmarks'));
    } finally {
      setIsLoading(false);
    }
  }, [projectId, toastError]);

  const refreshBenchmark = useCallback(async () => {
    if (!projectId || !benchmarkId) {
      setItems([]);
      setRuns([]);
      return;
    }
    try {
      const [loadedItems, loadedRuns] = await Promise.all([
        listBenchmarkItems(projectId, benchmarkId),
        listBenchmarkRuns(projectId, benchmarkId),
      ]);
      setItems(loadedItems);
      setRuns(loadedRuns);
      setActiveRun(
        loadedRuns.find(r => r.status === 'pending' || r.status === 'running'),
      );
    } catch (ex) {
      toastError(ex, t('Unable to load benchmark'));
    }
  }, [projectId, benchmarkId, toastError]);

  useEffect(() => {
    refreshBenchmarks();
  }, [refreshBenchmarks]);

  useEffect(() => {
    refreshBenchmark();
  }, [refreshBenchmark]);

  // Poll while a run is in flight so progress + completion surface without a
  // manual refresh (SSE events also nudge the project view; polling is the
  // dependable fallback).
  useEffect(() => {
    if (!activeRun || !projectId || !benchmarkId) {
      return undefined;
    }
    pollRef.current = setTimeout(async () => {
      try {
        const run = await getBenchmarkRun(projectId, benchmarkId, activeRun.id);
        if (run.status === 'pending' || run.status === 'running') {
          setActiveRun(run);
        } else {
          setActiveRun(undefined);
          await refreshBenchmark();
        }
      } catch {
        setActiveRun(undefined);
      }
    }, RUN_POLL_MS);
    return () => clearTimeout(pollRef.current);
  }, [activeRun, projectId, benchmarkId, refreshBenchmark]);

  const onCreateBenchmark = async () => {
    const name = newBenchmarkName.trim();
    if (!name) {
      return;
    }
    try {
      const created = await createBenchmark(projectId, { name });
      setNewBenchmarkName('');
      await refreshBenchmarks();
      setBenchmarkId(created.id);
    } catch (ex) {
      toastError(ex, t('Unable to create benchmark'));
    }
  };

  const buildAnswerSpec = (): Record<string, unknown> | undefined => {
    if (answerType === 'gold_sql') {
      return { sql: answerText };
    }
    if (answerType === 'eval_note') {
      return { note: answerText };
    }
    try {
      return JSON.parse(answerText || '{}');
    } catch {
      dispatch(
        addDangerToast(
          t(
            'Expected values must be JSON, e.g. {"nums": [42], "names": ["EMEA"]}',
          ),
        ),
      );
      return undefined;
    }
  };

  const onAddItem = async () => {
    if (!benchmarkId) {
      return;
    }
    const spec = buildAnswerSpec();
    if (!spec) {
      return;
    }
    try {
      await createBenchmarkItem(projectId, benchmarkId, {
        question,
        answer_type: answerType,
        answer_spec: spec,
        capability_tags: tagsText
          .split(',')
          .map(tag => tag.trim())
          .filter(Boolean),
        use_as_example: useAsExample,
      });
      dispatch(addSuccessToast(t('Test question added.')));
      setAddOpen(false);
      setQuestion('');
      setAnswerText('');
      setTagsText('');
      setUseAsExample(false);
      await refreshBenchmark();
    } catch (ex) {
      toastError(ex, t('Unable to add test question'));
    }
  };

  const onDryRun = async (item: BenchmarkItem) => {
    if (!benchmarkId) {
      return;
    }
    try {
      const preview = await dryRunBenchmarkItem(
        projectId,
        benchmarkId,
        item.id,
      );
      if (preview.answer_type === 'gold_sql') {
        setDryRunText(
          `${t('Gold result')} (${preview.row_count} ${t('rows')}):\n` +
            `${rowsPreviewText(preview.rows)}`,
        );
      } else if (preview.answer_type === 'expected_values') {
        setDryRunText(
          preview.problems.length
            ? `${t('Spec problems')}: ${preview.problems.join('; ')}`
            : `${t('Spec is valid')}: ${JSON.stringify(preview.spec)}`,
        );
      } else {
        setDryRunText(`${t('Judge note')}: ${preview.note ?? ''}`);
      }
    } catch (ex) {
      toastError(ex, t('Dry run failed'));
    }
  };

  // Single-config paradigm (plan_benchmark_authoring_agent_impl.md §1.1): a
  // run always tests the agent as-is — no model override, no config arms.
  const onStartRun = async () => {
    if (!benchmarkId) {
      return;
    }
    try {
      const submitted = await startBenchmarkRun(projectId, benchmarkId, {
        trials,
      });
      dispatch(
        addSuccessToast(
          t('Benchmark run started (%s questions).', submitted.total_items),
        ),
      );
      await refreshBenchmark();
    } catch (ex) {
      toastError(ex, t('Unable to start benchmark run'));
    }
  };

  const onOpenRun = async (run: BenchmarkRun) => {
    if (!benchmarkId) {
      return;
    }
    setOpenRun(run);
    setComparison(undefined);
    setCompareWith(undefined);
    setAnalysis(undefined);
    setHandoff(undefined);
    try {
      setOpenRunResults(
        await listBenchmarkRunResults(projectId, benchmarkId, run.id),
      );
    } catch (ex) {
      toastError(ex, t('Unable to load run results'));
    }
  };

  const onCompare = async (otherRunId: string) => {
    if (!benchmarkId || !openRun) {
      return;
    }
    setCompareWith(otherRunId);
    try {
      setComparison(
        await compareBenchmarkRuns(
          projectId,
          benchmarkId,
          openRun.id,
          otherRunId,
        ),
      );
    } catch (ex) {
      toastError(ex, t('Unable to compare runs'));
    }
  };

  const onAnalyze = async () => {
    if (!benchmarkId || !openRun) {
      return;
    }
    setAnalyzing(true);
    try {
      const outcome = await analyzeBenchmarkRun(
        projectId,
        benchmarkId,
        openRun.id,
      );
      setAnalysis(outcome.report);
    } catch (ex) {
      toastError(ex, t('Analysis failed'));
    } finally {
      setAnalyzing(false);
    }
  };

  const onHandoff = async () => {
    if (!benchmarkId || !openRun) {
      return;
    }
    setHandingOff(true);
    try {
      setHandoff(
        await handoffBenchmarkRunToCopilot(projectId, benchmarkId, openRun.id),
      );
    } catch (ex) {
      toastError(ex, t('Copilot handoff failed'));
    } finally {
      setHandingOff(false);
    }
  };

  const onOverride = async (
    result: BenchmarkResult,
    verdict: BenchmarkVerdict,
  ) => {
    if (!benchmarkId || !openRun) {
      return;
    }
    try {
      await overrideBenchmarkResult(
        projectId,
        benchmarkId,
        openRun.id,
        result.id,
        { verdict },
      );
      dispatch(addSuccessToast(t('Verdict overridden.')));
      await onOpenRun(
        await getBenchmarkRun(projectId, benchmarkId, openRun.id),
      );
      await refreshBenchmark();
    } catch (ex) {
      toastError(ex, t('Unable to override verdict'));
    }
  };

  const runLabel = (run: BenchmarkRun) => {
    const when = new Date(run.created_at).toLocaleString();
    const score =
      run.score !== null && run.score !== undefined
        ? ` — ${Math.round(run.score * 100)}%`
        : '';
    return `${when}${score} (${run.status})`;
  };

  const answerPlaceholder =
    answerType === 'gold_sql'
      ? t('SELECT region, sum(revenue) FROM ... (ground-truth SQL)')
      : answerType === 'expected_values'
        ? '{"nums": [1234.5], "names": ["EMEA"], "absent": [], "trap": false}'
        : t('What a correct answer must contain (reviewed by a human for now)');

  return (
    <PanelRoot data-test="semantic-layer-benchmarks">
      <Alert
        type="info"
        showIcon
        data-test="benchmarks-note"
        message={t(
          'Benchmarks score the AI SQL agent against your curated test ' +
            'questions and ground truth, so you can measure whether a model ' +
            'change helped. Verdicts compare executed data, not SQL text.',
        )}
      />

      <Flex gap="small" align="center" wrap>
        <Select
          data-test="benchmark-select"
          css={css`
            min-width: 220px;
          `}
          placeholder={t('Select a benchmark')}
          value={benchmarkId}
          onChange={value => setBenchmarkId(value as string)}
          options={benchmarks.map(benchmark => ({
            value: benchmark.id,
            label: `${benchmark.name} (${benchmark.item_count})`,
          }))}
        />
        {canWrite && (
          <>
            <Input
              data-test="new-benchmark-name"
              css={css`
                width: 200px;
              `}
              placeholder={t('New benchmark name')}
              value={newBenchmarkName}
              onChange={event => setNewBenchmarkName(event.target.value)}
              onPressEnter={onCreateBenchmark}
            />
            <Button
              buttonStyle="secondary"
              onClick={onCreateBenchmark}
              disabled={!newBenchmarkName.trim()}
            >
              {t('Create')}
            </Button>
          </>
        )}
      </Flex>

      {benchmarkId && (
        <>
          <Flex gap="small" align="center" wrap>
            {canWrite && (
              <Button
                data-test="add-benchmark-item"
                buttonStyle="primary"
                icon={<Icons.PlusOutlined iconSize="m" />}
                onClick={() => setAddOpen(true)}
              >
                {t('Add question')}
              </Button>
            )}
            {canWrite && (
              <Button
                data-test="import-golden"
                buttonStyle="secondary"
                onClick={async () => {
                  try {
                    const imported = await importGoldenAsBenchmarkItems(
                      projectId,
                      benchmarkId,
                    );
                    dispatch(
                      addSuccessToast(
                        t(
                          'Imported %s golden queries (%s duplicates skipped).',
                          imported.created,
                          imported.skipped_duplicates,
                        ),
                      ),
                    );
                    await refreshBenchmark();
                  } catch (ex) {
                    toastError(ex, t('Golden import failed'));
                  }
                }}
              >
                {t('Import golden queries')}
              </Button>
            )}
            <Select
              data-test="trials-select"
              value={trials}
              onChange={value => setTrials(Number(value))}
              options={[
                { value: 1, label: t('1 trial') },
                { value: 3, label: t('3 trials (reliability)') },
              ]}
            />
            {canWrite && (
              <Button
                data-test="run-benchmark"
                buttonStyle="primary"
                disabled={items.length === 0 || Boolean(activeRun)}
                onClick={onStartRun}
              >
                {t('Run benchmark')}
              </Button>
            )}
            {activeRun && (
              <Tag color="processing" data-test="run-progress">
                {t(
                  'Running… %s/%s',
                  activeRun.progress?.completed ?? 0,
                  activeRun.progress?.total ?? items.length,
                )}
              </Tag>
            )}
          </Flex>

          {dryRunText !== undefined && (
            <Alert
              type="info"
              closable
              onClose={() => setDryRunText(undefined)}
              data-test="dry-run-preview"
              message={
                <Typography.Text
                  code
                  css={css`
                    white-space: pre-wrap;
                  `}
                >
                  {dryRunText}
                </Typography.Text>
              }
            />
          )}

          <List
            data-test="benchmark-items"
            loading={isLoading}
            dataSource={items}
            locale={{
              emptyText: (
                <Empty
                  image={Empty.PRESENTED_IMAGE_SIMPLE}
                  description={t(
                    'No test questions yet. Add one or import golden queries.',
                  )}
                />
              ),
            }}
            renderItem={(item: BenchmarkItem) => (
              <List.Item
                key={item.id}
                actions={[
                  <Button
                    key="dry-run"
                    buttonStyle="link"
                    onClick={() => onDryRun(item)}
                  >
                    {t('Dry run')}
                  </Button>,
                  ...(canWrite && item.answer_type === 'gold_sql'
                    ? [
                        <Button
                          key="promote"
                          buttonStyle="link"
                          disabled={item.use_as_example}
                          onClick={async () => {
                            try {
                              await promoteBenchmarkItem(
                                projectId,
                                benchmarkId,
                                item.id,
                              );
                              dispatch(
                                addSuccessToast(t('Promoted to golden query.')),
                              );
                              await refreshBenchmark();
                            } catch (ex) {
                              toastError(ex, t('Promote failed'));
                            }
                          }}
                        >
                          {t('Promote')}
                        </Button>,
                      ]
                    : []),
                  ...(canWrite
                    ? [
                        <Popconfirm
                          key="delete"
                          title={t('Remove this test question?')}
                          okText={t('Remove')}
                          cancelText={t('Cancel')}
                          onConfirm={async () => {
                            try {
                              await deleteBenchmarkItem(
                                projectId,
                                benchmarkId,
                                item.id,
                              );
                              await refreshBenchmark();
                            } catch (ex) {
                              toastError(ex, t('Unable to remove question'));
                            }
                          }}
                        >
                          <Button
                            buttonStyle="link"
                            aria-label={t('Remove question')}
                            icon={<Icons.DeleteOutlined iconSize="m" />}
                          />
                        </Popconfirm>,
                      ]
                    : []),
                ]}
              >
                <Flex vertical gap={0}>
                  <Flex gap="small" align="center" wrap>
                    <Typography.Text strong>{item.question}</Typography.Text>
                    <Tag>{item.answer_type}</Tag>
                    {item.verified_at && (
                      <Tag color="success">{t('Verified')}</Tag>
                    )}
                    {item.use_as_example && (
                      <Tag color="blue">{t('Golden')}</Tag>
                    )}
                    {item.capability_tags.map(tag => (
                      <Tag key={tag}>{tag}</Tag>
                    ))}
                  </Flex>
                  {item.answer_type === 'gold_sql' && (
                    <Typography.Text
                      code
                      css={css`
                        white-space: pre-wrap;
                      `}
                    >
                      {String(item.answer_spec.sql ?? '')}
                    </Typography.Text>
                  )}
                </Flex>
              </List.Item>
            )}
          />

          <Typography.Title level={5}>{t('Evaluations')}</Typography.Title>
          <List
            data-test="benchmark-runs"
            dataSource={runs}
            locale={{
              emptyText: (
                <Empty
                  image={Empty.PRESENTED_IMAGE_SIMPLE}
                  description={t('No runs yet.')}
                />
              ),
            }}
            renderItem={(run: BenchmarkRun) => (
              <List.Item
                key={run.id}
                actions={[
                  <Button
                    key="open"
                    buttonStyle="link"
                    onClick={() => onOpenRun(run)}
                  >
                    {t('Details')}
                  </Button>,
                ]}
              >
                <Flex gap="small" align="center" wrap>
                  <Typography.Text>{runLabel(run)}</Typography.Text>
                  {run.totals?.by_capability &&
                    Object.entries(run.totals.by_capability).map(
                      ([tag, bucket]) => (
                        <Tag key={tag}>
                          {tag} {bucket.passed}/{bucket.items}
                        </Tag>
                      ),
                    )}
                  {run.totals && (
                    <Typography.Text type="secondary">
                      {t(
                        '%s passed / %s failed / %s review / %s errors',
                        run.totals.passed,
                        run.totals.failed,
                        run.totals.needs_review,
                        run.totals.errors,
                      )}
                      {run.totals.pass_hat_k !== null &&
                      run.totals.pass_hat_k !== undefined
                        ? ` — pass^k ${Math.round(run.totals.pass_hat_k * 100)}%`
                        : ''}
                    </Typography.Text>
                  )}
                </Flex>
              </List.Item>
            )}
          />
        </>
      )}

      <Modal
        title={t('Add test question')}
        show={addOpen}
        onHide={() => setAddOpen(false)}
        destroyOnHidden
        footer={
          <>
            <Button buttonStyle="secondary" onClick={() => setAddOpen(false)}>
              {t('Cancel')}
            </Button>
            <Button
              buttonStyle="primary"
              data-test="item-submit"
              disabled={!question.trim() || !answerText.trim()}
              onClick={onAddItem}
            >
              {t('Add')}
            </Button>
          </>
        }
      >
        <Flex vertical gap="small">
          <Input.TextArea
            data-test="item-question"
            rows={2}
            placeholder={t('Natural-language question')}
            value={question}
            onChange={event => setQuestion(event.target.value)}
          />
          <Select
            data-test="item-answer-type"
            value={answerType}
            onChange={value => setAnswerType(value as BenchmarkAnswerType)}
            options={[
              { value: 'gold_sql', label: t('Ground-truth SQL (recommended)') },
              { value: 'expected_values', label: t('Expected values') },
              { value: 'eval_note', label: t('Free-text expectation') },
            ]}
          />
          <Input.TextArea
            data-test="item-answer-spec"
            rows={4}
            placeholder={answerPlaceholder}
            value={answerText}
            onChange={event => setAnswerText(event.target.value)}
          />
          <Input
            data-test="item-tags"
            placeholder={t('Capability tags (comma-separated, optional)')}
            value={tagsText}
            onChange={event => setTagsText(event.target.value)}
          />
          <Checkbox
            checked={useAsExample}
            onChange={event => setUseAsExample(event.target.checked)}
          >
            {t('Also use as a golden few-shot example')}
          </Checkbox>
        </Flex>
      </Modal>

      <Modal
        title={openRun ? t('Run details — %s', runLabel(openRun)) : ''}
        show={Boolean(openRun)}
        onHide={() => setOpenRun(undefined)}
        footer={null}
        width={880}
        destroyOnHidden
      >
        <Flex vertical gap="small">
          {canWrite && (
            <Flex gap="small" align="center">
              <Button
                data-test="analyze-run"
                buttonStyle="secondary"
                loading={analyzing}
                disabled={openRun?.status !== 'complete'}
                onClick={onAnalyze}
              >
                {t('Analyze failures')}
              </Button>
              <Button
                data-test="handoff-run"
                buttonStyle="secondary"
                loading={handingOff}
                disabled={openRun?.status !== 'complete'}
                onClick={onHandoff}
              >
                {t('Propose MDL fixes')}
              </Button>
            </Flex>
          )}
          {handoff && (
            <Alert
              data-test="handoff-result"
              type="success"
              showIcon
              message={t(
                '%s staged MDL edit(s) proposed — review and apply them in the Copilot panel, then re-run this benchmark to verify.',
                handoff.changeset.items.length,
              )}
              description={
                <Flex vertical gap={0}>
                  {handoff.changeset.items.map(item => (
                    <Typography.Text code key={item.op + item.path}>
                      {item.op} {item.path}
                    </Typography.Text>
                  ))}
                </Flex>
              }
            />
          )}
          {analysis && (
            <Alert
              data-test="analysis-report"
              type={analysis.within_noise ? 'warning' : 'info'}
              showIcon
              message={analysis.summary}
              description={
                <Flex vertical gap="small">
                  {analysis.stats_note && (
                    <Typography.Text type="secondary">
                      {analysis.stats_note}
                    </Typography.Text>
                  )}
                  {analysis.findings.map(finding => (
                    <Flex
                      key={finding.item_id + finding.question}
                      vertical
                      gap={0}
                    >
                      <Flex gap="small" align="center" wrap>
                        <Tag color={finding.test_suspect ? 'orange' : 'blue'}>
                          {finding.diagnosis}
                        </Tag>
                        <Typography.Text strong>
                          {finding.question}
                        </Typography.Text>
                        {finding.test_suspect && (
                          <Tag color="orange">{t('check the test')}</Tag>
                        )}
                      </Flex>
                      {finding.suggested_action && (
                        <Typography.Text>
                          {finding.suggested_action}
                        </Typography.Text>
                      )}
                      {finding.suggested_fix_type && (
                        <Typography.Text type="secondary">
                          {finding.suggested_fix_type}
                        </Typography.Text>
                      )}
                    </Flex>
                  ))}
                </Flex>
              }
            />
          )}
          <Flex gap="small" align="center">
            <Typography.Text>{t('Compare to:')}</Typography.Text>
            <Select
              data-test="compare-select"
              css={css`
                min-width: 260px;
              `}
              placeholder={t('Pick an earlier run')}
              value={compareWith}
              onChange={value => onCompare(value as string)}
              options={runs
                .filter(
                  run => run.id !== openRun?.id && run.status === 'complete',
                )
                .map(run => ({ value: run.id, label: runLabel(run) }))}
            />
          </Flex>
          {comparison && (
            <Alert
              data-test="comparison-banner"
              type={
                comparison.significant
                  ? comparison.delta >= 0
                    ? 'success'
                    : 'error'
                  : 'warning'
              }
              showIcon
              message={
                comparison.significant
                  ? t(
                      'Change of %s points (95%% CI %s to %s) on %s shared questions — statistically meaningful.',
                      Math.round(comparison.delta * 100),
                      Math.round(comparison.ci_low * 100),
                      Math.round(comparison.ci_high * 100),
                      comparison.n_items,
                    )
                  : t(
                      'Change of %s points is within noise (95%% CI %s to %s, n=%s) — do not act on it.',
                      Math.round(comparison.delta * 100),
                      Math.round(comparison.ci_low * 100),
                      Math.round(comparison.ci_high * 100),
                      comparison.n_items,
                    )
              }
              description={
                comparison.benchmark_changed
                  ? t(
                      'Warning: the benchmark contents changed between these runs; only shared questions are compared.',
                    )
                  : undefined
              }
            />
          )}
          <List
            data-test="run-results"
            dataSource={openRunResults}
            renderItem={(result: BenchmarkResult) => (
              <List.Item
                key={result.id}
                actions={
                  canWrite
                    ? [
                        <Button
                          key="mark-pass"
                          buttonStyle="link"
                          onClick={() => onOverride(result, 'pass')}
                        >
                          {t('Mark pass')}
                        </Button>,
                        <Button
                          key="mark-fail"
                          buttonStyle="link"
                          onClick={() => onOverride(result, 'fail')}
                        >
                          {t('Mark fail')}
                        </Button>,
                      ]
                    : undefined
                }
              >
                <Flex vertical gap={0}>
                  <Flex gap="small" align="center" wrap>
                    {verdictTag(
                      (result.override_verdict ??
                        result.verdict) as BenchmarkVerdict,
                    )}
                    {result.override_verdict && (
                      <Tag color="purple">
                        {t('Overridden by %s', result.override_by ?? '')}
                      </Tag>
                    )}
                    <Typography.Text strong>{result.question}</Typography.Text>
                    {result.trial_index > 0 && (
                      <Tag>{t('trial %s', result.trial_index + 1)}</Tag>
                    )}
                  </Flex>
                  {result.reasons.length > 0 && (
                    <Typography.Text type="secondary">
                      {result.reasons.join(' ')}
                    </Typography.Text>
                  )}
                  {result.agent_sql && (
                    <Typography.Text
                      code
                      css={css`
                        white-space: pre-wrap;
                      `}
                    >
                      {result.agent_sql}
                    </Typography.Text>
                  )}
                  {result.verdict !== 'pass' && (
                    <Flex gap="large" wrap>
                      <Typography.Text
                        code
                        css={css`
                          white-space: pre-wrap;
                        `}
                      >
                        {t('Agent:')}{' '}
                        {rowsPreviewText(result.agent_rows_preview)}
                      </Typography.Text>
                      <Typography.Text
                        code
                        css={css`
                          white-space: pre-wrap;
                        `}
                      >
                        {t('Expected:')}{' '}
                        {rowsPreviewText(result.gold_rows_preview)}
                      </Typography.Text>
                    </Flex>
                  )}
                </Flex>
              </List.Item>
            )}
          />
        </Flex>
      </Modal>
    </PanelRoot>
  );
}
