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
import { t } from '@apache-superset/core/translation';
import { Tag, Tooltip } from '@superset-ui/core/components';
import { CoverageStatusInfo, getCoverageStatus, refreshCoverage } from '../api';

const POLL_MS = 4000;

const TAG_COLOR: Record<CoverageStatusInfo['status'], string> = {
  analysing: 'processing',
  stale: 'warning',
  ready: 'success',
  none: 'default',
};

const label = (info: CoverageStatusInfo): string => {
  if (info.status === 'analysing') return t('Coverage: analysing…');
  if (info.status === 'none') return t('Coverage: not run');
  const pct =
    typeof info.score === 'number' ? `${Math.round(info.score * 100)}%` : '—';
  return info.status === 'stale'
    ? t('Coverage: %s (stale)', pct)
    : t('Coverage: %s', pct);
};

export interface CoverageBadgeProps {
  projectId?: string | null;
  /** Bump to force a re-fetch (e.g. after the MDL directory changes). */
  refreshSignal?: unknown;
}

/**
 * Header badge for background directory coverage (Feature B): shows the latest
 * score, an "analysing…" state while a run is in flight, and a "stale" hint when
 * the active MDL has changed since the last completed run. Clicking re-runs.
 */
const CoverageBadge = ({ projectId, refreshSignal }: CoverageBadgeProps) => {
  const [info, setInfo] = useState<CoverageStatusInfo | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const poll = useCallback(async () => {
    if (!projectId) return;
    try {
      const next = await getCoverageStatus(projectId);
      setInfo(next);
      if (next.status === 'analysing') {
        timer.current = setTimeout(poll, POLL_MS);
      }
    } catch {
      // Best-effort badge — a transient status failure is not surfaced.
    }
  }, [projectId]);

  useEffect(() => {
    if (timer.current) clearTimeout(timer.current);
    poll();
    return () => {
      if (timer.current) clearTimeout(timer.current);
    };
  }, [poll, refreshSignal]);

  const onReRun = useCallback(async () => {
    if (!projectId) return;
    try {
      await refreshCoverage(projectId);
      poll();
    } catch {
      // ignore — the badge stays on its last known state
    }
  }, [projectId, poll]);

  if (!projectId || info === null || info.status === 'none') return null;

  return (
    <Tooltip title={t('Re-run coverage analysis')}>
      <Tag
        color={TAG_COLOR[info.status]}
        onClick={onReRun}
        style={{ cursor: 'pointer' }}
        data-test="coverage-badge"
      >
        {label(info)}
      </Tag>
    </Tooltip>
  );
};

export default CoverageBadge;
