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
  MouseEvent as ReactMouseEvent,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import { VariableSizeList, type ListChildComponentProps } from 'react-window';
import rison from 'rison';
import { SupersetClient } from '@superset-ui/core';
import { t } from '@apache-superset/core/translation';
import { css, useTheme } from '@apache-superset/core/theme';
import { Alert } from '@apache-superset/core/components';
import {
  Button,
  Checkbox,
  Dropdown,
  Empty,
  Flex,
  Input,
  Modal,
  Tag,
  Typography,
} from '@superset-ui/core/components';
import { Icons } from '@superset-ui/core/components/Icons';
import {
  createDataset,
  getDatasetWritePermission,
  listAllRegisteredTableNames,
  listPhysicalTables,
  type OnboardingSelection,
} from '../api';

// Superset's Add Dataset flow — the escape hatch for registering physical tables
// that aren't datasets yet (and therefore can't be onboarded). Opened in a new
// tab so the editor (and this picker's in-flight selection) is preserved.
const ADD_DATASET_URL = '/dataset/add/';

export interface OnboardingTablePickerProps {
  open: boolean;
  databaseId?: number;
  catalogName?: string | null;
  schema?: string | null;
  /** Whether the user may register datasets inline (proxy: project write). */
  canWrite?: boolean;
  onCancel: () => void;
  onConfirm: (selection: OnboardingSelection) => void;
}

interface TableRow {
  id: number;
  tableName: string;
}

const PAGE_SIZE = 50;
// Virtualized list geometry. The list viewport is fixed-height; rows are uniform,
// the section header is taller (it carries 1–3 helper lines).
const LIST_HEIGHT = 320;
const ROW_HEIGHT = 36;
// Prefetch the next registered page when the user scrolls within this many rows
// of the end of the loaded registered block.
const PREFETCH_ROWS = 10;

// One row in the flattened, virtualized list: registered datasets first, then a
// section header, then unregistered physical tables, then an optional loader.
type ListItem =
  | { kind: 'reg'; row: { id: number; tableName: string }; rowIndex: number }
  | { kind: 'header' }
  | { kind: 'unreg'; name: string }
  | { kind: 'loading' };

interface PickerRowData {
  listItems: ListItem[];
  theme: ReturnType<typeof useTheme>;
  allowRegister: boolean;
  registeredScanTruncated: boolean;
  unregisteredCount: number;
  isSelected: (id: number) => boolean;
  toggleAt: (index: number, shiftKey: boolean) => void;
  isNewSelected: (name: string) => boolean;
  toggleNew: (name: string) => void;
}

/**
 * A single virtualized row. Defined at module scope (stable identity) and reads
 * all dynamic values from `data` (react-window's `itemData`) so the list reuses
 * DOM nodes across re-renders rather than remounting them. `style` (absolute
 * positioning from react-window) MUST be applied to the outermost element.
 */
