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
import { useState } from 'react';
import { t } from '@apache-superset/core/translation';
import { css, styled } from '@apache-superset/core/theme';
import {
  Button,
  Collapse,
  Icons,
  InfoTooltip,
  Radio,
  Tag,
  Tooltip,
} from '@superset-ui/core/components';
import type {
  AgentStep,
  AgentStepDetail as Detail,
  RecalledExample,
  RetrievedChunk,
} from './api';

// Length past which a SQL block collapses by default so the timeline stays
// scannable (B3); shorter SQL renders inline.
const SQL_COLLAPSE_THRESHOLD = 160;

const List = styled.dl`
  ${({ theme }) => css`
    display: grid;
    grid-template-columns: max-content minmax(0, 1fr);
    gap: ${theme.sizeUnit}px ${theme.sizeUnit * 2}px;
    margin: ${theme.sizeUnit}px 0 0;
    font-size: ${theme.fontSizeSM}px;

    dt {
      color: ${theme.colorTextSecondary};
      white-space: nowrap;
    }

    dd {
      margin: 0;
      overflow-wrap: anywhere;
    }
  `}
`;

// `display: contents` removes this wrapper from layout so its dt/dd become
// direct grid items of the List dl, keeping the label/value column alignment.
const Field = styled.div`
  display: contents;
`;

const Code = styled.pre`
  ${({ theme }) => css`
    margin: ${theme.sizeUnit}px 0 0;
    padding: ${theme.sizeUnit}px ${theme.sizeUnit * 2}px;
    background: ${theme.colorBgLayout};
    border-radius: ${theme.borderRadius}px;
    font-size: ${theme.fontSizeSM}px;
    white-space: pre-wrap;
    overflow-wrap: anywhere;
  `}
`;

const CodeFrame = styled.div`
  position: relative;
`;

const CopyAffordance = styled.div`
  ${({ theme }) => css`
    position: absolute;
    top: ${theme.sizeUnit}px;
    right: ${theme.sizeUnit}px;
  `}
`;

const CollapsePanel = styled(Collapse)`
  ${({ theme }) => css`
    margin-top: ${theme.sizeUnit}px;
    background: transparent;
    font-size: ${theme.fontSizeSM}px;
  `}
`;

const Row = ({ label, value }: { label: string; value: React.ReactNode }) =>
  value === null || value === undefined || value === '' ? null : (
    <Field>
      <dt>{label}</dt>
      <dd>{value}</dd>
    </Field>
  );

// A small clipboard button with transient "Copied" feedback (B7). Degrades
// silently when the Clipboard API is unavailable (e.g. insecure context).
export const CopyButton = ({
  text,
  label = t('Copy'),
}: {
  text: string;
  label?: string;
}) => {
  const [copied, setCopied] = useState(false);
  const onCopy = () => {
    navigator.clipboard
      ?.writeText(text)
      .then(() => {
        setCopied(true);
        setTimeout(() => setCopied(false), 1500);
        return undefined;
      })
      .catch(() => undefined);
  };
  return (
    <Button
      buttonStyle="link"
      buttonSize="xsmall"
      aria-label={label}
      onClick={onCopy}
      icon={
        copied ? (
          <Icons.CheckOutlined iconSize="s" />
        ) : (
          <Icons.CopyOutlined iconSize="s" />
        )
      }
    />
  );
};

// A copyable code block; long SQL collapses behind a disclosure so a wide
// rewrite doesn't dominate the timeline (B3 + B7).
const CodeBlock = ({ code }: { code: string }) => {
  const body = (
    <CodeFrame>
      <Code>{code}</Code>
      <CopyAffordance>
        <CopyButton text={code} />
      </CopyAffordance>
    </CodeFrame>
  );
  if (code.length <= SQL_COLLAPSE_THRESHOLD) {
    return body;
  }
  return (
    <CollapsePanel
      ghost
      items={[{ key: 'sql', label: t('Show SQL'), children: body }]}
    />
  );
};

const Sql = ({ label, sql }: { label: string; sql?: string | null }) =>
  sql ? (
    <Field>
      <dt>{label}</dt>
      <dd>
        <CodeBlock code={sql} />
      </dd>
    </Field>
  ) : null;

type SqlForm = 'native' | 'semantic';

