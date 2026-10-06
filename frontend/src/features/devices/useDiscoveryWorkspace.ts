import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  DiscoveryApiError,
  v3DiscoveryApi,
  type CandidateStatus,
  type ClaimCandidateInput,
  type ClaimCandidateResponse,
  type DiscoveryCandidate,
  type DiscoveryQuery,
  type DiscoverySource,
} from '../../api/v3Discovery';

export interface DiscoveryFilters {
  search: string;
  status: 'all' | CandidateStatus;
  source: 'all' | DiscoverySource;
  conflict: 'all' | 'conflict' | 'clear';
}

const EMPTY_FILTERS: DiscoveryFilters = { search: '', status: 'all', source: 'all', conflict: 'all' };
const PAGE_SIZE = 30;

export interface DiscoveryApi {
  list: typeof v3DiscoveryApi.list;
  get: typeof v3DiscoveryApi.get;
  claim: typeof v3DiscoveryApi.claim;
  ignore: typeof v3DiscoveryApi.ignore;
  restore: typeof v3DiscoveryApi.restore;
}
interface Options { active: boolean; api?: DiscoveryApi; debounceMs?: number }
function asError(error: unknown): DiscoveryApiError {
  return error instanceof DiscoveryApiError
    ? error : new DiscoveryApiError('network', '设备发现工作区暂时不可用', { cause: error });
}
function aborted(error: unknown): boolean { return asError(error).kind === 'aborted'; }

