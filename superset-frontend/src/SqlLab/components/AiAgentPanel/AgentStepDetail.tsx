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
import { t } from '@apache-superset/core/translation';
import { css, styled } from '@apache-superset/core/theme';
import type { AgentStep, AgentStepDetail as Detail } from './api';

const List = styled.dl`
  ${({ theme }) => css`
    display: grid;
    grid-template-columns: max-content 1fr;
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

const Row = ({ label, value }: { label: string; value: React.ReactNode }) =>
  value === null || value === undefined || value === '' ? null : (
    <div>
      <dt>{label}</dt>
      <dd>{value}</dd>
    </div>
  );

const Sql = ({ label, sql }: { label: string; sql?: string | null }) =>
  sql ? (
    <div>
      <dt>{label}</dt>
      <dd>
        <Code>{sql}</Code>
      </dd>
    </div>
  ) : null;

// Each branch renders only the fields the corresponding backend step produces
// (api.ts AgentStepDetail union). Falls through to `null` for an unknown shape,
// so a future detail kind never throws — the step still shows its summary.
function DetailBody({ detail }: { detail: Detail }) {
  switch (detail.kind) {
    case 'load_context':
      return (
        <List>
          <Row label={t('Datasets')} value={detail.dataset_count} />
          <Row label={t('Database')} value={detail.database_name} />
          <Row
            label={t('Candidate tables')}
            value={detail.retrieval?.candidate_table_names?.join(', ')}
          />
        </List>
      );
    case 'intent':
      return (
        <List>
          <Row label={t('Intent')} value={detail.intent} />
          <Row label={t('Reason')} value={detail.reason} />
        </List>
      );
    case 'wren_context':
      return (
        <List>
          <Row
            label={t('Available')}
            value={detail.available ? t('yes') : t('no')}
          />
          <Row
            label={t('Matched models')}
            value={detail.matched_models.join(', ')}
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
      );
    case 'draft':
      return (
        <List>
          <Row label={t('Type')} value={detail.response_type} />
          <Row label={t('Model')} value={detail.model} />
          <Row
            label={t('Recalled examples')}
            value={detail.recalled_example_count || null}
          />
        </List>
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
          {detail.rewritten ? (
            <>
              <Sql label={t('Semantic SQL')} sql={detail.semantic_sql} />
              <Sql label={t('Native SQL')} sql={detail.native_sql} />
            </>
          ) : null}
          <Row
            label={t('Warnings')}
            value={detail.warnings.join('; ') || null}
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
