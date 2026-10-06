import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  DeviceApiError,
  v3DevicesApi,
  type ConnectionStatus,
  type CreateDeviceInput,
  type DeviceDetail,
  type DeviceListItem,
  type DevicesApi,
  type DeviceImportance,
  type OperationMode,
  type UpdateDeviceInput,
} from '../../api/v3Devices';

export interface DeviceFilters {
  search: string;
  connectionStatus: 'all' | ConnectionStatus;
  operationMode: 'all' | OperationMode;
  areaId: string;
  lifecycle: 'all' | 'active' | 'retired';
}

export const EMPTY_DEVICE_FILTERS: DeviceFilters = {
  search: '',
  connectionStatus: 'all',
  operationMode: 'all',
  areaId: '',
  lifecycle: 'all',
};

const systemNow = () => new Date();

interface WorkspaceOptions {
  api?: DevicesApi;
  initialDeviceId?: string | null;
  onSelectionChange?: (deviceId: string | null) => void;
  onInvalidSelection?: (deviceId: string) => void;
  debounceMs?: number;
  pageSize?: number;
  now?: () => Date;
  focusTarget?: Pick<Window, 'addEventListener' | 'removeEventListener'> | null;
}

function normalizedError(error: unknown): DeviceApiError {
  if (error instanceof DeviceApiError) return error;
  return new DeviceApiError('network', '设备工作区发生未知错误', { cause: error });
}

function isAborted(error: unknown): boolean {
  return error instanceof DeviceApiError && error.kind === 'aborted';
}

export function detailToListItem(device: DeviceDetail): DeviceListItem {
  return {
    device_id: device.device_id,
    display_name: device.display_name,
    device_type: device.device_type,
    area_id: device.area_id,
    importance: device.importance,
    profile_source: device.profile_source,
    profile_version: device.profile_version,
    operation_mode: device.operation_mode,
    retired_at: device.retired_at,
    retirement_reason: device.retirement_reason,
    connection_status: device.connection_status,
    ip_address: device.ip_address,
    state_version: device.state_version,
    last_received_at: device.received_at,
    lifecycle_status: device.lifecycle_status,
  };
}