export function useDiscoveryWorkspace({ active, api = v3DiscoveryApi, debounceMs = 250 }: Options) {
  const [filters, setFilters] = useState(EMPTY_FILTERS);
  const [debouncedSearch, setDebouncedSearch] = useState('');
  const [items, setItems] = useState<DiscoveryCandidate[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<DiscoveryApiError | null>(null);
  const [lastUpdatedAt, setLastUpdatedAt] = useState<string | null>(null);
  const [hasMore, setHasMore] = useState(false);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<DiscoveryCandidate | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<DiscoveryApiError | null>(null);
  const [actionPending, setActionPending] = useState(false);
  const listAbort = useRef<AbortController | null>(null);
  const detailAbort = useRef<AbortController | null>(null);
  const actionAbort = useRef<AbortController | null>(null);
  const sequence = useRef(0);
  const detailSequence = useRef(0);
  const mounted = useRef(true);
  const itemsRef = useRef<DiscoveryCandidate[]>([]);
  const query = useMemo<DiscoveryQuery>(() => ({
    search: debouncedSearch.trim() || undefined,
    status: filters.status === 'all' ? undefined : filters.status,
    source: filters.source === 'all' ? undefined : filters.source,
    conflict: filters.conflict === 'all' ? undefined : filters.conflict === 'conflict',
    limit: PAGE_SIZE,
    offset: 0,
  }), [debouncedSearch, filters.status, filters.source, filters.conflict]);

  const refreshList = useCallback(async (append = false) => {
    const current = ++sequence.current;
    listAbort.current?.abort();
    const controller = new AbortController();
    listAbort.current = controller;
    setLoading(true);
    setError(null);
    try {
      const result = await api.list({ ...query, offset: append ? itemsRef.current.length : 0 }, controller.signal);
      if (!mounted.current || current !== sequence.current) return;
      const nextItems = append
        ? [...itemsRef.current, ...result.items.filter((item) => !itemsRef.current.some((old) => old.candidate_id === item.candidate_id))]
        : result.items;
      itemsRef.current = nextItems;
      setItems(nextItems);
      setTotal(result.total);
      setHasMore(nextItems.length < result.total);
      setLastUpdatedAt(new Date().toISOString());
      setError(null);
      if (!append && selectedId && !result.items.some((item) => item.candidate_id === selectedId)) {
        setSelectedId(null);
        setDetail(null);
        detailAbort.current?.abort();
      }
    } catch (cause) {
      if (!mounted.current || current !== sequence.current || aborted(cause)) return;
      setError(asError(cause));
    } finally {
      if (mounted.current && current === sequence.current) setLoading(false);
    }
  }, [api, query, selectedId]);

  useEffect(() => {
    const timer = window.setTimeout(() => setDebouncedSearch(filters.search), debounceMs);
    return () => window.clearTimeout(timer);
  }, [filters.search, debounceMs]);

  useEffect(() => {
    if (active) void refreshList();
  }, [active, refreshList]);

  const patchFilters = useCallback((patch: Partial<DiscoveryFilters>) => {
    setFilters((current) => ({ ...current, ...patch }));
  }, []);
  const clearFilters = useCallback(() => setFilters(EMPTY_FILTERS), []);

  const selectCandidate = useCallback(async (candidateId: string | null) => {
    detailAbort.current?.abort();
    setSelectedId(candidateId);
    setDetailError(null);
    if (!candidateId) { setDetail(null); setDetailLoading(false); return; }
    const current = ++detailSequence.current;
    const controller = new AbortController();
    detailAbort.current = controller;
    setDetailLoading(true);
    try {
      const result = await api.get(candidateId, controller.signal);
      if (!mounted.current || current !== detailSequence.current) return;
      setDetail(result);
    } catch (cause) {
      if (!mounted.current || current !== detailSequence.current || aborted(cause)) return;
      setDetailError(asError(cause));
    } finally {
      if (mounted.current && current === detailSequence.current) setDetailLoading(false);
    }
  }, [api]);

  const claimCandidate = useCallback(async (input: ClaimCandidateInput): Promise<ClaimCandidateResponse> => {
    if (!selectedId) throw new DiscoveryApiError('bad_request', '请先选择待确认设备');
    actionAbort.current?.abort();
    const controller = new AbortController();
    actionAbort.current = controller;
    setActionPending(true);
    try {
      const result = await api.claim(selectedId, input, controller.signal);
      if (mounted.current) {
        setDetail(result.candidate);
        setItems((current) => current.map((item) => item.candidate_id === selectedId ? result.candidate : item));
        await refreshList();
      }
      return result;
    } finally { if (mounted.current) setActionPending(false); }
  }, [api, refreshList, selectedId]);

  const ignoreCandidate = useCallback(async (reason: string) => {
    if (!detail) throw new DiscoveryApiError('bad_request', '请先选择待确认设备');
    actionAbort.current?.abort();
    const controller = new AbortController();
    actionAbort.current = controller;
    setActionPending(true);
    try {
      const result = await api.ignore(detail.candidate_id, detail.candidate_version, reason, controller.signal);
      if (mounted.current) {
        setDetail(result);
        setItems((current) => current.map((item) => item.candidate_id === result.candidate_id ? result : item));
        await refreshList();
      }
      return result;
    } finally { if (mounted.current) setActionPending(false); }
  }, [api, detail, refreshList]);

  const restoreCandidate = useCallback(async () => {
    if (!detail) throw new DiscoveryApiError('bad_request', '请先选择待确认设备');
    actionAbort.current?.abort();
    const controller = new AbortController();
    actionAbort.current = controller;
    setActionPending(true);
    try {
      const result = await api.restore(detail.candidate_id, detail.candidate_version, controller.signal);
      if (mounted.current) {
        setDetail(result);
        setItems((current) => current.map((item) => item.candidate_id === result.candidate_id ? result : item));
        await refreshList();
      }
      return result;
    } finally { if (mounted.current) setActionPending(false); }
  }, [api, detail, refreshList]);

  const loadMore = useCallback(() => refreshList(true), [refreshList]);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      listAbort.current?.abort();
      detailAbort.current?.abort();
      actionAbort.current?.abort();
    };
  }, []);

  return {
    filters, patchFilters, clearFilters, items, total, hasMore, loading, error, lastUpdatedAt,
    selectedId, detail, detailLoading, detailError, actionPending,
    refreshList, loadMore, selectCandidate, claimCandidate, ignoreCandidate, restoreCandidate,
  };
}

export function discoveryErrorMessage(error: DiscoveryApiError): string {
  const message = ({
    bad_request: '查询或提交内容不符合要求，请检查后重试。',
    unauthorized: '登录已失效，请重新登录。',
    forbidden: '当前账号没有执行此操作的权限。',
    not_found: '待确认设备已不存在，请刷新列表。',
    conflict: '候选版本或身份已发生冲突，请刷新并重新核验。',
    too_large: '请求内容过大，请缩短后重试。',
    unavailable: '设备发现服务或数据库尚未准备好。',
    network: '无法连接设备发现服务，请检查网络。',
    invalid_response: '服务返回的数据格式异常。',
    http: '设备发现请求失败。',
    aborted: '请求已取消。',
  } as const)[error.kind];
  return error.requestId ? `${message}（请求编号：${error.requestId}）` : message;
}

export { EMPTY_FILTERS as EMPTY_DISCOVERY_FILTERS };
