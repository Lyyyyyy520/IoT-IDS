import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { TrafficApiError, type TrafficApi } from '../../api/v3Traffic';
import { peersResponse, trafficResponse } from '../../test/trafficFixtures';
import { useDeviceTraffic } from './useDeviceTraffic';

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((accept, fail) => { resolve = accept; reject = fail; });
  return { promise, resolve, reject };
}
function fakeApi(overrides: Partial<TrafficApi> = {}): TrafficApi {
  return {
    getTraffic: vi.fn().mockResolvedValue(trafficResponse()),
    getPeers: vi.fn().mockResolvedValue(peersResponse()),
    ...overrides,
  };
}
class VisibilityTarget {
  visibilityState: DocumentVisibilityState = 'visible';
  listeners = new Set<EventListener>();
  addEventListener(_type: 'visibilitychange', listener: EventListener) { this.listeners.add(listener); }
  removeEventListener(_type: 'visibilitychange', listener: EventListener) { this.listeners.delete(listener); }
  set(value: DocumentVisibilityState) {
    this.visibilityState = value;
    this.listeners.forEach((listener) => listener(new Event('visibilitychange')));
  }
}

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe('useDeviceTraffic', () => {
  it('uses separate realtime/history/peer queries and supported resolution windows', async () => {
    const api = fakeApi();
    const now = () => new Date('2026-09-21T10:00:00Z');
    const { result } = renderHook(() => useDeviceTraffic({
      deviceId: 'camera-01', active: true, api, now, visibilityTarget: null,
    }));
    await waitFor(() => expect(result.current.history?.device_id).toBe('camera-01'));
    expect(api.getTraffic).toHaveBeenCalledTimes(2);
    expect(vi.mocked(api.getTraffic).mock.calls[0][1]).toMatchObject({
      from: new Date('2026-09-21T09:55:00Z'),
      to: new Date('2026-09-21T10:00:00Z'),
      resolution: 'minute',
    });
    expect(vi.mocked(api.getTraffic).mock.calls[1][1]).toMatchObject({
      from: new Date('2026-09-21T09:00:00Z'), resolution: 'minute',
    });
    act(() => result.current.setRange('7d'));
    await waitFor(() => expect(
      vi.mocked(api.getTraffic).mock.calls.some(([, query]) => query.resolution === 'hour'),
    ).toBe(true));
  });

  it('aborts old device requests and never accepts their slow response', async () => {
    const oldRealtime = deferred<ReturnType<typeof trafficResponse>>();
    let firstCamera = true;
    const signals: AbortSignal[] = [];
    const getTraffic = vi.fn((deviceId: string, _query: unknown, signal?: AbortSignal) => {
      if (signal) signals.push(signal);
      if (deviceId === 'camera-01' && firstCamera) {
        firstCamera = false;
        return oldRealtime.promise;
      }
      return Promise.resolve(trafficResponse({ device_id: deviceId }));
    });
    const api = fakeApi({
      getTraffic,
      getPeers: vi.fn()
        .mockResolvedValueOnce(peersResponse())
        .mockResolvedValue(peersResponse({ device_id: 'sensor-02' })),
    });
    const { result, rerender } = renderHook(
      ({ deviceId }) => useDeviceTraffic({
        deviceId, active: true, api, visibilityTarget: null,
        now: () => new Date('2026-09-21T10:00:00Z'),
      }),
      { initialProps: { deviceId: 'camera-01' } },
    );
    rerender({ deviceId: 'sensor-02' });
    await waitFor(() => expect(result.current.history?.device_id).toBe('sensor-02'));
    expect(signals.some((signal) => signal.aborted)).toBe(true);
    await act(async () => {
      oldRealtime.resolve(trafficResponse({ device_id: 'camera-01' }));
      await oldRealtime.promise;
    });
    expect(result.current.realtime?.device_id).toBe('sensor-02');
  });

  it('pauses while hidden and refreshes immediately when visible again', async () => {
    vi.useFakeTimers();
    const visibility = new VisibilityTarget();
    const api = fakeApi();
    renderHook(() => useDeviceTraffic({
      deviceId: 'camera-01', active: true, api,
      visibilityTarget: visibility,
      now: () => new Date('2026-09-21T10:00:00Z'),
      realtimeIntervalMs: 100, historyIntervalMs: 100, peersIntervalMs: 100,
    }));
    await act(async () => { await Promise.resolve(); });
    const initialTrafficCalls = vi.mocked(api.getTraffic).mock.calls.length;
    act(() => visibility.set('hidden'));
    await act(async () => { vi.advanceTimersByTime(1_000); await Promise.resolve(); });
    expect(api.getTraffic).toHaveBeenCalledTimes(initialTrafficCalls);
    act(() => visibility.set('visible'));
    await act(async () => { await Promise.resolve(); });
    expect(vi.mocked(api.getTraffic).mock.calls.length).toBeGreaterThan(initialTrafficCalls);
  });

  it('retains last real data and marks the stream stale after a refresh failure', async () => {
    vi.useFakeTimers();
    const getTraffic = vi.fn()
      .mockResolvedValueOnce(trafficResponse())
      .mockResolvedValueOnce(trafficResponse())
      .mockRejectedValue(new TrafficApiError('network', '断线'));
    const api = fakeApi({ getTraffic });
    const { result } = renderHook(() => useDeviceTraffic({
      deviceId: 'camera-01', active: true, api, visibilityTarget: null,
      now: () => new Date('2026-09-21T10:00:00Z'),
      realtimeIntervalMs: 100, historyIntervalMs: 100_000, peersIntervalMs: 100_000,
    }));
    await act(async () => { await Promise.resolve(); });
    expect(result.current.realtime?.device_id).toBe('camera-01');
    await act(async () => { vi.advanceTimersByTime(100); await Promise.resolve(); });
    expect(result.current.realtime?.device_id).toBe('camera-01');
    expect(result.current.realtimeError?.kind).toBe('network');
  });

  it('clears timers and aborts all request controllers on unmount', () => {
    const signals: AbortSignal[] = [];
    const never = new Promise<never>(() => {});
    const api = fakeApi({
      getTraffic: vi.fn((_id, _query, signal) => {
        if (signal) signals.push(signal);
        return never;
      }),
      getPeers: vi.fn((_id, _query, signal) => {
        if (signal) signals.push(signal);
        return never;
      }),
    });
    const { unmount } = renderHook(() => useDeviceTraffic({
      deviceId: 'camera-01', active: true, api, visibilityTarget: null,
    }));
    expect(signals).toHaveLength(3);
    unmount();
    expect(signals.every((signal) => signal.aborted)).toBe(true);
  });
});
