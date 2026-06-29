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
import { Tag, Tooltip } from '@superset-ui/core/components';
import { CoverageStatusInfo, getCoverageStatus, refreshCoverage } from '../api';
import { COVERAGE_EVENT_TYPES, useProjectEvents } from './useProjectEvents';
import CoveragePanel from './CoveragePanel';

// Low-frequency safety net: SSE drives live updates, this only covers missed
// events / SSE-buffering proxies. Far cheaper than the old 4s analysing-poll.
const FALLBACK_POLL_MS = 30000;

const TAG_COLOR: Record<CoverageStatusInfo['status'], string> = {
  analysing: 'processing',
  stale: 'warning',
  ready: 'success',
  none: 'default',
};

const label = (info: CoverageStatusInfo): string => {
  if (info.status === 'analysing') {
    // Mirror the live stage detail in the badge when the run is reporting it.
    const detail = info.progress?.detail;
    return detail
      ? t('Coverage: analysing — %s', detail)
      : t('Coverage: analysing…');
  }
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
 * the active MDL has changed since the last completed run. Clicking opens the
 * coverage viewer (progress or stored report); it never re-runs analysis —
 * re-run is an explicit action inside the viewer.
 */
const CoverageBadge = ({ projectId, refreshSignal }: CoverageBadgeProps) => {
  const [info, setInfo] = useState<CoverageStatusInfo | null>(null);
  // Distinguishes "first status not fetched yet" (show a placeholder) from
  // "fetched, genuinely nothing to show" (render nothing) — so the badge does
  // not pop in from blank.
  const [loaded, setLoaded] = useState(false);
  const [panelOpen, setPanelOpen] = useState(false);

  const poll = useCallback(async () => {
    if (!projectId) return;
    try {
      setInfo(await getCoverageStatus(projectId));
    } catch {
      // Best-effort badge — a transient status failure is not surfaced.
    } finally {
      setLoaded(true);
    }
  }, [projectId]);

  // Reset to the loading placeholder when switching projects so a stale badge
  // from the previous project never shows under the new one.
  useEffect(() => {
    setLoaded(false);
    setInfo(null);
    setPanelOpen(false);
  }, [projectId]);

  // Live updates: refetch when the project emits a coverage/active-set event.
  useProjectEvents(projectId, COVERAGE_EVENT_TYPES, poll, Boolean(projectId));

  // Initial load + on an explicit refresh signal (e.g. MDL directory changed).
  useEffect(() => {
    poll();
  }, [poll, refreshSignal]);

  // Safety-net poll for missed SSE events / buffering proxies.
  useEffect(() => {
    if (!projectId) return undefined;
    const id = setInterval(poll, FALLBACK_POLL_MS);
    return () => clearInterval(id);
  }, [projectId, poll]);

  const onRerun = useCallback(async () => {
    if (!projectId) return;
    try {
      await refreshCoverage(projectId);
      poll();
    } catch {
      // ignore — the viewer stays on its last known state
    }
  }, [projectId, poll]);

  if (!projectId) return null;
  // Initial fetch in flight: a subtle placeholder instead of a blank that the
  // real badge later pops into.
  if (!loaded) {
    return (
      <Tag color="processing" data-test="coverage-loading">
        {t('Coverage: …')}
      </Tag>
    );
  }
  if (info === null || info.status === 'none') return null;

  return (
    <>
      <Tooltip title={t('View coverage')}>
        <Tag
          color={TAG_COLOR[info.status]}
          onClick={() => setPanelOpen(true)}
          style={{ cursor: 'pointer' }}
          data-test="coverage-badge"
        >
          {label(info)}
        </Tag>
      </Tooltip>
      <CoveragePanel
        projectId={projectId}
        info={info}
        open={panelOpen}
        onClose={() => setPanelOpen(false)}
        onRerun={onRerun}
      />
    </>
  );
};

export default CoverageBadge;
