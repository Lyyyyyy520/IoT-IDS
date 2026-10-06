import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  TrafficApiError,
  v3TrafficApi,
  type PeersResponse,
  type PeerSort,
  type TrafficApi,
  type TrafficDirection,
  type TrafficResolution,
  type TrafficResponse,
} from '../../api/v3Traffic';

export type TrafficRangeKey = '15m' | '1h' | '6h' | '24h' | '7d' | '30d';
export interface TrafficRange {
  key: TrafficRangeKey;
  label: string;
  milliseconds: number;
  resolution: TrafficResolution;
}
export const TRAFFIC_RANGES: readonly TrafficRange[] = [
  { key: '15m', label: '最近 15 分钟', milliseconds: 15 * 60_000, resolution: 'minute' },
  { key: '1h', label: '1 小时', milliseconds: 60 * 60_000, resolution: 'minute' },
  { key: '6h', label: '6 小时', milliseconds: 6 * 60 * 60_000, resolution: 'minute' },
  { key: '24h', label: '24 小时', milliseconds: 24 * 60 * 60_000, resolution: '5minute' },
  { key: '7d', label: '7 天', milliseconds: 7 * 24 * 60 * 60_000, resolution: 'hour' },
  { key: '30d', label: '30 天', milliseconds: 30 * 24 * 60 * 60_000, resolution: 'hour' },
] as const;

interface VisibilityTarget {
  readonly visibilityState: DocumentVisibilityState;
  addEventListener(type: 'visibilitychange', listener: EventListener): void;
  removeEventListener(type: 'visibilitychange', listener: EventListener): void;
}
interface Options {
  deviceId: string | null;
  active: boolean;
  api?: TrafficApi;
  now?: () => Date;
  visibilityTarget?: VisibilityTarget | null;
  realtimeIntervalMs?: number;
  historyIntervalMs?: number;
  peersIntervalMs?: number;
  maxBackoffMs?: number;
}

const systemNow = () => new Date();
const noop = () => {};

function normalizedError(error: unknown): TrafficApiError {
  if (error instanceof TrafficApiError) return error;
  return new TrafficApiError('network', '流量工作区发生未知错误', { cause: error });
}
function isAbort(error: unknown): boolean {
  return error instanceof TrafficApiError && error.kind === 'aborted';
}
function rangeByKey(key: TrafficRangeKey): TrafficRange {
  return TRAFFIC_RANGES.find((range) => range.key === key) ?? TRAFFIC_RANGES[1];
}
function bounds(now: Date, milliseconds: number) {
  return { from: new Date(now.getTime() - milliseconds), to: now };
}
function nextDelay(base: number, failures: number, maximum: number) {
  return Math.min(maximum, base * 2 ** Math.min(failures, 5));
}
function assertDevice<T extends { device_id: string }>(response: T, expected: string): T {
  if (response.device_id !== expected) {
    throw new TrafficApiError('invalid_response', '流量响应 device_id 与当前设备不一致');
  }
  return response;
}