const PickerRow = ({ index, style, data }: ListChildComponentProps) => {
  const {
    listItems,
    theme,
    allowRegister,
    registeredScanTruncated,
    unregisteredCount,
    isSelected,
    toggleAt,
    isNewSelected,
    toggleNew,
  } = data as PickerRowData;
  const item = listItems[index];
  if (!item) return null;

  if (item.kind === 'loading') {
    return (
      <div style={style}>
        <Flex
          justify="center"
          align="center"
          css={css`
            height: 100%;
          `}
        >
          <Icons.LoadingOutlined />
        </Flex>
      </div>
    );
  }

  if (item.kind === 'header') {
    return (
      <div style={style}>
        <Flex
          vertical
          justify="center"
          css={css`
            height: 100%;
            padding: ${theme.sizeUnit}px ${theme.sizeUnit * 2}px;
            background: ${theme.colorBgLayout};
          `}
          data-test="picker-unregistered-header"
        >
          <Flex align="center" gap={theme.sizeUnit}>
            <Typography.Text type="secondary" strong>
              {t('Not registered (%s)', unregisteredCount)}
            </Typography.Text>
            <Typography.Text type="secondary">
              {allowRegister
                ? t('— check to register & onboard')
                : t('— ask an admin to register these')}
            </Typography.Text>
          </Flex>
          {allowRegister ? (
            <Typography.Text
              type="secondary"
              css={css`
                font-size: ${theme.fontSizeSM}px;
              `}
              data-test="picker-register-hint"
            >
              {t(
                'Registered with default columns and you as owner; ' +
                  'refine later in the dataset editor.',
              )}
            </Typography.Text>
          ) : null}
          {registeredScanTruncated ? (
            <Typography.Text
              type="warning"
              css={css`
                font-size: ${theme.fontSizeSM}px;
              `}
              data-test="picker-scan-truncated"
            >
              {t(
                'This schema has many datasets; some tables here may ' +
                  'already be registered.',
              )}
            </Typography.Text>
          ) : null}
        </Flex>
      </div>
    );
  }

  if (item.kind === 'unreg') {
    const { name } = item;
    return (
      <div style={style}>
        <Flex
          align="center"
          gap={theme.sizeUnit * 2}
          css={css`
            height: 100%;
            padding: 0 ${theme.sizeUnit * 2}px;
            cursor: ${allowRegister ? 'pointer' : 'not-allowed'};
            opacity: ${allowRegister ? 1 : 0.6};
            &:hover {
              background: ${allowRegister ? theme.colorBgTextHover : 'inherit'};
            }
          `}
          onClick={() => allowRegister && toggleNew(name)}
          data-test="picker-unregistered-row"
        >
          <Checkbox
            checked={isNewSelected(name)}
            disabled={!allowRegister}
            onClick={(event: ReactMouseEvent) => {
              event.stopPropagation();
              if (allowRegister) toggleNew(name);
            }}
            data-test="picker-unregistered-checkbox"
          />
          <Typography.Text>{name}</Typography.Text>
          <Tag>{t('not registered')}</Tag>
        </Flex>
      </div>
    );
  }

  // Registered dataset row.
  const { row, rowIndex } = item;
  return (
    <div style={style}>
      <Flex
        align="center"
        gap={theme.sizeUnit * 2}
        css={css`
          height: 100%;
          padding: 0 ${theme.sizeUnit * 2}px;
          cursor: pointer;
          &:hover {
            background: ${theme.colorBgTextHover};
          }
        `}
        onClick={(event: ReactMouseEvent) => toggleAt(rowIndex, event.shiftKey)}
        data-test="picker-row"
      >
        <Checkbox
          checked={isSelected(row.id)}
          onClick={(event: ReactMouseEvent) => {
            event.stopPropagation();
            toggleAt(rowIndex, event.shiftKey);
          }}
          data-test="picker-checkbox"
        />
        <Typography.Text>{row.tableName}</Typography.Text>
      </Flex>
    </div>
  );
};

/**
 * Pick which registered tables (Superset datasets) to onboard from a — possibly
 * huge — schema. Listing is server-side paginated + searchable via Superset's own
 * dataset API (same pattern as DatasetSelect), so user operations stay O(1) in
 * schema size: pages load on scroll, search re-queries the server, and "select
 * all" is expressed as a filter + exclusions rather than enumerating every id.
 */