// The semantic->native rewrite carries two forms of the same query: the
// authored form against bare model names, and the native form with
// schema-qualified tables that actually ran. When both are present this offers
// a toggle that defaults to the native (executed) form — execution truth, and
// the only form that disambiguates which physical schema each table resolved to
// under a multi-schema project — with the authored form one click away. When
// only one form is present it falls back to a single labeled block.
const SqlRewrite = ({
  semanticSql,
  nativeSql,
}: {
  semanticSql?: string | null;
  nativeSql?: string | null;
}) => {
  const [form, setForm] = useState<SqlForm>('native');
  if (!semanticSql || !nativeSql) {
    if (nativeSql) {
      return <Sql label={t('Native SQL')} sql={nativeSql} />;
    }
    return <Sql label={t('Semantic SQL')} sql={semanticSql} />;
  }
  return (
    <Field>
      <dt>{t('SQL')}</dt>
      <dd>
        <Radio.GroupWrapper
          options={[
            { label: t('Native (executed)'), value: 'native' },
            { label: t('Semantic (authored)'), value: 'semantic' },
          ]}
          value={form}
          onChange={event => setForm(event.target.value as SqlForm)}
          optionType="button"
          buttonStyle="solid"
          size="small"
        />
        <CodeBlock code={form === 'native' ? nativeSql : semanticSql} />
      </dd>
    </Field>
  );
};

const TagRow = styled.div`
  ${({ theme }) => css`
    display: flex;
    flex-wrap: wrap;
    gap: ${theme.sizeUnit}px;
    margin-top: ${theme.sizeUnit}px;
  `}
`;

const Warnings = styled.ul`
  ${({ theme }) => css`
    margin: ${theme.sizeUnit}px 0 0;
    padding-left: ${theme.sizeUnit * 4}px;
    color: ${theme.colorWarning};
    font-size: ${theme.fontSizeSM}px;
  `}
`;

const ChunkList = styled.div`
  ${({ theme }) => css`
    display: flex;
    flex-direction: column;
    gap: ${theme.sizeUnit}px;
  `}
`;

const ChunkCard = styled.div`
  ${({ theme }) => css`
    padding: ${theme.sizeUnit}px ${theme.sizeUnit * 2}px;
    background: ${theme.colorBgLayout};
    border-radius: ${theme.borderRadius}px;
    font-size: ${theme.fontSizeSM}px;
  `}
`;

const ChunkHead = styled.div`
  ${({ theme }) => css`
    display: flex;
    align-items: center;
    gap: ${theme.sizeUnit}px;
    margin-bottom: ${theme.sizeUnit / 2}px;
    color: ${theme.colorTextSecondary};
  `}
`;

const ChunkText = styled.div`
  overflow-wrap: anywhere;
`;

// A wrapping list of names rendered as tags; renders nothing when empty so the
// surrounding grid Row collapses (A2).
const TagList = ({ items }: { items: string[] }) =>
  items.length ? (
    <TagRow>
      {items.map(name => (
        <Tag key={name}>{name}</Tag>
      ))}
    </TagRow>
  ) : null;

const WarningList = ({ messages }: { messages?: string[] | null }) =>
  messages && messages.length ? (
    <Warnings data-test="step-warnings">
      {messages.map(message => (
        <li key={message}>{message}</li>
      ))}
    </Warnings>
  ) : null;

const formatScore = (score?: number | null): string | null =>
  score === null || score === undefined ? null : score.toFixed(2);

const ChunkGroupHead = styled.div`
  ${({ theme }) => css`
    display: flex;
    align-items: center;
    gap: ${theme.sizeUnit}px;
    margin-top: ${theme.sizeUnit}px;
    color: ${theme.colorTextSecondary};
    font-weight: ${theme.fontWeightStrong};
  `}
`;

const GROUPLESS = '__ungrouped__';

// Order chunks into stable model groups, preserving first-seen (rank) order so
// the most relevant model leads. Modelless chunks (relationships) group last.
const groupChunksByModel = (
  chunks: RetrievedChunk[],
): { model: string | null; items: RetrievedChunk[] }[] => {
  const order: string[] = [];
  const groups = new Map<string, RetrievedChunk[]>();
  chunks.forEach(chunk => {
    const key = chunk.model ?? GROUPLESS;
    if (!groups.has(key)) {
      groups.set(key, []);
      order.push(key);
    }
    groups.get(key)?.push(chunk);
  });
  return order.map(key => ({
    model: key === GROUPLESS ? null : key,
    items: groups.get(key) ?? [],
  }));
};

