import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  mobileAccessApi,
  MobileAccessApiError,
  type MobileAccessApi,
  type MobileAccountStatus,
  type MobileScopeKind,
  type MobileScopesResponse,
  type MobileSession,
  type MobileSessionStatus,
  type MobileUser,
  type PairingStartResponse,
} from '../../api/v3MobileAccess';
import {
  v3DevicesApi,
  type DeviceListItem,
} from '../../api/v3Devices';

export interface MobileAccessFilters {
  search: string;
  accountStatus: MobileAccountStatus | 'all';
  mobileOnly: 'all' | 'true' | 'false';
}

export interface MobileAccessWorkspaceOptions {
  api?: MobileAccessApi;
  deviceApi?: Pick<typeof v3DevicesApi, 'listDevices'>;
  debounceMs?: number;
  pageSize?: number;
  now?: () => number;
}

const EMPTY_FILTERS: MobileAccessFilters = {
  search: '',
  accountStatus: 'all',
  mobileOnly: 'all',
};

function normalizedError(error: unknown): MobileAccessApiError {
  return error instanceof MobileAccessApiError
    ? error
    : new MobileAccessApiError('http', '操作失败，请稍后重试', { cause: error });
}

function aborted(error: unknown): boolean {
  return error instanceof MobileAccessApiError && error.kind === 'aborted';
}

