import { StrictMode } from 'react';
import { act, render, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { MonitorApiError, type MonitorSnapshot, type RealtimeEventType } from '../../api/v3Monitor';
import { validSnapshot } from '../../test/monitorFixtures';
import { MonitorStore, useMonitorStore, type EventSourceLike } from './monitorStore';

class FakeEventSource implements EventSourceLike {
  onopen: ((event: Event) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;
  readonly listeners = new Map<string, EventListener>();
  readonly close = vi.fn();

  addEventListener(type: string, listener: EventListener) {
    this.listeners.set(type, listener);
  }

  open() {
    this.onopen?.(new Event('open'));
  }

  fail() {
    this.onerror?.(new Event('error'));
  }

  emit(type: RealtimeEventType | 'snapshot.required', payload: unknown, lastEventId = '') {
    const event = new MessageEvent(type, { data: JSON.stringify(payload), lastEventId });
    this.listeners.get(type)?.(event);
  }
}

class FakeVisibility {
  visibilityState: DocumentVisibilityState = 'hidden';
  private listener: EventListener | null = null;
  addEventListener(_type: 'visibilitychange', listener: EventListener) { this.listener = listener; }
  removeEventListener(_type: 'visibilitychange', listener: EventListener) {
    if (this.listener === listener) this.listener = null;
  }
  show() { this.visibilityState = 'visible'; this.listener?.(new Event('visibilitychange')); }
}

function telemetry(eventId = 6, stateVersion = 6) {
  return {
    event_id: eventId,
    event_type: 'device.telemetry_updated',
    occurred_at: '2026-09-20T02:00:01Z',
    device_id: 'camera-01',
    state_version: stateVersion,
    payload: {
      connection_status: 'online',
      observation_id: 22,
      source: 'mqtt',
      ip_address: '192.168.4.22',
      observed_at: '2026-09-20T02:00:01Z',
      received_at: '2026-09-20T02:00:01Z',
      sources: ['mqtt', 'probe-a'],
    },
  };
}

function inventory(eventId: number, action: 'created' | 'updated' = 'updated') {
  return {
    event_id: eventId,
    event_type: 'device.inventory_changed',
    occurred_at: '2026-09-20T02:00:01Z',
    device_id: null,
    state_version: 2,
    payload: {
      action,
      device_id: 'camera-01',
      profile_version: 2,
    },
  };
}

function discovered(eventId: number) {
  return {
    event_id: eventId,
    event_type: 'device.discovered',
    occurred_at: '2026-09-20T02:00:01Z',
    device_id: null,
    state_version: 1,
    payload: { candidate_id: 'candidate-01', status: 'pending', candidate_version: 1 },
  };
}

function harness(options: {
  snapshots?: MonitorSnapshot[];
  failure?: MonitorApiError;
  visibility?: FakeVisibility;
} = {}) {
  const sources: { url: string; source: FakeEventSource }[] = [];
  const snapshots = options.snapshots ?? [validSnapshot];
  let fetchIndex = 0;
  const fetchSnapshot = options.failure
    ? vi.fn().mockRejectedValue(options.failure)
    : vi.fn().mockImplementation(async () => snapshots[Math.min(fetchIndex++, snapshots.length - 1)]);
  const pendingTimers: (() => void)[] = [];
  const store = new MonitorStore({
    fetchSnapshot,
    createEventSource: (url) => {
      const source = new FakeEventSource();
      sources.push({ url, source });
      return source;
    },
    now: () => new Date('2026-09-20T02:00:02Z'),
    setTimer: (callback) => { pendingTimers.push(callback); return 1 as ReturnType<typeof setTimeout>; },
    clearTimer: () => {},
    visibilitySource: options.visibility ?? null,
    retryDelaysMs: [10, 20],
  });
  return { store, sources, fetchSnapshot, pendingTimers };
}

describe('MonitorStore', () => {
  it('loads snapshot before opening exactly one SSE at its cursor', async () => {
    const test = harness();
    test.store.start();
    expect(test.sources).toHaveLength(0);
    await waitFor(() => expect(test.sources).toHaveLength(1));
    expect(test.sources[0].url).toBe('/api/v3/events?after=5');
    test.sources[0].source.open();
    expect(test.store.getSnapshot().realtime).toBe('connected');
  });

  it('ignores duplicate event ids and stale state versions without rolling state back', async () => {
    const test = harness();
    test.store.start();
    await waitFor(() => expect(test.sources).toHaveLength(1));
    const source = test.sources[0].source;
    source.emit('device.telemetry_updated', telemetry(5, 99), '5');
    expect(test.store.getSnapshot().lastEventId).toBe(5);
    source.emit('device.telemetry_updated', telemetry(6, 5), '6');
    expect(test.store.getSnapshot().snapshot?.devices[0].ip_address).toBe('192.168.4.21');
    expect(test.store.getSnapshot().recentEvents).toHaveLength(0);
    expect(test.store.getSnapshot().lastEventId).toBe(6);
  });

  it('applies a newer device state event without re-fetching the snapshot', async () => {
    const test = harness();
    test.store.start();
    await waitFor(() => expect(test.sources).toHaveLength(1));
    test.sources[0].source.emit('device.telemetry_updated', telemetry(), '6');
    expect(test.store.getSnapshot().snapshot?.devices[0]).toMatchObject({
      ip_address: '192.168.4.22', state_version: 6, sources: ['mqtt', 'probe-a'],
    });
    expect(test.fetchSnapshot).toHaveBeenCalledTimes(1);
  });

  it('renders a newer device event through the React hook', async () => {
    const sources: FakeEventSource[] = [];
    function Probe() {
      const monitor = useMonitorStore({
        fetchSnapshot: vi.fn().mockResolvedValue(validSnapshot),
        createEventSource: () => {
          const source = new FakeEventSource();
          sources.push(source);
          return source;
        },
        visibilitySource: null,
      });
      const device = monitor.snapshot?.devices[0];
      return <span>{device ? `${device.ip_address}/${device.state_version}` : 'loading'}</span>;
    }
    render(<Probe />);
    await waitFor(() => expect(sources).toHaveLength(1));
    act(() => sources[0].emit('device.telemetry_updated', telemetry(), '6'));
    await waitFor(() => expect(document.body).toHaveTextContent('192.168.4.22/6'));
  });

  it('snapshot.required closes the old source, refetches, and reconnects once', async () => {
    const next = { ...validSnapshot, event_cursor: 10 };
    const test = harness({ snapshots: [validSnapshot, next] });
    test.store.start();
    await waitFor(() => expect(test.sources).toHaveLength(1));
    const first = test.sources[0].source;
    first.emit('snapshot.required', { event_cursor: 9, reason: 'event_log_gap' });
    await waitFor(() => expect(test.sources).toHaveLength(2));
    expect(first.close).toHaveBeenCalledTimes(1);
    expect(test.sources[1].url).toBe('/api/v3/events?after=10');
    expect(test.fetchSnapshot).toHaveBeenCalledTimes(2);
  });

  it('debounces inventory changes into one full snapshot resynchronization', async () => {
    const next = { ...validSnapshot, event_cursor: 7 };
    const test = harness({ snapshots: [validSnapshot, next] });
    test.store.start();
    await waitFor(() => expect(test.sources).toHaveLength(1));
    const first = test.sources[0].source;
    first.emit('device.inventory_changed', inventory(6), '6');
    first.emit('device.inventory_changed', inventory(7, 'created'), '7');

    expect(first.close).toHaveBeenCalledTimes(1);
    expect(test.store.getSnapshot()).toMatchObject({
      phase: 'resyncing', stale: true, lastEventId: 7,
    });
    expect(test.pendingTimers).toHaveLength(1);
    test.pendingTimers[0]();
    await waitFor(() => expect(test.fetchSnapshot).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(test.sources).toHaveLength(2));
    expect(test.sources[1].url).toBe('/api/v3/events?after=7');
  });

  it('accepts a discovery event cursor without adding an untrusted candidate to trusted devices', async () => {
    const test = harness();
    test.store.start();
    await waitFor(() => expect(test.sources).toHaveLength(1));
    test.sources[0].source.emit('device.discovered', discovered(6), '6');
    expect(test.store.getSnapshot().lastEventId).toBe(6);
    expect(test.store.getSnapshot().recentEvents[0]?.event_type).toBe('device.discovered');
    expect(test.store.getSnapshot().snapshot?.devices).toEqual(validSnapshot.devices);
    expect(test.fetchSnapshot).toHaveBeenCalledTimes(1);
  });

  it('keeps the last real snapshot stale on disconnect and schedules only one reconnect', async () => {
    const test = harness();
    test.store.start();
    await waitFor(() => expect(test.sources).toHaveLength(1));
    test.sources[0].source.fail();
    test.sources[0].source.fail();
    expect(test.store.getSnapshot()).toMatchObject({ realtime: 'disconnected', stale: true });
    expect(test.store.getSnapshot().snapshot?.devices[0].device_id).toBe('camera-01');
    expect(test.pendingTimers).toHaveLength(1);
    test.pendingTimers[0]();
    expect(test.sources).toHaveLength(2);
    expect(test.sources[1].url).toBe('/api/v3/events?after=5');
  });

  it('refetches after the page becomes visible', async () => {
    const visibility = new FakeVisibility();
    const test = harness({ visibility });
    test.store.start();
    await waitFor(() => expect(test.sources).toHaveLength(1));
    visibility.show();
    await waitFor(() => expect(test.fetchSnapshot).toHaveBeenCalledTimes(2));
    expect(test.sources).toHaveLength(2);
    expect(test.sources[0].source.close).toHaveBeenCalled();
  });

  it.each([
    ['unauthorized', 401], ['forbidden', 403], ['unavailable', 503], ['network', null],
  ] as const)('preserves the distinct %s snapshot failure', async (kind, status) => {
    const test = harness({
      failure: new MonitorApiError(kind, `failure-${kind}`, status ? { status } : {}),
    });
    test.store.start();
    await waitFor(() => expect(test.store.getSnapshot().phase).toBe('error'));
    expect(test.store.getSnapshot().error?.kind).toBe(kind);
    expect(test.sources).toHaveLength(0);
  });

  it('does not leave duplicate EventSources under StrictMode and closes on unmount', async () => {
    const sources: FakeEventSource[] = [];
    const fetchSnapshot = vi.fn().mockResolvedValue(validSnapshot);
    function Probe() {
      const monitor = useMonitorStore({
        fetchSnapshot,
        createEventSource: () => {
          const source = new FakeEventSource();
          sources.push(source);
          return source;
        },
        visibilitySource: null,
      });
      return <span>{monitor.realtime}</span>;
    }
    const view = render(<StrictMode><Probe /></StrictMode>);
    await waitFor(() => expect(sources).toHaveLength(1));
    expect(sources.filter((source) => !source.close.mock.calls.length)).toHaveLength(1);
    act(() => view.unmount());
    expect(sources[0].close).toHaveBeenCalledTimes(1);
  });
});