const ChunkRow = ({
  chunk,
  index,
  used = false,
}: {
  chunk: RetrievedChunk;
  index: number;
  // A view chunk surfaced into the prompt ("used") gets a badge so the user sees
  // which vetted, named query grounded the answer.
  used?: boolean;
}) => {
  const score = formatScore(chunk.score);
  return (
    <ChunkCard key={`${chunk.model ?? ''}.${chunk.name ?? index}`}>
      <ChunkHead>
        {chunk.kind ? <Tag>{chunk.kind}</Tag> : null}
        {used ? <Tag color="success">{t('used')}</Tag> : null}
        {chunk.name || chunk.model}
        {score ? <span>· {t('score %s', score)}</span> : null}
      </ChunkHead>
      <ChunkText>{chunk.text}</ChunkText>
    </ChunkCard>
  );
};

// Collapsible list of the MDL chunks the retriever ranked into the prompt,
// grouped by their model so the question -> matched model -> columns path reads
// (A1 + B2). Models that the draft actually matched get a "matched" badge.
// Collapsed by default; the count + retriever mode read on the header.
const RetrievedChunks = ({
  chunks,
  retriever,
  matchedModels = [],
  matchedViews = [],
}: {
  chunks?: RetrievedChunk[] | null;
  retriever?: string | null;
  matchedModels?: string[];
  matchedViews?: string[];
}) => {
  if (!chunks || chunks.length === 0) {
    return null;
  }
  const label = retriever
    ? t('Retrieved chunks (%s · %s)', chunks.length, retriever)
    : t('Retrieved chunks (%s)', chunks.length);
  const matched = new Set(matchedModels);
  const usedViews = new Set(matchedViews);
  // Views are pulled into their own group so a question -> view provenance reads
  // distinctly from the model/column chunks; the rest keep the model grouping.
  const viewChunks = chunks.filter(chunk => chunk.kind === 'view');
  const groups = groupChunksByModel(
    chunks.filter(chunk => chunk.kind !== 'view'),
  );
  return (
    <CollapsePanel
      ghost
      data-test="retrieved-chunks"
      items={[
        {
          key: 'chunks',
          label,
          children: (
            <ChunkList>
              {groups.map(group => (
                <div key={group.model ?? GROUPLESS}>
                  {group.model ? (
                    <ChunkGroupHead>
                      {group.model}
                      {matched.has(group.model) ? (
                        <Tag color="success">{t('matched')}</Tag>
                      ) : null}
                    </ChunkGroupHead>
                  ) : null}
                  {group.items.map((chunk, index) => (
                    <ChunkRow
                      key={`${chunk.name ?? index}`}
                      chunk={chunk}
                      index={index}
                    />
                  ))}
                </div>
              ))}
              {viewChunks.length ? (
                <div key="__views__" data-test="view-chunks">
                  <ChunkGroupHead>{t('Views')}</ChunkGroupHead>
                  {viewChunks.map((chunk, index) => (
                    <ChunkRow
                      key={`view.${chunk.name ?? index}`}
                      chunk={chunk}
                      index={index}
                      used={usedViews.has(chunk.name ?? '')}
                    />
                  ))}
                </div>
              ) : null}
            </ChunkList>
          ),
        },
      ]}
    />
  );
};

const ProvenanceMeta = styled.div`
  ${({ theme }) => css`
    margin-bottom: ${theme.sizeUnit / 2}px;
    color: ${theme.colorTextTertiary};
    font-size: ${theme.fontSizeSM}px;
  `}
`;

type Provenance = { label: string; color?: string; title: string };

// Resolve where a recalled query came from (F3/2C) into a badge + tooltip, so
// the explain timeline shows provenance, not just the example text.
const provenanceOf = (example: RecalledExample): Provenance => {
  if (example.source === 'golden') {
    return example.verified
      ? {
          label: t('Verified'),
          color: 'success',
          title: example.name
            ? t('Curated, human-verified golden query: %s', example.name)
            : t('Curated, human-verified golden query.'),
        }
      : {
          label: t('Golden'),
          color: 'processing',
          title: example.name
            ? t('Curated golden query: %s', example.name)
            : t('Curated golden query (not yet verified).'),
        };
  }
  return example.in_scope === false
    ? {
        label: t('Learned · broader'),
        title: t(
          "Learned from a confirmed query outside this project's onboarded " +
            'tables — the SQL is reused, but semantic model names are hidden.',
        ),
      }
    : {
        label: t('Learned'),
        title: t(
          'Learned automatically from a past confirmed query on this database.',
        ),
      };
};