const OnboardingTablePicker = ({
  open,
  databaseId,
  catalogName,
  schema,
  canWrite = true,
  onCancel,
  onConfirm,
}: OnboardingTablePickerProps) => {
  const theme = useTheme();
  const [rows, setRows] = useState<TableRow[]>([]);
  const [search, setSearch] = useState('');
  const [page, setPage] = useState(0);
  const [hasMore, setHasMore] = useState(true);
  const [loading, setLoading] = useState(false);
  const [totalCount, setTotalCount] = useState(0);
  const [error, setError] = useState<string | null>(null);
  // The schema's physical table names (registered or not). MDL onboards only
  // registered datasets, so names not in the authoritative registered set are
  // "unregistered" and surfaced for inline registration.
  const [physicalNames, setPhysicalNames] = useState<string[]>([]);
  const physicalCount = physicalNames.length;
  // The COMPLETE set of registered dataset names (not just loaded display pages),
  // so classification is authoritative rather than eventually-consistent (R1).
  const [registeredNamesAll, setRegisteredNamesAll] = useState<Set<string>>(
    new Set(),
  );
  const [registeredNamesLoaded, setRegisteredNamesLoaded] = useState(false);
  const [registeredScanTruncated, setRegisteredScanTruncated] = useState(false);

  // Selection model for REGISTERED datasets (keyed on dataset id). include: an
  // explicit set; all: every matching row minus an exclude set (Gmail-style) so
  // "select all" stays O(1) on a huge schema.
  const [mode, setMode] = useState<'include' | 'all'>('include');
  const [included, setIncluded] = useState<Set<number>>(new Set());
  const [excluded, setExcluded] = useState<Set<number>>(new Set());
  const lastIndexRef = useRef<number | null>(null);

  // Selection of UNREGISTERED physical tables (keyed on table name — they have no
  // dataset id yet). Always explicit: "select all" never pulls these in, so a
  // bulk action can't silently create datasets. Registered at confirm time.
  const [includedNew, setIncludedNew] = useState<Set<string>>(new Set());
  const [registering, setRegistering] = useState(false);
  const [registerProgress, setRegisterProgress] = useState<string | null>(null);
  // Kept separate from `error` (the dataset-fetch error) so the post-failure
  // refresh — which resets `error` — can't wipe the registration failure notice.
  const [registerError, setRegisterError] = useState<string | null>(null);
  // The user's REAL Dataset `can_write`, from `/api/v1/dataset/_info` (not the
  // project-write proxy). null = not yet known; treated as permitted so we never
  // block on a slow/failed lookup — the POST still enforces server-side.
  const [canRegister, setCanRegister] = useState<boolean | null>(null);
  // Inline registration requires BOTH project write (to be here at all) and the
  // real dataset write grant.
  const allowRegister = canWrite && canRegister !== false;

  const fetchPage = useCallback(
    async (nextPage: number, term: string, replace: boolean) => {
      if (typeof databaseId !== 'number' || !schema) return;
      setLoading(true);
      setError(null);
      try {
        const filters: { col: string; opr: string; value: unknown }[] = [
          { col: 'database', opr: 'rel_o_m', value: databaseId },
          { col: 'schema', opr: 'eq', value: schema },
        ];
        if (term) {
          filters.push({ col: 'table_name', opr: 'ct', value: term });
        }
        const query = rison.encode({
          filters,
          order_column: 'table_name',
          order_direction: 'asc',
          page: nextPage,
          page_size: PAGE_SIZE,
        });
        const response = await SupersetClient.get({
          endpoint: `/api/v1/dataset/?q=${query}`,
        });
        const fetched = (
          (response.json.result as { id: number; table_name: string }[]) ?? []
        ).map(item => ({ id: item.id, tableName: item.table_name }));
        const count = (response.json.count as number) ?? 0;
        setTotalCount(count);
        setRows(prev => {
          const merged = replace ? fetched : [...prev, ...fetched];
          setHasMore(merged.length < count);
          return merged;
        });
        setPage(nextPage);
      } catch (caught) {
        setError(caught instanceof Error ? caught.message : String(caught));
        setHasMore(false);
      } finally {
        setLoading(false);
      }
    },
    [databaseId, schema],
  );

  const loadPhysical = useCallback(async () => {
    if (typeof databaseId !== 'number' || !schema) return;
    try {
      const result = await listPhysicalTables(databaseId, schema, catalogName);
      setPhysicalNames(result.names);
    } catch {
      // The banner/unregistered list is advisory; a failed physical lookup must
      // not block onboarding the registered datasets we did load.
      setPhysicalNames([]);
    }
  }, [databaseId, schema, catalogName]);

  const loadRegisteredNames = useCallback(async () => {
    if (typeof databaseId !== 'number' || !schema) return;
    try {
      const { names, truncated } = await listAllRegisteredTableNames(
        databaseId,
        schema,
      );
      setRegisteredNamesAll(new Set(names));
      setRegisteredScanTruncated(truncated);
    } catch {
      // Fall back to empty: classification then leans on the loaded display
      // pages (the prior, eventually-consistent behavior) rather than breaking.
      setRegisteredNamesAll(new Set());
      setRegisteredScanTruncated(false);
    } finally {
      setRegisteredNamesLoaded(true);
    }
  }, [databaseId, schema]);

  // (Re)load page 0 whenever the picker opens or the search term changes.
  useEffect(() => {
    if (!open) return undefined;
    const handle = setTimeout(() => {
      lastIndexRef.current = null;
      fetchPage(0, search, true);
    }, 250);
    return () => clearTimeout(handle);
    // fetchPage depends on rows.length (for hasMore); intentionally not a dep here
    // so typing/opening drives reloads, not row growth.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, search, databaseId, schema]);

  // Count the schema's physical tables + read the authoritative registered-name
  // set once per open (both independent of search).
  useEffect(() => {
    if (open) {
      loadPhysical();
      loadRegisteredNames();
    }
  }, [open, loadPhysical, loadRegisteredNames]);

  // Resolve the real Dataset can_write once per open (only matters when there are
  // unregistered tables to register; cheap enough to always fetch). Permissive on
  // failure — registration is ultimately enforced by the create POST.
  useEffect(() => {
    if (!open) return;
    getDatasetWritePermission()
      .then(setCanRegister)
      .catch(() => setCanRegister(true));
  }, [open]);

  // Returning from the Add Dataset tab should reflect newly-registered tables:
  // refresh the dataset list (page 0) and the physical count on window focus.
  useEffect(() => {
    if (!open) return undefined;
    const onFocus = () => {
      lastIndexRef.current = null;
      fetchPage(0, search, true);
      loadPhysical();
      loadRegisteredNames();
    };
    window.addEventListener('focus', onFocus);
    return () => window.removeEventListener('focus', onFocus);
  }, [open, search, fetchPage, loadPhysical, loadRegisteredNames]);

  // Reset selection when the picker is (re)opened.
  useEffect(() => {
    if (open) {
      setMode('include');
      setIncluded(new Set());
      setExcluded(new Set());
      setIncludedNew(new Set());
      setRegisterProgress(null);
      setRegisterError(null);
      setCanRegister(null);
      setPhysicalNames([]);
      setRegisteredNamesAll(new Set());
      setRegisteredNamesLoaded(false);
      setRegisteredScanTruncated(false);
    }
  }, [open]);

  const isSelected = useCallback(
    (id: number) => (mode === 'all' ? !excluded.has(id) : included.has(id)),
    [mode, excluded, included],
  );

  // Registered datasets currently selected (id-based, Gmail-style for "all").
  const registeredSelectedCount = useMemo(
    () =>
      mode === 'all' ? Math.max(totalCount - excluded.size, 0) : included.size,
    [mode, totalCount, excluded, included],
  );
  // Total includes the explicitly-checked unregistered tables (registered on
  // confirm). "Select all" never touches these, so they are always explicit.
  const selectedCount = registeredSelectedCount + includedNew.size;

  // Names already known to be registered (present in a loaded dataset page).
  const registeredNames = useMemo(
    () => new Set(rows.map(row => row.tableName)),
    [rows],
  );

  // Physical tables not yet registered as datasets, filtered by the same search
  // term (client-side: the physical list is the whole schema in one call). These
  // can be registered + onboarded inline. Classified against the AUTHORITATIVE
  // registered-name set (R1), unioned with the loaded display rows as a safety
  // net if the authoritative scan failed/empty. The confirm-time dup-guard
  // remains as defense-in-depth (cap-truncation edge).
  const unregistered = useMemo(() => {
    // Only classify once the authoritative scan has resolved — before that the
    // set is empty and every physical table would falsely appear unregistered.
    if (!registeredNamesLoaded) return [];
    const term = search.trim().toLowerCase();
    return physicalNames.filter(
      name =>
        !registeredNamesAll.has(name) &&
        !registeredNames.has(name) &&
        (!term || name.toLowerCase().includes(term)),
    );
  }, [
    registeredNamesLoaded,
    registeredNamesAll,
    physicalNames,
    registeredNames,
    search,
  ]);

  // Authoritative registered count for the gap banner (whole-schema, unfiltered),
  // falling back to the display count until the scan resolves.
  const registeredTotal = registeredNamesLoaded
    ? registeredNamesAll.size
    : totalCount;

  const isNewSelected = useCallback(
    (name: string) => includedNew.has(name),
    [includedNew],
  );

  const toggleNew = useCallback((name: string) => {
    setIncludedNew(prev => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });
  }, []);

  const setSelected = useCallback(
    (id: number, selected: boolean) => {
      if (mode === 'all') {
        setExcluded(prev => {
          const next = new Set(prev);
          if (selected) next.delete(id);
          else next.add(id);
          return next;
        });
      } else {
        setIncluded(prev => {
          const next = new Set(prev);
          if (selected) next.add(id);
          else next.delete(id);
          return next;
        });
      }
    },
    [mode],
  );

  const toggleAt = useCallback(
    (index: number, shiftKey: boolean) => {
      const row = rows[index];
      if (!row) return;
      const nextSelected = !isSelected(row.id);
      if (shiftKey && lastIndexRef.current !== null) {
        // Range-select over currently loaded rows only.
        const start = Math.min(lastIndexRef.current, index);
        const end = Math.max(lastIndexRef.current, index);
        for (let i = start; i <= end; i += 1) {
          setSelected(rows[i].id, nextSelected);
        }
      } else {
        setSelected(row.id, nextSelected);
      }
      lastIndexRef.current = index;
    },
    [rows, isSelected, setSelected],
  );

  const selectAllMatching = useCallback(() => {
    setMode('all');
    setExcluded(new Set());
  }, []);

  const deselectAll = useCallback(() => {
    setMode('include');
    setIncluded(new Set());
    setExcluded(new Set());
    setIncludedNew(new Set());
  }, []);

  const listRef = useRef<VariableSizeList>(null);

  // The section header carries up to three lines (title, register hint, scan
  // truncation warning); size it accordingly so virtualization doesn't clip it.
  const headerHeight = useMemo(() => {
    let h = ROW_HEIGHT;
    if (allowRegister) h += 18;
    if (registeredScanTruncated) h += 18;
    return h;
  }, [allowRegister, registeredScanTruncated]);

  // Flattened, virtualized model: registered rows, then header, then unregistered
  // rows, then an optional loader. Registered rows occupy [0, rows.length) so a
  // list index in that range maps 1:1 to a `rows` index (shift-range still works).
  const listItems = useMemo<ListItem[]>(() => {
    const items: ListItem[] = rows.map((row, rowIndex) => ({
      kind: 'reg',
      row,
      rowIndex,
    }));
    if (unregistered.length > 0) {
      items.push({ kind: 'header' });
      unregistered.forEach(name => items.push({ kind: 'unreg', name }));
    }
    if (loading) items.push({ kind: 'loading' });
    return items;
  }, [rows, unregistered, loading]);

  const getItemSize = useCallback(
    (index: number) =>
      listItems[index]?.kind === 'header' ? headerHeight : ROW_HEIGHT,
    [listItems, headerHeight],
  );

  // VariableSizeList caches sizes by index; reset when the structure or the
  // header's height changes so rows aren't drawn at stale offsets.
  useEffect(() => {
    listRef.current?.resetAfterIndex(0);
  }, [listItems, headerHeight]);

  // Load the next registered page as the user nears the end of the loaded block
  // (replaces the old scroll-threshold handler).
  const handleItemsRendered = useCallback(
    ({ visibleStopIndex }: { visibleStopIndex: number }) => {
      if (
        !loading &&
        hasMore &&
        visibleStopIndex >= rows.length - PREFETCH_ROWS
      ) {
        fetchPage(page + 1, search, false);
      }
    },
    [loading, hasMore, rows.length, page, search, fetchPage],
  );

  const onboardRegistered = useCallback(
    (extraIds: number[] = []) => {
      if (mode === 'all') {
        // Newly-registered datasets are part of "all" server-side, so the same
        // all-minus-excludes selection naturally includes them.
        onConfirm({
          mode: 'all',
          excludeDatasetIds: [...excluded],
          search: search || null,
        });
      } else {
        onConfirm({
          mode: 'include',
          datasetIds: [...new Set([...included, ...extraIds])],
        });
      }
    },
    [mode, excluded, included, search, onConfirm],
  );

  const handleConfirm = useCallback(async () => {
    const newNames = [...includedNew];
    // Fast path: nothing to register → onboard exactly as before (synchronous).
    if (newNames.length === 0) {
      onboardRegistered();
      return;
    }
    if (typeof databaseId !== 'number' || !schema) return;

    setRegistering(true);
    setRegisterError(null);
    const loadedByName = new Map(rows.map(row => [row.tableName, row.id]));
    const createdIds: number[] = [];
    const failed: { name: string; message: string }[] = [];

    for (let i = 0; i < newNames.length; i += 1) {
      const name = newNames[i];
      setRegisterProgress(
        t('Registering %(n)s of %(total)s…', {
          n: i + 1,
          total: newNames.length,
        }),
      );
      // Dup guard: a later dataset page may already carry this table — reuse its
      // id rather than re-creating (Superset rejects duplicate table_name).
      const existingId = loadedByName.get(name);
      if (typeof existingId === 'number') {
        createdIds.push(existingId);
        // eslint-disable-next-line no-continue
        continue;
      }
      try {
        // Sequential by design: clearer progress + gentler on the dataset API.
        // eslint-disable-next-line no-await-in-loop
        const id = await createDataset({
          databaseId,
          schema,
          tableName: name,
          catalog: catalogName,
        });
        createdIds.push(id);
      } catch (caught) {
        failed.push({
          name,
          message: caught instanceof Error ? caught.message : String(caught),
        });
      }
    }

    setRegistering(false);
    setRegisterProgress(null);

    if (failed.length > 0) {
      // Surface the failures and STAY OPEN. Fold any successes into the
      // registered selection (and refresh so they move to the registered list),
      // and keep only the failed tables checked for retry.
      setRegisterError(
        t('Could not register %(tables)s', {
          tables: failed.map(f => `${f.name} (${f.message})`).join('; '),
        }),
      );
      setIncludedNew(new Set(failed.map(f => f.name)));
      if (createdIds.length > 0) {
        setMode('include');
        setIncluded(prev => {
          const next = new Set(prev);
          createdIds.forEach(id => next.add(id));
          return next;
        });
        lastIndexRef.current = null;
        fetchPage(0, search, true);
        loadPhysical();
      }
      return;
    }

    onboardRegistered(createdIds);
  }, [
    includedNew,
    onboardRegistered,
    databaseId,
    schema,
    catalogName,
    rows,
    search,
    fetchPage,
    loadPhysical,
  ]);

  const contextMenu = {
    items: [
      { key: 'all', label: t('Select all (matching)') },
      { key: 'none', label: t('Deselect all') },
    ],
    onClick: ({ key }: { key: string }) =>
      key === 'all' ? selectAllMatching() : deselectAll(),
  };

  // Stable `itemData` for the virtualized rows. Changing values flow through this
  // (react-window re-renders rows on data change) while the row component
  // identity stays fixed — so React reuses DOM nodes instead of remounting them.
  const itemData = useMemo<PickerRowData>(
    () => ({
      listItems,
      theme,
      allowRegister,
      registeredScanTruncated,
      unregisteredCount: unregistered.length,
      isSelected,
      toggleAt,
      isNewSelected,
      toggleNew,
    }),
    [
      listItems,
      theme,
      allowRegister,
      registeredScanTruncated,
      unregistered.length,
      isSelected,
      toggleAt,
      isNewSelected,
      toggleNew,
    ],
  );

  return (
    <Modal
      show={open}
      onHide={onCancel}
      title={t('Select tables to onboard')}
      footer={
        <Flex justify="space-between" align="center" style={{ width: '100%' }}>
          <Typography.Text type="secondary" data-test="picker-count">
            {registerProgress ??
              (mode === 'all'
                ? t('All %s matching selected', selectedCount)
                : t('%s selected', selectedCount))}
          </Typography.Text>
          <Flex gap={theme.sizeUnit * 2}>
            <Button
              buttonStyle="secondary"
              disabled={registering}
              onClick={onCancel}
            >
              {t('Cancel')}
            </Button>
            <Button
              buttonStyle="primary"
              disabled={selectedCount === 0 || registering}
              loading={registering}
              onClick={handleConfirm}
              data-test="picker-confirm"
            >
              {includedNew.size > 0
                ? t('Register & onboard %s table(s)', selectedCount)
                : t('Onboard %s table(s)', selectedCount)}
            </Button>
          </Flex>
        </Flex>
      }
    >
      <Flex vertical gap={theme.sizeUnit * 2}>
        <Typography.Text type="secondary" data-test="picker-subtitle">
          {t('Only tables registered as Superset datasets can be onboarded.')}
        </Typography.Text>
        <Input
          allowClear
          placeholder={t('Search tables…')}
          value={search}
          onChange={event => setSearch(event.target.value)}
          prefix={<Icons.SearchOutlined />}
          data-test="picker-search"
        />
        {registeredNamesLoaded && physicalCount > registeredTotal ? (
          <Alert
            type="info"
            showIcon
            data-test="picker-gap-banner"
            message={t(
              '%(registered)s of %(physical)s tables in %(schema)s are ' +
                'registered as datasets. Only registered tables can be onboarded.',
              {
                registered: registeredTotal,
                physical: physicalCount,
                schema,
              },
            )}
            action={
              <Typography.Link
                href={ADD_DATASET_URL}
                target="_blank"
                rel="noopener noreferrer"
                data-test="picker-register-link"
              >
                {t('Register more →')}
              </Typography.Link>
            }
          />
        ) : null}
        <Flex gap={theme.sizeUnit * 2}>
          <Button buttonSize="small" onClick={selectAllMatching}>
            {t('Select all')}
          </Button>
          <Button buttonSize="small" onClick={deselectAll}>
            {t('Clear')}
          </Button>
        </Flex>
        {error ? (
          <Typography.Text type="danger">{error}</Typography.Text>
        ) : null}
        {registerError ? (
          <Typography.Text type="danger" data-test="picker-register-error">
            {registerError}
          </Typography.Text>
        ) : null}
        <Dropdown menu={contextMenu} trigger={['contextMenu']}>
          <div
            css={css`
              height: ${LIST_HEIGHT}px;
              overflow: hidden;
              border: 1px solid ${theme.colorBorderSecondary};
              border-radius: ${theme.borderRadius}px;
            `}
            data-test="picker-list"
          >
            {listItems.length === 0 ? (
              <Empty
                image={Empty.PRESENTED_IMAGE_SIMPLE}
                description={
                  <Flex vertical gap={theme.sizeUnit} align="center">
                    <Typography.Text type="secondary">
                      {t('No registered tables found in this schema.')}
                    </Typography.Text>
                    <Typography.Link
                      href={ADD_DATASET_URL}
                      target="_blank"
                      rel="noopener noreferrer"
                      data-test="picker-register-link-empty"
                    >
                      {t('Register tables as datasets →')}
                    </Typography.Link>
                  </Flex>
                }
              />
            ) : (
              <VariableSizeList
                ref={listRef}
                height={LIST_HEIGHT}
                width="100%"
                itemCount={listItems.length}
                itemSize={getItemSize}
                itemData={itemData}
                itemKey={index => {
                  const item = listItems[index];
                  if (item?.kind === 'reg') return `reg:${item.row.id}`;
                  if (item?.kind === 'unreg') return `new:${item.name}`;
                  return item?.kind ?? `i:${index}`;
                }}
                overscanCount={8}
                onItemsRendered={handleItemsRendered}
              >
                {PickerRow}
              </VariableSizeList>
            )}
          </div>
        </Dropdown>
      </Flex>
    </Modal>
  );
};

export default OnboardingTablePicker;