export function useDeviceTraffic(options: Options) {
  const api = options.api ?? v3TrafficApi;
  const nowRef = useRef(options.now ?? systemNow);
  nowRef.current = options.now ?? systemNow;
  const visibilityTarget = options.visibilityTarget === undefined
    ? (typeof document === 'undefined' ? null : document)
    : options.visibilityTarget;
  const [visible, setVisible] = useState(
    visibilityTarget?.visibilityState !== 'hidden',
  );
  const [rangeKey, setRangeKey] = useState<TrafficRangeKey>('1h');
  const [peerDirection, setPeerDirectionState] = useState<'all' | TrafficDirection>('all');
  const [peerProtocol, setPeerProtocolState] = useState('');
  const [peerSort, setPeerSortState] = useState<PeerSort>('bytes');
  const [peerOffset, setPeerOffset] = useState(0);
  const [realtime, setRealtime] = useState<TrafficResponse | null>(null);
  const [history, setHistory] = useState<TrafficResponse | null>(null);
  const [peers, setPeers] = useState<PeersResponse | null>(null);
  const [realtimeError, setRealtimeError] = useState<TrafficApiError | null>(null);
  const [historyError, setHistoryError] = useState<TrafficApiError | null>(null);
  const [peersError, setPeersError] = useState<TrafficApiError | null>(null);
  const [realtimeLoading, setRealtimeLoading] = useState(false);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [peersLoading, setPeersLoading] = useState(false);
  const [realtimeUpdatedAt, setRealtimeUpdatedAt] = useState<string | null>(null);
  const [historyUpdatedAt, setHistoryUpdatedAt] = useState<string | null>(null);
  const [peersUpdatedAt, setPeersUpdatedAt] = useState<string | null>(null);
  const refreshRealtimeRef = useRef<() => void>(noop);
  const refreshHistoryRef = useRef<() => void>(noop);
  const refreshPeersRef = useRef<() => void>(noop);

  useEffect(() => {
    if (!visibilityTarget) return undefined;
    const listener: EventListener = () => setVisible(visibilityTarget.visibilityState !== 'hidden');
    visibilityTarget.addEventListener('visibilitychange', listener);
    return () => visibilityTarget.removeEventListener('visibilitychange', listener);
  }, [visibilityTarget]);

  useEffect(() => {
    setRealtime(null);
    setHistory(null);
    setPeers(null);
    setRealtimeError(null);
    setHistoryError(null);
    setPeersError(null);
    setRealtimeUpdatedAt(null);
    setHistoryUpdatedAt(null);
    setPeersUpdatedAt(null);
    setPeerOffset(0);
  }, [options.deviceId]);

  useEffect(() => {
    if (!options.active || !visible || !options.deviceId) {
      refreshRealtimeRef.current = noop;
      return undefined;
    }
    const deviceId = options.deviceId;
    let stopped = false;
    let timer: number | undefined;
    let controller: AbortController | null = null;
    let pending = false;
    let failures = 0;
    const interval = options.realtimeIntervalMs ?? 5_000;
    const maximum = options.maxBackoffMs ?? 30_000;
    const schedule = (delay: number) => {
      if (!stopped) timer = window.setTimeout(() => void run(), delay);
    };
    const run = async () => {
      if (pending || stopped) return;
      pending = true;
      controller = new AbortController();
      setRealtimeLoading(true);
      try {
        const current = nowRef.current();
        const result = assertDevice(await api.getTraffic(deviceId, {
          ...bounds(current, 5 * 60_000), resolution: 'minute',
        }, controller.signal), deviceId);
        if (stopped) return;
        setRealtime(result);
        setRealtimeError(null);
        setRealtimeUpdatedAt(nowRef.current().toISOString());
        failures = 0;
        schedule(interval);
      } catch (error) {
        if (stopped || isAbort(error)) return;
        setRealtimeError(normalizedError(error));
        failures += 1;
        schedule(nextDelay(interval, failures, maximum));
      } finally {
        pending = false;
        if (!stopped) setRealtimeLoading(false);
      }
    };
    refreshRealtimeRef.current = () => {
      if (timer !== undefined) window.clearTimeout(timer);
      void run();
    };
    void run();
    return () => {
      stopped = true;
      refreshRealtimeRef.current = noop;
      if (timer !== undefined) window.clearTimeout(timer);
      controller?.abort();
    };
  }, [api, options.active, options.deviceId, options.maxBackoffMs,
    options.realtimeIntervalMs, visible]);

  const selectedRange = useMemo(() => rangeByKey(rangeKey), [rangeKey]);
  useEffect(() => {
    if (!options.active || !visible || !options.deviceId) {
      refreshHistoryRef.current = noop;
      return undefined;
    }
    const deviceId = options.deviceId;
    let stopped = false;
    let timer: number | undefined;
    let controller: AbortController | null = null;
    let pending = false;
    let failures = 0;
    const interval = options.historyIntervalMs ?? 30_000;
    const maximum = options.maxBackoffMs ?? 120_000;
    const schedule = (delay: number) => {
      if (!stopped) timer = window.setTimeout(() => void run(), delay);
    };
    const run = async () => {
      if (pending || stopped) return;
      pending = true;
      controller = new AbortController();
      setHistoryLoading(true);
      try {
        const current = nowRef.current();
        const result = assertDevice(await api.getTraffic(deviceId, {
          ...bounds(current, selectedRange.milliseconds),
          resolution: selectedRange.resolution,
        }, controller.signal), deviceId);
        if (stopped) return;
        setHistory(result);
        setHistoryError(null);
        setHistoryUpdatedAt(nowRef.current().toISOString());
        failures = 0;
        schedule(interval);
      } catch (error) {
        if (stopped || isAbort(error)) return;
        setHistoryError(normalizedError(error));
        failures += 1;
        schedule(nextDelay(interval, failures, maximum));
      } finally {
        pending = false;
        if (!stopped) setHistoryLoading(false);
      }
    };
    refreshHistoryRef.current = () => {
      if (timer !== undefined) window.clearTimeout(timer);
      void run();
    };
    void run();
    return () => {
      stopped = true;
      refreshHistoryRef.current = noop;
      if (timer !== undefined) window.clearTimeout(timer);
      controller?.abort();
    };
  }, [api, options.active, options.deviceId, options.historyIntervalMs,
    options.maxBackoffMs, selectedRange, visible]);

  useEffect(() => {
    if (!options.active || !visible || !options.deviceId) {
      refreshPeersRef.current = noop;
      return undefined;
    }
    const deviceId = options.deviceId;
    let stopped = false;
    let timer: number | undefined;
    let controller: AbortController | null = null;
    let pending = false;
    let failures = 0;
    const interval = options.peersIntervalMs ?? 60_000;
    const maximum = options.maxBackoffMs ?? 240_000;
    const schedule = (delay: number) => {
      if (!stopped) timer = window.setTimeout(() => void run(), delay);
    };
    const run = async () => {
      if (pending || stopped) return;
      pending = true;
      controller = new AbortController();
      setPeersLoading(true);
      try {
        const current = nowRef.current();
        const result = assertDevice(await api.getPeers(deviceId, {
          ...bounds(current, selectedRange.milliseconds),
          direction: peerDirection === 'all' ? undefined : peerDirection,
          protocol: peerProtocol.trim() || undefined,
          sort: peerSort,
          limit: 25,
          offset: peerOffset,
        }, controller.signal), deviceId);
        if (stopped) return;
        setPeers(result);
        setPeersError(null);
        setPeersUpdatedAt(nowRef.current().toISOString());
        failures = 0;
        schedule(interval);
      } catch (error) {
        if (stopped || isAbort(error)) return;
        setPeersError(normalizedError(error));
        failures += 1;
        schedule(nextDelay(interval, failures, maximum));
      } finally {
        pending = false;
        if (!stopped) setPeersLoading(false);
      }
    };
    refreshPeersRef.current = () => {
      if (timer !== undefined) window.clearTimeout(timer);
      void run();
    };
    void run();
    return () => {
      stopped = true;
      refreshPeersRef.current = noop;
      if (timer !== undefined) window.clearTimeout(timer);
      controller?.abort();
    };
  }, [api, options.active, options.deviceId, options.maxBackoffMs,
    options.peersIntervalMs, peerDirection, peerOffset, peerProtocol, peerSort,
    selectedRange, visible]);

  const setPeerDirection = useCallback((value: 'all' | TrafficDirection) => {
    setPeerOffset(0);
    setPeerDirectionState(value);
  }, []);
  const setPeerProtocol = useCallback((value: string) => {
    setPeerOffset(0);
    setPeerProtocolState(value);
  }, []);
  const setPeerSort = useCallback((value: PeerSort) => {
    setPeerOffset(0);
    setPeerSortState(value);
  }, []);
  const setRange = useCallback((value: TrafficRangeKey) => {
    setPeerOffset(0);
    setRangeKey(value);
  }, []);
  const refreshAll = useCallback(() => {
    refreshRealtimeRef.current();
    refreshHistoryRef.current();
    refreshPeersRef.current();
  }, []);

  return {
    visible,
    rangeKey,
    range: selectedRange,
    setRange,
    realtime,
    history,
    peers,
    realtimeError,
    historyError,
    peersError,
    realtimeLoading,
    historyLoading,
    peersLoading,
    realtimeUpdatedAt,
    historyUpdatedAt,
    peersUpdatedAt,
    peerDirection,
    setPeerDirection,
    peerProtocol,
    setPeerProtocol,
    peerSort,
    setPeerSort,
    peerOffset,
    setPeerOffset,
    refreshAll,
  };
}

export type DeviceTrafficWorkspace = ReturnType<typeof useDeviceTraffic>;