// Legend explaining the provenance badges, surfaced on the collapsible header so
// users can read *where each recalled query came from* at a glance.
const RECALLED_LEGEND = t(
  'Where each recalled query came from: Verified/Golden = curated project ' +
    'golden queries; Learned = auto-captured from past confirmed queries on ' +
    'this database; “broader” = learned from outside this project’s onboarded tables.',
);

// Collapsible list of the confirmed NL->SQL examples the memory seam recalled
// into the prompt (B1), each badged with its provenance (F3/2C). Collapsed by
// default; the count reads on the header.
const RecalledExamples = ({
  examples,
}: {
  examples?: RecalledExample[] | null;
}) => {
  if (!examples || examples.length === 0) {
    return null;
  }
  return (
    <CollapsePanel
      ghost
      data-test="recalled-examples"
      items={[
        {
          key: 'examples',
          label: (
            <span>
              {t('Recalled examples (%s)', examples.length)}
              <InfoTooltip
                tooltip={RECALLED_LEGEND}
                data-test="recalled-legend"
                css={css`
                  margin-inline-start: 6px;
                `}
              />
            </span>
          ),
          children: (
            <ChunkList>
              {examples.map((example, index) => {
                const prov = provenanceOf(example);
                return (
                  <ChunkCard key={`${example.question}-${index}`}>
                    <ChunkHead>
                      {example.question}
                      <Tooltip title={prov.title}>
                        <Tag
                          color={prov.color}
                          data-test="provenance-tag"
                          css={css`
                            margin-inline-start: 8px;
                          `}
                        >
                          {prov.label}
                        </Tag>
                      </Tooltip>
                    </ChunkHead>
                    {example.source === 'golden' && example.name ? (
                      <ProvenanceMeta data-test="provenance-name">
                        {t('Golden query · %s', example.name)}
                      </ProvenanceMeta>
                    ) : null}
                    {example.native_sql ? (
                      <Code>{example.native_sql}</Code>
                    ) : null}
                  </ChunkCard>
                );
              })}
            </ChunkList>
          ),
        },
      ]}
    />
  );
};