export function useMobileAccessWorkspace(options: MobileAccessWorkspaceOptions = {}) {
  const api = options.api ?? mobileAccessApi;
  const deviceApi = options.deviceApi ?? v3DevicesApi;
  const pageSize = options.pageSize ?? 50;
  const now = options.now ?? Date.now;
  const mounted = useRef(true);
  const listAbort = useRef<AbortController | null>(null);
  const detailAbort = useRef<AbortController | null>(null);
  const deviceAbort = useRef<AbortController | null>(null);
  const mutationAbort = useRef<AbortController | null>(null);
  const sessionAbort = useRef<AbortController | null>(null);
  const listSequence = useRef(0);
  const detailSequence = useRef(0);

  const [filters, setFilters] = useState(EMPTY_FILTERS);
  const [debouncedSearch, setDebouncedSearch] = useState('');
  const [users, setUsers] = useState<MobileUser[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(0);
  const [listLoading, setListLoading] = useState(true);
  const [listError, setListError] = useState<MobileAccessApiError | null>(null);
  const [selectedUser, setSelectedUser] = useState<MobileUser | null>(null);
  const selectedId = useRef<number | null>(null);
  const [scopes, setScopes] = useState<MobileScopesResponse | null>(null);
  const [deviceDraft, setDeviceDraft] = useState<string[]>([]);
  const [areaDraft, setAreaDraft] = useState<string[]>([]);
  const [scopeConflict, setScopeConflict] = useState(false);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<MobileAccessApiError | null>(null);
  const [devices, setDevices] = useState<DeviceListItem[]>([]);
  const [sessions, setSessions] = useState<MobileSession[]>([]);
  const [sessionStatus, setSessionStatus] = useState<MobileSessionStatus | 'all'>('all');
  const [sessionsLoading, setSessionsLoading] = useState(false);
  const [sessionsError, setSessionsError] = useState<MobileAccessApiError | null>(null);
  const [sessionPage, setSessionPage] = useState(0);
  const [sessionTotal, setSessionTotal] = useState(0);
  const [sessionHasMore, setSessionHasMore] = useState(false);
  const [pairing, setPairing] = useState<PairingStartResponse | null>(null);
  const [pairingSeconds, setPairingSeconds] = useState(0);
  const [mutationPending, setMutationPending] = useState(false);

  useEffect(() => {
    const timer = window.setTimeout(
      () => setDebouncedSearch(filters.search.trim()),
      options.debounceMs ?? 300,
    );
    return () => window.clearTimeout(timer);
  }, [filters.search, options.debounceMs]);

  const listQuery = useMemo(() => ({
    search: debouncedSearch || undefined,
    account_status: filters.accountStatus === 'all' ? undefined : filters.accountStatus,
    mobile_only: filters.mobileOnly === 'all' ? undefined : filters.mobileOnly === 'true',
    limit: pageSize,
    offset: page * pageSize,
  }), [debouncedSearch, filters.accountStatus, filters.mobileOnly, page, pageSize]);

  const refreshUsers = useCallback(async () => {
    const sequence = ++listSequence.current;
    listAbort.current?.abort();
    const controller = new AbortController();
    listAbort.current = controller;
    setListLoading(true);
    setListError(null);
    try {
      const result = await api.listUsers(listQuery, controller.signal);
      if (!mounted.current || sequence !== listSequence.current) return;
      setUsers(result.items);
      setTotal(result.total);
      if (selectedId.current) {
        const current = result.items.find((item) => item.user_id === selectedId.current);
        if (current) setSelectedUser(current);
      }
    } catch (error) {
      if (mounted.current && sequence === listSequence.current && !aborted(error)) {
        setListError(normalizedError(error));
      }
    } finally {
      if (mounted.current && sequence === listSequence.current) setListLoading(false);
    }
  }, [api, listQuery]);

  useEffect(() => { void refreshUsers(); }, [refreshUsers]);

  const loadDevices = useCallback(async () => {
    const controller = new AbortController();
    deviceAbort.current?.abort();
    deviceAbort.current = controller;
    const all: DeviceListItem[] = [];
    let offset = 0;
    while (true) {
      const result = await deviceApi.listDevices(
        { limit: 100, offset },
        controller.signal,
      );
      all.push(...result.items);
      offset += result.items.length;
      if (offset >= result.total || result.items.length === 0) break;
    }
    if (mounted.current) setDevices(all);
  }, [deviceApi]);

  const clearPairing = useCallback(() => {
    setPairing(null);
    setPairingSeconds(0);
  }, []);

  const loadSelected = useCallback(async (userId: number) => {
    const sequence = ++detailSequence.current;
    detailAbort.current?.abort();
    const controller = new AbortController();
    detailAbort.current = controller;
    setDetailLoading(true);
    setDetailError(null);
    setScopeConflict(false);
    try {
      const [user, scopeResult] = await Promise.all([
        api.getUser(userId, controller.signal),
        api.getScopes(userId, controller.signal),
      ]);
      if (!mounted.current || sequence !== detailSequence.current) return;
      setSelectedUser(user);
      setScopes(scopeResult);
      setDeviceDraft(
        scopeResult.scopes.filter((item) => item.scope_kind === 'device')
          .map((item) => item.scope_value),
      );
      setAreaDraft(
        scopeResult.scopes.filter((item) => item.scope_kind === 'area')
          .map((item) => item.scope_value),
      );
    } catch (error) {
      if (mounted.current && sequence === detailSequence.current && !aborted(error)) {
        setDetailError(normalizedError(error));
      }
    } finally {
      if (mounted.current && sequence === detailSequence.current) setDetailLoading(false);
    }
  }, [api]);

  const selectUser = useCallback((user: MobileUser | null) => {
    clearPairing();
    detailAbort.current?.abort();
    selectedId.current = user?.user_id ?? null;
    setSelectedUser(user);
    setScopes(null);
    setDeviceDraft([]);
    setAreaDraft([]);
    setScopeConflict(false);
    setDetailError(null);
    if (user) void loadSelected(user.user_id);
  }, [clearPairing, loadSelected]);

  const refreshSessions = useCallback(async () => {
    sessionAbort.current?.abort();
    const controller = new AbortController();
    sessionAbort.current = controller;
    setSessionsLoading(true);
    setSessionsError(null);
    try {
      const result = await api.listSessions({
        user_id: selectedId.current ?? undefined,
        status: sessionStatus === 'all' ? undefined : sessionStatus,
        limit: 25,
        offset: sessionPage * 25,
      }, controller.signal);
      if (mounted.current && sessionAbort.current === controller) {
        setSessions(result.sessions);
        setSessionTotal(result.pagination.total);
        setSessionHasMore(result.pagination.has_more);
      }
    } catch (error) {
      if (mounted.current && !aborted(error)) setSessionsError(normalizedError(error));
    } finally {
      if (mounted.current && sessionAbort.current === controller) setSessionsLoading(false);
    }
  }, [api, sessionPage, sessionStatus]);

  useEffect(() => { void refreshSessions(); }, [refreshSessions, selectedUser?.user_id]);
  useEffect(() => { setSessionPage(0); }, [selectedUser?.user_id, sessionStatus]);
  useEffect(() => { void loadDevices().catch((error) => {
    if (mounted.current && !aborted(error)) setDetailError(normalizedError(error));
  }); }, [loadDevices]);

  const runMutation = useCallback(async <T,>(
    operation: (signal: AbortSignal) => Promise<T>,
  ): Promise<T> => {
    mutationAbort.current?.abort();
    const controller = new AbortController();
    mutationAbort.current = controller;
    setMutationPending(true);
    try {
      return await operation(controller.signal);
    } finally {
      if (mounted.current) setMutationPending(false);
    }
  }, []);

  const createUser = useCallback(async (username: string, displayName: string) => {
    const user = await runMutation(
      (signal) => api.createUser(
        { username, display_name: displayName },
        signal,
      ),
    );
    await refreshUsers();
    selectUser(user);
    return user;
  }, [api, refreshUsers, runMutation, selectUser]);

  const updateUser = useCallback(async (
    input: { display_name?: string; account_status?: MobileAccountStatus; disabled_reason?: string },
  ) => {
    if (!selectedUser) throw new MobileAccessApiError('bad_request', '请先选择用户');
    const user = await runMutation((signal) => api.updateUser(
      selectedUser.user_id,
      { expected_profile_version: selectedUser.profile_version, ...input },
      signal,
    ));
    setSelectedUser(user);
    await refreshUsers();
    await refreshSessions();
    return user;
  }, [api, refreshSessions, refreshUsers, runMutation, selectedUser]);

  const saveScopes = useCallback(async () => {
    if (!selectedUser || !scopes) throw new MobileAccessApiError('bad_request', '范围尚未加载');
    setScopeConflict(false);
    try {
      const result = await runMutation((signal) => api.replaceScopes(
        selectedUser.user_id,
        {
          expected_scope_version: scopes.scope_version,
          scopes: [
            ...deviceDraft.map((scope_value) => ({ scope_kind: 'device' as const, scope_value })),
            ...areaDraft.map((scope_value) => ({ scope_kind: 'area' as const, scope_value })),
          ],
        },
        signal,
      ));
      setScopes(result);
      await loadSelected(selectedUser.user_id);
      await refreshUsers();
      return result;
    } catch (error) {
      if (
        error instanceof MobileAccessApiError
        && error.code === 'mobile_scope_version_conflict'
      ) setScopeConflict(true);
      throw error;
    }
  }, [
    api, areaDraft, deviceDraft, loadSelected, refreshUsers,
    runMutation, scopes, selectedUser,
  ]);

  const startPairing = useCallback(async () => {
    if (!selectedUser) throw new MobileAccessApiError('bad_request', '请先选择用户');
    const result = await runMutation(
      (signal) => api.startPairing(selectedUser.user_id, signal),
    );
    setPairing(result);
    setPairingSeconds(Math.max(0, Math.ceil(
      (new Date(result.expires_at).getTime() - now()) / 1000,
    )));
    await refreshUsers();
    return result;
  }, [api, now, refreshUsers, runMutation, selectedUser]);

  useEffect(() => {
    if (!pairing) return undefined;
    const timer = window.setInterval(() => {
      const remaining = Math.max(
        0,
        Math.ceil((new Date(pairing.expires_at).getTime() - now()) / 1000),
      );
      setPairingSeconds(remaining);
      if (remaining <= 0) clearPairing();
    }, 1000);
    return () => window.clearInterval(timer);
  }, [clearPairing, now, pairing]);

  const revokeSession = useCallback(async (sessionId: string) => {
    const result = await runMutation(
      (signal) => api.revokeSession(sessionId, 'admin_revoked', signal),
    );
    await refreshSessions();
    await refreshUsers();
    return result;
  }, [api, refreshSessions, refreshUsers, runMutation]);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      listSequence.current += 1;
      detailSequence.current += 1;
      listAbort.current?.abort();
      detailAbort.current?.abort();
      deviceAbort.current?.abort();
      mutationAbort.current?.abort();
      sessionAbort.current?.abort();
    };
  }, []);

  const areas = useMemo(() => Array.from(new Set(
    devices.map((device) => device.area_id).filter((value): value is string => Boolean(value)),
  )).sort((a, b) => a.localeCompare(b)), [devices]);

  return {
    filters,
    setFilters: (patch: Partial<MobileAccessFilters>) => {
      setPage(0);
      setFilters((current) => ({ ...current, ...patch }));
    },
    clearFilters: () => {
      setPage(0);
      setFilters(EMPTY_FILTERS);
    },
    users,
    total,
    page,
    pageSize,
    setPage,
    listLoading,
    listError,
    refreshUsers,
    selectedUser,
    selectUser,
    scopes,
    deviceDraft,
    setDeviceDraft,
    areaDraft,
    setAreaDraft,
    scopeConflict,
    loadSelected,
    detailLoading,
    detailError,
    devices,
    areas,
    sessions,
    sessionStatus,
    setSessionStatus,
    sessionsLoading,
    sessionsError,
    sessionPage,
    setSessionPage,
    sessionTotal,
    sessionHasMore,
    refreshSessions,
    pairing,
    pairingSeconds,
    clearPairing,
    mutationPending,
    createUser,
    updateUser,
    saveScopes,
    startPairing,
    revokeSession,
  };
}

export type MobileAccessWorkspace = ReturnType<typeof useMobileAccessWorkspace>;
export type { MobileScopeKind };
