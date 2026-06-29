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
import { useTheme } from '@apache-superset/core/theme';
import {
  Flex,
  Progress,
  Steps,
  Typography,
} from '@superset-ui/core/components';
import { Icons } from '@superset-ui/core/components/Icons';
import { CoverageProgressInfo } from '../api';

// The four coarse stages of run_directory_coverage, kept deliberately short
// (USWDS step-label guidance). The order matches the backend pipeline.
const STEP_TITLES = [
  t('Extract'),
  t('Build facts'),
  t('Judge'),
  t('Aggregate'),
];

// Maps a backend ``stage`` string to its step index when the backend has not
// supplied an explicit ``phase_index`` (forward-compatible default).
const STAGE_TO_PHASE: Record<string, number> = {
  extracting: 0,
  building_facts: 1,
  judging: 2,
  checking_overreach: 2,
  aggregating: 3,
};

export interface CoverageProgressProps {
  progress?: CoverageProgressInfo | null;
}

/**
 * Live progress for an in-flight directory coverage run (Feature C). Shows a
 * four-step stepper (Extract → Build → Judge → Aggregate) plus a determinate
 * bar when the current stage is countable (e.g. document 2 of 5). With no
 * progress payload it degrades to an indeterminate "analysing" state so it is
 * useful even before the backend emits stage detail.
 */
const CoverageProgress = ({ progress }: CoverageProgressProps) => {
  const theme = useTheme();
  const stage = progress?.stage;
  const phaseIndex =
    progress?.phase_index ??
    (stage !== undefined ? STAGE_TO_PHASE[stage] : undefined) ??
    0;
  const total = progress?.total ?? 0;
  const current = progress?.current ?? 0;
  // A determinate bar only when the stage exposes a countable denominator
  // (extraction over N documents). Judge is a single batched call — no bar.
  const showBar = total > 0;
  const percent = showBar ? Math.round((current / total) * 100) : 0;

  return (
    <Flex
      vertical
      gap={theme.sizeUnit * 3}
      data-test="coverage-progress"
      css={{ paddingTop: theme.sizeUnit }}
    >
      <Flex align="center" gap={theme.sizeUnit * 2}>
        <Icons.LoadingOutlined spin iconSize="m" />
        <Typography.Text strong>
          {progress?.detail || t('Analysing coverage…')}
        </Typography.Text>
      </Flex>
      <Steps
        size="small"
        current={phaseIndex}
        items={STEP_TITLES.map((title, index) => ({
          title,
          status: index < phaseIndex ? 'finish' : undefined,
        }))}
      />
      {showBar ? (
        <Progress
          percent={percent}
          size="small"
          data-test="coverage-progress-bar"
        />
      ) : null}
      <Typography.Text type="secondary">
        {t('You can keep editing while this runs.')}
      </Typography.Text>
    </Flex>
  );
};

export default CoverageProgress;