// Each branch renders only the fields the corresponding backend step produces
// (api.ts AgentStepDetail union). Falls through to `null` for an unknown shape,
// so a future detail kind never throws — the step still shows its summary.
function DetailBody({ detail }: { detail: Detail }) {
  switch (detail.kind) {
    case 'load_context': {
      const retrieval = detail.retrieval;
      const candidates = retrieval?.candidate_table_names ?? [];
      const scanned = retrieval?.scanned_table_count;
      const omitted = retrieval?.omitted_table_count;
      return (
        <List>
          <Row label={t('Datasets')} value={detail.dataset_count} />
          <Row label={t('Database')} value={detail.database_name} />
          {candidates.length ? (
            <Field>
              <dt>{t('Candidate tables')}</dt>
              <dd>
                <TagList items={candidates} />
              </dd>
            </Field>
          ) : null}
          <Row
            label={t('Ranked / scanned')}
            value={
              scanned
                ? omitted
                  ? t(
                      '%s of %s (%s omitted)',
                      candidates.length,
                      scanned,
                      omitted,
                    )
                  : t('%s of %s', candidates.length, scanned)
                : null
            }
          />
          {retrieval?.context_truncated ? (
            <Field>
              <dt>{t('Truncated')}</dt>
              <dd>
                <Tag color="warning">{t('schema scan truncated')}</Tag>
              </dd>
            </Field>
          ) : null}
        </List>
      );
    }
    case 'intent':
      return (
        <List>
          <Row label={t('Intent')} value={detail.intent} />
          <Row label={t('Reason')} value={detail.reason} />
        </List>
      );
    case 'wren_context':
      return (
        <>
          <List>
            <Row
              label={t('Available')}
              value={detail.available ? t('yes') : t('no')}
            />
            <Row
              label={t('Matched models')}
              value={detail.matched_models.join(', ')}
            />
            <Row
              label={t('Matched views')}
              value={(detail.matched_views ?? []).join(', ')}
            />
            <Row label={t('Retriever')} value={detail.retrieval_mode} />
            <Row
              label={t('Retrieved chunks')}
              value={detail.retrieved_item_count}
            />
            <Row label={t('Context items')} value={detail.context_item_count} />
            <Row
              label={t('Recalled examples')}
              value={detail.recalled_example_count || null}
            />
            <Row label={t('MDL path')} value={detail.mdl_path} />
          </List>
          <RetrievedChunks
            chunks={detail.retrieved_chunks}
            retriever={detail.retrieval_mode}
            matchedModels={detail.matched_models}
            matchedViews={detail.matched_views}
          />
          <WarningList
            messages={
              detail.warnings && detail.warnings.length
                ? detail.warnings
                : detail.available
                  ? null
                  : [
                      t(
                        'No semantic layer is active for this scope — answered from raw schema only.',
                      ),
                    ]
            }
          />
        </>
      );
    case 'draft':
      return (
        <>
          <List>
            <Row label={t('Type')} value={detail.response_type} />
            <Row label={t('Model')} value={detail.model} />
            <Row
              label={t('Recalled examples')}
              value={detail.recalled_example_count || null}
            />
          </List>
          <RecalledExamples examples={detail.recalled_examples} />
        </>
      );
    case 'dry_plan':
      return (
        <List>
          <Row
            label={t('Available')}
            value={detail.available ? t('yes') : t('no')}
          />
          <Row
            label={t('Diagnostics')}
            value={detail.diagnostics.join('; ') || t('none')}
          />
        </List>
      );
    case 'plan_semantic_sql':
      return (
        <List>
          <Row label={t('Engine')} value={detail.engine} />
          <Row
            label={t('Rewritten')}
            value={detail.rewritten ? t('yes') : t('no')}
          />
          <Row
            label={t('Referenced tables')}
            value={detail.referenced_tables.join(', ')}
          />
          <Row
            label={t('Warnings')}
            value={detail.warnings.join('; ') || null}
          />
          <SqlRewrite
            semanticSql={detail.semantic_sql}
            nativeSql={detail.native_sql}
          />
        </List>
      );
    case 'validate_sql':
      return (
        <List>
          <Row label={t('Dialect')} value={detail.dialect} />
          <Row
            label={t('Valid')}
            value={detail.is_valid ? t('yes') : t('no')}
          />
          <Row
            label={t('Errors')}
            value={detail.errors.join('; ') || t('none')}
          />
        </List>
      );
    case 'repair':
      return (
        <List>
          <Row label={t('Attempt')} value={detail.attempt} />
          <Row label={t('Errors')} value={detail.errors.join('; ') || null} />
          <Row
            label={t('Engine diagnostics')}
            value={detail.dry_plan_diagnostics.join('; ') || null}
          />
        </List>
      );
    case 'execute':
      return (
        <List>
          <Row label={t('Rows')} value={detail.row_count} />
          <Row label={t('Adapter')} value={detail.adapter} />
          <Row label={t('Query id')} value={detail.query_id} />
          <Row label={t('Error')} value={detail.error} />
          <Sql
            label={t('Executed SQL')}
            sql={detail.executed_sql || detail.sql}
          />
        </List>
      );
    case 'build_artifacts':
      return (
        <List>
          <Row label={t('Insight cards')} value={detail.insight_card_count} />
          <Row label={t('Chart')} value={detail.chart_type} />
          <Row
            label={t('Data preview')}
            value={detail.has_data_preview ? t('yes') : t('no')}
          />
        </List>
      );
    case 'reflect':
      return (
        <List>
          <Row label={t('Outcome')} value={detail.outcome} />
          <Row
            label={t('Remaining retries')}
            value={detail.remaining_sql_iterations}
          />
          <Row label={t('Retry feedback')} value={detail.retry_feedback} />
        </List>
      );
    default:
      return null;
  }
}

export interface AgentStepDetailProps {
  step: AgentStep;
}

export default function AgentStepDetail({ step }: AgentStepDetailProps) {
  if (!step.detail) {
    return null;
  }
  return (
    <div data-test="agent-step-detail">
      <DetailBody detail={step.detail} />
    </div>
  );
}
