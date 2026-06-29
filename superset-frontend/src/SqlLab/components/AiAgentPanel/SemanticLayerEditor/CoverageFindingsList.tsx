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
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactElement,
} from 'react';
import { VariableSizeList, type ListChildComponentProps } from 'react-window';
import { t } from '@apache-superset/core/translation';
import { useTheme } from '@apache-superset/core/theme';
import { Flex, Tag, Typography } from '@superset-ui/core/components';
import { Icons } from '@superset-ui/core/components/Icons';
import { CoverageFinding, CoverageStatus } from '../api';

const STATUS_COLOR: Record<CoverageStatus, string> = {
  covered: 'success',
  partial: 'warning',
  missing: 'error',
};

// Per-row height assumed before a row has been measured. Generous on purpose so
// the very first (pre-measurement) paint does not clip the last visible row.
const ESTIMATED_ROW_HEIGHT = 112;
// Vertical chrome reserved outside the list: modal title bar, footer, and the
// pinned coverage summary. The list height is capped to the remaining viewport
// so the dialog never grows past the screen — excess rows scroll inside the
// list itself (react-window) rather than pushing the modal taller.
const RESERVED_VERTICAL_PX = 360;
const MIN_LIST_HEIGHT = 160;

/** A single coverage claim card. Extracted so it can be measured + virtualized. */
export const CoverageFindingCard = ({
  finding,
}: {
  finding: CoverageFinding;
}) => {
  const theme = useTheme();
  return (
    <Flex
      vertical
      gap={theme.sizeUnit}
      css={{
        border: `1px solid ${theme.colorBorderSecondary}`,
        borderRadius: theme.borderRadius,
        padding: theme.sizeUnit * 2,
      }}
      data-test="coverage-finding"
    >
      <Flex align="center" gap={theme.sizeUnit} wrap="wrap">
        <Tag color={STATUS_COLOR[finding.status]}>{finding.status}</Tag>
        <Tag>{finding.claim.kind}</Tag>
        <Typography.Text strong>{finding.claim.subject}</Typography.Text>
        {finding.document_filename ? (
          <Tag
            icon={<Icons.FileTextOutlined />}
            data-test="coverage-finding-source"
          >
            {finding.document_filename}
          </Tag>
        ) : null}
      </Flex>
      <Typography.Text>{finding.claim.statement}</Typography.Text>
      {finding.matched ? (
        <Typography.Text type="secondary">
          {t('Matched: %s', finding.matched)}
        </Typography.Text>
      ) : null}
      {finding.suggestion ? (
        <Typography.Text type="warning">
          {t('Fix: %s', finding.suggestion)}
        </Typography.Text>
      ) : null}
    </Flex>
  );
};

interface RowData {
  findings: CoverageFinding[];
  /** Gap baked into each row's measured height (react-window absolutely
   *  positions rows, so CSS margins between cards do not apply). */
  gap: number;
  setSize: (index: number, size: number) => void;
}

const FindingRow = ({
  index,
  style,
  data,
}: ListChildComponentProps<RowData>) => {
  const { findings, gap, setSize } = data;
  const ref = useRef<HTMLDivElement>(null);

  // Measure the rendered card (heights vary with statement length, the optional
  // matched/suggestion lines, and text wrapping) and feed it back to the list.
  useEffect(() => {
    const node = ref.current;
    if (!node) return undefined;
    const measure = () => {
      const height = node.getBoundingClientRect().height;
      if (height > 0) setSize(index, height + gap);
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(node);
    return () => observer.disconnect();
  }, [index, gap, setSize, findings]);

  return (
    <div style={style}>
      <div ref={ref} css={{ paddingBottom: gap }}>
        <CoverageFindingCard finding={findings[index]} />
      </div>
    </div>
  );
};

/**
 * Virtualized list of coverage claims. Coverage reports can carry hundreds of
 * findings; rendering them all would bloat the DOM, so only the visible rows are
 * mounted. The list height is bounded by the viewport so the surrounding dialog
 * stays on screen and the claims scroll internally.
 */
export const CoverageFindingsList = ({
  findings,
}: {
  findings: CoverageFinding[];
}): ReactElement => {
  const theme = useTheme();
  const gap = theme.sizeUnit * 2;
  const listRef = useRef<VariableSizeList>(null);
  const sizeMap = useRef<Map<number, number>>(new Map());
  // Bumped on each new measurement to recompute the list height + re-render.
  const [, setVersion] = useState(0);

  const setSize = useCallback((index: number, size: number) => {
    if (sizeMap.current.get(index) === size) return;
    sizeMap.current.set(index, size);
    listRef.current?.resetAfterIndex(index);
    setVersion(version => version + 1);
  }, []);

  const getItemSize = useCallback(
    (index: number) => sizeMap.current.get(index) ?? ESTIMATED_ROW_HEIGHT + gap,
    [gap],
  );

  // Drop cached measurements when the set of findings changes (e.g. a re-run).
  useEffect(() => {
    sizeMap.current.clear();
    listRef.current?.resetAfterIndex(0);
    setVersion(version => version + 1);
  }, [findings]);

  // Track viewport height so the cap follows window resizes.
  const [viewport, setViewport] = useState(() =>
    typeof window === 'undefined' ? 768 : window.innerHeight,
  );
  useEffect(() => {
    const onResize = () => setViewport(window.innerHeight);
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, []);

  const itemData = useMemo<RowData>(
    () => ({ findings, gap, setSize }),
    [findings, gap, setSize],
  );

  // Grow to fit the content, but never past the viewport budget — past that the
  // list scrolls internally and the dialog stays bounded.
  const total = findings.reduce(
    (sum, _finding, index) => sum + getItemSize(index),
    0,
  );
  const available = Math.max(MIN_LIST_HEIGHT, viewport - RESERVED_VERTICAL_PX);
  const height = Math.min(total, available);

  return (
    <div data-test="coverage-findings-list">
      <VariableSizeList
        ref={listRef}
        height={height}
        width="100%"
        itemCount={findings.length}
        itemSize={getItemSize}
        itemData={itemData}
        itemKey={index => `finding-${index}`}
        overscanCount={4}
      >
        {FindingRow}
      </VariableSizeList>
    </div>
  );
};

export default CoverageFindingsList;