export function useDeviceWorkspace(options: WorkspaceOptions = {}) {
  const api = options.api ?? v3DevicesApi;
  const pageSize = options.pageSize ?? 24;
  const now = options.now ?? systemNow;
  const focusTarget = options.focusTarget === undefined
    ? (typeof window === 'undefined' ? null : window)
    : options.focusTarget;
  const [filters, setFilters] = useState<DeviceFilters>(EMPTY_DEVICE_FILTERS);
  const [debouncedSearch, setDebouncedSearch] = useState('');
  const [items, setItems] = useState<DeviceListItem[]>([]);
  const itemsRef = useRef<DeviceListItem[]>([]);
  const [total, setTotal] = useState(0);
  const [listLoading, setListLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [listError, setListError] = useState<DeviceApiError | null>(null);
  const [lastUpdatedAt, setLastUpdatedAt] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(options.initialDeviceId ?? null);
  const selectedIdRef = useRef<string | null>(options.initialDeviceId ?? null);
  const [detail, setDetail] = useState<DeviceDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<DeviceApiError | null>(null);
  const [selectionNotice, setSelectionNotice] = useState<string | null>(null);
  const [mutationPending, setMutationPending] = useState(false);
  const mutationPendingRef = useRef(false);
  const listAbort = useRef<AbortController | null>(null);
  const detailAbort = useRef<AbortController | null>(null);
  const mutationAbort = useRef<AbortController | null>(null);
  const listSequence = useRef(0);
  const detailSequence = useRef(0);
  const mounted = useRef(true);
  const attemptedInitialSelection = useRef<string | null>(null);

  useEffect(() => {
    const timer = window.setTimeout(
      () => setDebouncedSearch(filters.search.trim()),
      options.debounceMs ?? 300,
    );
    return () => window.clearTimeout(timer);
  }, [filters.search, options.debounceMs]);

  const query = useMemo(() => ({
    search: debouncedSearch || undefined,
    connection_status: filters.connectionStatus === 'all' ? undefined : filters.connectionStatus,
    operation_mode: filters.operationMode === 'all' ? undefined : filters.operationMode,
    area_id: filters.areaId.trim() || undefined,
    retired: filters.lifecycle === 'all' ? undefined : filters.lifecycle === 'retired',
  }), [
    debouncedSearch,
    filters.areaId,
    filters.connectionStatus,
    filters.lifecycle,
    filters.operationMode,
  ]);

  const runList = useCallback(async (append: boolean) => {
    const requestSequence = ++listSequence.current;
    listAbort.current?.abort();
    const controller = new AbortController();
    listAbort.current = controller;
    if (append) setLoadingMore(true);
    else setListLoading(true);
    setListError(null);
    try {
      const result = await api.listDevices(
        { ...query, limit: pageSize, offset: append ? itemsRef.current.length : 0 },
        controller.signal,
      );
      if (!mounted.current || requestSequence !== listSequence.current) return;
      const next = append
        ? [...itemsRef.current, ...result.items.filter(
          (candidate) => !itemsRef.current.some((item) => item.device_id === candidate.device_id),
        )]
        : result.items;
      itemsRef.current = next;
      setItems(next);
      setTotal(result.total);
      setLastUpdatedAt(now().toISOString());
    } catch (error) {
      if (!mounted.current || requestSequence !== listSequence.current || isAborted(error)) return;
      setListError(normalizedError(error));
    } finally {
      if (mounted.current && requestSequence === listSequence.current) {
        setListLoading(false);
        setLoadingMore(false);
      }
    }
  }, [api, now, pageSize, query]);

  useEffect(() => {
    void runList(false);
  }, [runList]);

  const selectDevice = useCallback(async (deviceId: string | null) => {
    selectedIdRef.current = deviceId;
    attemptedInitialSelection.current = deviceId;
    setSelectedId(deviceId);
    options.onSelectionChange?.(deviceId);
    setSelectionNotice(null);
    detailAbort.current?.abort();
    const requestSequence = ++detailSequence.current;
    if (!deviceId) {
      setDetail(null);
      setDetailError(null);
      setDetailLoading(false);
      return;
    }
    const controller = new AbortController();
    detailAbort.current = controller;
    setDetailLoading(true);
    setDetailError(null);
    try {
      const result = await api.getDevice(deviceId, controller.signal);
      if (!mounted.current || requestSequence !== detailSequence.current) return;
      setDetail(result);
      setItems((current) => {
        const next = current.map((item) => item.device_id === result.device_id
          ? detailToListItem(result)
          : item);
        itemsRef.current = next;
        return next;
      });
    } catch (error) {
      if (!mounted.current || requestSequence !== detailSequence.current || isAborted(error)) return;
      const apiError = normalizedError(error);
      if (apiError.kind === 'not_found') {
        setSelectionNotice(`设备 ${deviceId} 不存在或已被删除，已返回设备列表。`);
        setSelectedId(null);
        selectedIdRef.current = null;
        setDetail(null);
        options.onInvalidSelection?.(deviceId);
      } else {
        setDetailError(apiError);
      }
    } finally {
      if (mounted.current && requestSequence === detailSequence.current) setDetailLoading(false);
    }
  }, [api, options.onInvalidSelection, options.onSelectionChange]);

  useEffect(() => {
    if (!options.initialDeviceId) {
      attemptedInitialSelection.current = null;
      return;
    }
    if (attemptedInitialSelection.current === options.initialDeviceId) return;
    attemptedInitialSelection.current = options.initialDeviceId;
    void selectDevice(options.initialDeviceId);
  }, [options.initialDeviceId, selectDevice]);

  const refreshDetail = useCallback(async () => {
    if (selectedIdRef.current) await selectDevice(selectedIdRef.current);
  }, [selectDevice]);

  const acceptServerDevice = useCallback((device: DeviceDetail) => {
    selectedIdRef.current = device.device_id;
    attemptedInitialSelection.current = device.device_id;
    setSelectedId(device.device_id);
    setDetail(device);
    setDetailError(null);
    setSelectionNotice(null);
    options.onSelectionChange?.(device.device_id);
    setItems((current) => {
      const projected = detailToListItem(device);
      const exists = current.some((item) => item.device_id === device.device_id);
      const next = exists
        ? current.map((item) => item.device_id === device.device_id ? projected : item)
        : [projected, ...current];
      itemsRef.current = next;
      return next;
    });
  }, [options.onSelectionChange]);

  const runMutation = useCallback(async <T,>(operation: (signal: AbortSignal) => Promise<T>): Promise<T> => {
    if (mutationPendingRef.current) {
      throw new DeviceApiError('conflict', '已有设备操作正在提交，请等待完成');
    }
    mutationPendingRef.current = true;
    mutationAbort.current?.abort();
    const controller = new AbortController();
    mutationAbort.current = controller;
    setMutationPending(true);
    try {
      return await operation(controller.signal);
    } finally {
      mutationPendingRef.current = false;
      if (mounted.current) setMutationPending(false);
    }
  }, []);

  const createDevice = useCallback(async (input: CreateDeviceInput) => {
    const result = await runMutation((signal) => api.createDevice(input, signal));
    acceptServerDevice(result);
    void runList(false);
    return result;
  }, [acceptServerDevice, api, runList, runMutation]);

  const updateDevice = useCallback(async (deviceId: string, input: UpdateDeviceInput) => {
    const result = await runMutation((signal) => api.updateDevice(deviceId, input, signal));
    acceptServerDevice(result);
    void runList(false);
    return result;
  }, [acceptServerDevice, api, runList, runMutation]);

  const setOperationMode = useCallback(async (
    deviceId: string,
    mode: OperationMode,
    expectedVersion: number,
  ) => {
    const result = await runMutation(
      (signal) => api.setOperationMode(deviceId, mode, expectedVersion, signal),
    );
    acceptServerDevice(result);
    void runList(false);
    return result;
  }, [acceptServerDevice, api, runList, runMutation]);

  const retireDevice = useCallback(async (
    deviceId: string,
    reason: string,
    expectedVersion: number,
  ) => {
    const result = await runMutation(
      (signal) => api.retireDevice(deviceId, reason, expectedVersion, signal),
    );
    acceptServerDevice(result);
    void runList(false);
    return result;
  }, [acceptServerDevice, api, runList, runMutation]);

  const restoreDevice = useCallback(async (deviceId: string, expectedVersion: number) => {
    const result = await runMutation(
      (signal) => api.restoreDevice(deviceId, expectedVersion, signal),
    );
    acceptServerDevice(result);
    void runList(false);
    return result;
  }, [acceptServerDevice, api, runList, runMutation]);

  const deleteDevice = useCallback(async (deviceId: string, confirmation: string) => {
    const result = await runMutation(
      (signal) => api.deleteDevice(deviceId, confirmation, signal),
    );
    selectedIdRef.current = null;
    attemptedInitialSelection.current = null;
    setSelectedId(null);
    setDetail(null);
    setDetailError(null);
    options.onSelectionChange?.(null);
    await runList(false);
    return result;
  }, [api, options.onSelectionChange, runList, runMutation]);

  useEffect(() => {
    if (!focusTarget) return undefined;
    const handleFocus = () => {
      void runList(false);
      if (selectedIdRef.current) void selectDevice(selectedIdRef.current);
    };
    focusTarget.addEventListener('focus', handleFocus);
    return () => focusTarget.removeEventListener('focus', handleFocus);
  }, [focusTarget, runList, selectDevice]);

  useEffect(() => {
    // React StrictMode intentionally runs one setup/cleanup rehearsal. Restore
    // the mounted marker during the second setup so its real responses can land.
    mounted.current = true;
    return () => {
      mounted.current = false;
      listSequence.current += 1;
      detailSequence.current += 1;
      attemptedInitialSelection.current = null;
      listAbort.current?.abort();
      detailAbort.current?.abort();
      mutationAbort.current?.abort();
    };
  }, []);

  const patchFilters = useCallback((patch: Partial<DeviceFilters>) => {
    setFilters((current) => ({ ...current, ...patch }));
  }, []);

  const clearFilters = useCallback(() => setFilters(EMPTY_DEVICE_FILTERS), []);

  return {
    filters,
    patchFilters,
    clearFilters,
    items,
    total,
    listLoading,
    loadingMore,
    listError,
    lastUpdatedAt,
    hasMore: items.length < total,
    loadMore: () => runList(true),
    refreshList: () => runList(false),
    selectedId,
    detail,
    detailLoading,
    detailError,
    selectionNotice,
    selectDevice,
    refreshDetail,
    mutationPending,
    createDevice,
    updateDevice,
    setOperationMode,
    retireDevice,
    restoreDevice,
    deleteDevice,
  };
}

export type DeviceWorkspace = ReturnType<typeof useDeviceWorkspace>;
export type { DeviceImportance };
