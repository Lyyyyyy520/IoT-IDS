import { useEffect, useMemo, useSyncExternalStore } from 'react';
import {
  EVENTS_ENDPOINT,
  MonitorApiError,
  fetchMonitorSnapshot,
  parseMonitorEvent,
  parseSnapshotRequired,
  type MonitorApiErrorKind,
  type MonitorRealtimeEvent,
  type MonitorSnapshot,
  type MonitorStreamEvent,
  type RealtimeEventType,
} from '../../api/v3Monitor';

export type MonitorPhase = 'idle' | 'loading' | 'ready' | 'resyncing' | 'error';
export type RealtimeConnectionState = 'idle' | 'connecting' | 'connected' | 'disconnected';

export interface MonitorViewState {
  snapshot: MonitorSnapshot | null;
  phase: MonitorPhase;
  realtime: RealtimeConnectionState;
  stale: boolean;
  lastSyncedAt: string | null;
  lastEventId: number;
  recentEvents: MonitorRealtimeEvent[];
  error: {
    kind: MonitorApiErrorKind;
    message: string;
    requestId: string | null;
  } | null;
  resyncReason: string | null;
}

export interface EventSourceLike {
  onopen: ((event: Event) => void) | null;
  onerror: ((event: Event) => void) | null;
  addEventListener(type: string, listener: EventListener): void;
  close(): void;
}

interface VisibilitySource {
  visibilityState: DocumentVisibilityState;
  addEventListener(type: 'visibilitychange', listener: EventListener): void;
  removeEventListener(type: 'visibilitychange', listener: EventListener): void;
}

interface MonitorStoreOptions {
  enabled?: boolean;
  fetchSnapshot?: (signal?: AbortSignal) => Promise<MonitorSnapshot>;
  createEventSource?: (url: string) => EventSourceLike;
  now?: () => Date;
  setTimer?: (callback: () => void, delay: number) => ReturnType<typeof setTimeout>;
  clearTimer?: (timer: ReturnType<typeof setTimeout>) => void;
  visibilitySource?: VisibilitySource | null;
  retryDelaysMs?: readonly number[];
  inventoryResyncDelayMs?: number;
  inventoryResyncMinimumIntervalMs?: number;
}

const INITIAL_STATE: MonitorViewState = {
  snapshot: null,
  phase: 'idle',
  realtime: 'idle',
  stale: false,
  lastSyncedAt: null,
  lastEventId: 0,
  recentEvents: [],
  error: null,
  resyncReason: null,
};

const EVENT_NAMES: RealtimeEventType[] = [
  'device.connection_changed',
  'device.telemetry_updated',
  'device.inventory_changed',
  'device.discovered',
  'system.component_changed',
  'incident.opened',
  'incident.updated',
  'incident.recovering',
  'incident.resolved',
];

function defaultEventSource(url: string): EventSourceLike {
  return new EventSource(url);
}

function normalizedError(error: unknown): MonitorApiError {
  if (error instanceof MonitorApiError) return error;
  return new MonitorApiError('network', '实时监视服务发生未知错误', { cause: error });
}

export class MonitorStore {
  private state: MonitorViewState = INITIAL_STATE;
  private readonly listeners = new Set<() => void>();
  private readonly fetchSnapshot: NonNullable<MonitorStoreOptions['fetchSnapshot']>;
  private readonly createEventSource: NonNullable<MonitorStoreOptions['createEventSource']>;
  private readonly now: NonNullable<MonitorStoreOptions['now']>;
  private readonly setTimer: NonNullable<MonitorStoreOptions['setTimer']>;
  private readonly clearTimer: NonNullable<MonitorStoreOptions['clearTimer']>;
  private readonly visibilitySource: VisibilitySource | null;
  private readonly retryDelaysMs: readonly number[];
  private readonly inventoryResyncDelayMs: number;
  private readonly inventoryResyncMinimumIntervalMs: number;
  private source: EventSourceLike | null = null;
  private retryTimer: ReturnType<typeof setTimeout> | null = null;
  private inventoryTimer: ReturnType<typeof setTimeout> | null = null;
  private abortController: AbortController | null = null;
  private started = false;
  private generation = 0;
  private retryAttempt = 0;
  private lastInventoryResyncAt = Number.NEGATIVE_INFINITY;

  constructor(options: MonitorStoreOptions = {}) {
    this.fetchSnapshot = options.fetchSnapshot ?? fetchMonitorSnapshot;
    this.createEventSource = options.createEventSource ?? defaultEventSource;
    this.now = options.now ?? (() => new Date());
    this.setTimer = options.setTimer ?? ((callback, delay) => setTimeout(callback, delay));
    this.clearTimer = options.clearTimer ?? ((timer) => clearTimeout(timer));
    this.visibilitySource = options.visibilitySource === undefined
      ? (typeof document === 'undefined' ? null : document)
      : options.visibilitySource;
    this.retryDelaysMs = options.retryDelaysMs ?? [1000, 2000, 4000, 8000, 15000];
    this.inventoryResyncDelayMs = options.inventoryResyncDelayMs ?? 200;
    this.inventoryResyncMinimumIntervalMs = options.inventoryResyncMinimumIntervalMs ?? 1000;
  }

  getSnapshot = (): MonitorViewState => this.state;

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };

  start = (): void => {
    if (this.started) return;
    this.started = true;
    this.visibilitySource?.addEventListener('visibilitychange', this.handleVisibility);
    void this.synchronize('initial');
  };

  stop = (): void => {
    if (!this.started) return;
    this.started = false;
    this.generation += 1;
    this.visibilitySource?.removeEventListener('visibilitychange', this.handleVisibility);
    this.abortController?.abort();
    this.abortController = null;
    this.closeSource();
    this.cancelRetry();
    this.cancelInventoryResync();
    this.patch({ realtime: 'idle' });
  };

  resync = (reason = 'manual'): Promise<void> => {
    if (!this.started) return Promise.resolve();
    return this.synchronize(reason);
  };

  private patch(next: Partial<MonitorViewState>): void {
    this.state = { ...this.state, ...next };
    this.listeners.forEach((listener) => listener());
  }

  private handleVisibility = (): void => {
    if (this.visibilitySource?.visibilityState === 'visible') {
      void this.synchronize('visibility_restored');
    }
  };

  private async synchronize(reason: string): Promise<void> {
    const generation = ++this.generation;
    this.abortController?.abort();
    this.abortController = new AbortController();
    this.closeSource();
    this.cancelRetry();
    this.cancelInventoryResync();
    this.patch({
      phase: this.state.snapshot ? 'resyncing' : 'loading',
      realtime: 'idle',
      stale: this.state.snapshot !== null,
      error: null,
      resyncReason: reason,
    });
    try {
      const snapshot = await this.fetchSnapshot(this.abortController.signal);
      if (!this.started || generation !== this.generation) return;
      this.retryAttempt = 0;
      this.patch({
        snapshot,
        phase: 'ready',
        stale: false,
        lastSyncedAt: this.now().toISOString(),
        lastEventId: snapshot.event_cursor,
        recentEvents: [],
        error: null,
        resyncReason: null,
      });
      this.openEventSource(snapshot.event_cursor);
    } catch (error) {
      if (!this.started || generation !== this.generation) return;
      const apiError = normalizedError(error);
      if (apiError.kind === 'aborted') return;
      this.patch({
        phase: 'error',
        realtime: 'disconnected',
        stale: this.state.snapshot !== null,
        error: {
          kind: apiError.kind,
          message: apiError.message,
          requestId: apiError.requestId,
        },
        resyncReason: null,
      });
    }
  }

  private openEventSource(after: number): void {
    if (!this.started) return;
    this.closeSource();
    this.patch({ realtime: 'connecting' });
    let source: EventSourceLike;
    try {
      source = this.createEventSource(`${EVENTS_ENDPOINT}?after=${encodeURIComponent(after)}`);
    } catch (error) {
      this.handleStreamFailure(error);
      return;
    }
    this.source = source;
    source.onopen = () => {
      if (this.source !== source || !this.started) return;
      this.retryAttempt = 0;
      this.patch({ realtime: 'connected', stale: false, error: null });
    };
    source.onerror = () => {
      if (this.source !== source || !this.started) return;
      this.handleStreamFailure(new Error('event source disconnected'));
    };
    EVENT_NAMES.forEach((eventName) => {
      source.addEventListener(eventName, ((event: MessageEvent<string>) => {
        this.handleEvent(eventName, event);
      }) as EventListener);
    });
    source.addEventListener('snapshot.required', ((event: MessageEvent<string>) => {
      this.handleSnapshotRequired(event);
    }) as EventListener);
  }

  private handleEvent(eventType: RealtimeEventType, message: MessageEvent<string>): void {
    if (!this.started) return;
    let event: MonitorStreamEvent;
    try {
      event = parseMonitorEvent(eventType, message.data);
      if (message.lastEventId && Number(message.lastEventId) !== event.event_id) {
        throw new MonitorApiError('invalid_response', 'SSE id 与事件信封不一致');
      }
    } catch (error) {
      this.requireSnapshot(normalizedError(error).message);
      return;
    }
    if (event.event_id <= this.state.lastEventId) return;
    if (event.event_id !== this.state.lastEventId + 1) {
      this.requireSnapshot('检测到实时事件缺口');
      return;
    }

    const snapshot = this.state.snapshot;
    if (!snapshot) {
      this.requireSnapshot('本地没有可应用事件的快照');
      return;
    }

    if (event.event_type === 'device.inventory_changed') {
      this.scheduleInventoryResync(event);
      return;
    }

    if (event.event_type === 'device.discovered') {
      this.acceptEvent(event, snapshot);
      return;
    }

    if (
      event.event_type === 'incident.opened'
      || event.event_type === 'incident.updated'
      || event.event_type === 'incident.recovering'
      || event.event_type === 'incident.resolved'
    ) {
      this.scheduleIncidentResync(event);
      return;
    }

    if (event.event_type === 'system.component_changed') {
      const current = snapshot.system_components.find(
        (component) => component.component_id === event.payload.component_id,
      );
      if (current && event.state_version <= current.state_version) {
        this.advanceIgnoredEvent(event.event_id, snapshot);
        return;
      }
      const components = [
            ...snapshot.system_components.filter(
              (component) => component.component_id !== event.payload.component_id,
            ),
            {
              component_id: event.payload.component_id,
              readiness: event.payload.readiness,
              started_at: event.payload.started_at,
              ready_at: event.payload.ready_at,
              reason: event.payload.reason,
              state_version: event.state_version,
              updated_at: event.payload.updated_at,
            },
          ].sort((left, right) => left.component_id.localeCompare(right.component_id));
      this.acceptEvent(event, { ...snapshot, system_components: components });
      return;
    }

    const current = snapshot.devices.find((device) => device.device_id === event.device_id);
    if (!current) {
      this.requireSnapshot('事件引用了快照中不存在的设备');
      return;
    }
    if (event.state_version <= current.state_version) {
      this.advanceIgnoredEvent(event.event_id, snapshot);
      return;
    }
    const devices = snapshot.devices.map((device) =>
          device.device_id === event.device_id
            ? {
                ...device,
                connection_status: event.payload.connection_status,
                ip_address: event.payload.ip_address,
                observed_at: event.payload.observed_at,
                received_at: event.payload.received_at,
                sources: event.payload.sources,
                state_version: event.state_version,
              }
            : device,
        );
    this.acceptEvent(event, { ...snapshot, devices });
  }

  private advanceIgnoredEvent(eventId: number, snapshot: MonitorSnapshot): void {
    this.patch({
      snapshot: { ...snapshot, event_cursor: eventId },
      lastEventId: eventId,
      lastSyncedAt: this.now().toISOString(),
    });
  }

  private scheduleInventoryResync(event: MonitorStreamEvent): void {
    const snapshot = this.state.snapshot;
    if (!snapshot || event.event_type !== 'device.inventory_changed') return;
    this.closeSource();
    this.cancelRetry();
    this.patch({
      snapshot: { ...snapshot, event_cursor: event.event_id },
      lastEventId: event.event_id,
      realtime: 'idle',
      stale: true,
      phase: 'resyncing',
      resyncReason: `inventory_changed:${event.payload.action}`,
    });
    if (this.inventoryTimer !== null) return;
    const elapsed = this.now().getTime() - this.lastInventoryResyncAt;
    const rateLimitDelay = Math.max(0, this.inventoryResyncMinimumIntervalMs - elapsed);
    const delay = Math.max(this.inventoryResyncDelayMs, rateLimitDelay);
    this.inventoryTimer = this.setTimer(() => {
      this.inventoryTimer = null;
      this.lastInventoryResyncAt = this.now().getTime();
      if (this.started) void this.synchronize('inventory_changed');
    }, delay);
  }

  private scheduleIncidentResync(event: MonitorStreamEvent): void {
    const snapshot = this.state.snapshot;
    if (
      !snapshot
      || !(
        event.event_type === 'incident.opened'
        || event.event_type === 'incident.updated'
        || event.event_type === 'incident.recovering'
        || event.event_type === 'incident.resolved'
      )
    ) return;
    this.closeSource();
    this.cancelRetry();
    this.patch({
      snapshot: { ...snapshot, event_cursor: event.event_id },
      lastEventId: event.event_id,
      realtime: 'idle',
      stale: true,
      phase: 'resyncing',
      resyncReason: 'incident_changed:' + event.event_type,
    });
    if (this.inventoryTimer !== null) return;
    const elapsed = this.now().getTime() - this.lastInventoryResyncAt;
    const rateLimitDelay = Math.max(
      0, this.inventoryResyncMinimumIntervalMs - elapsed,
    );
    const delay = Math.max(
      this.inventoryResyncDelayMs, rateLimitDelay,
    );
    this.inventoryTimer = this.setTimer(() => {
      this.inventoryTimer = null;
      this.lastInventoryResyncAt = this.now().getTime();
      if (this.started) void this.synchronize('incident_changed');
    }, delay);
  }

  private acceptEvent(event: MonitorRealtimeEvent, snapshot: MonitorSnapshot): void {
    this.patch({
      snapshot: { ...snapshot, event_cursor: event.event_id },
      lastEventId: event.event_id,
      lastSyncedAt: this.now().toISOString(),
      recentEvents: [event, ...this.state.recentEvents].slice(0, 24),
      phase: 'ready',
      stale: false,
      error: null,
    });
  }

  private handleSnapshotRequired(message: MessageEvent<string>): void {
    try {
      const required = parseSnapshotRequired(message.data);
      void this.synchronize(`snapshot_required:${required.reason}`);
    } catch (error) {
      this.requireSnapshot(normalizedError(error).message);
    }
  }

  private requireSnapshot(reason: string): void {
    if (!this.started || this.state.phase === 'resyncing') return;
    void this.synchronize(`unsafe_increment:${reason}`);
  }

  private handleStreamFailure(_error: unknown): void {
    this.closeSource();
    this.patch({ realtime: 'disconnected', stale: this.state.snapshot !== null });
    if (!this.started || this.retryTimer !== null) return;
    const index = Math.min(this.retryAttempt, this.retryDelaysMs.length - 1);
    const delay = this.retryDelaysMs[index] ?? 15000;
    this.retryAttempt += 1;
    this.retryTimer = this.setTimer(() => {
      this.retryTimer = null;
      if (this.started) this.openEventSource(this.state.lastEventId);
    }, delay);
  }

  private closeSource(): void {
    if (!this.source) return;
    const source = this.source;
    this.source = null;
    source.onopen = null;
    source.onerror = null;
    source.close();
  }

  private cancelRetry(): void {
    if (this.retryTimer === null) return;
    this.clearTimer(this.retryTimer);
    this.retryTimer = null;
  }

  private cancelInventoryResync(): void {
    if (this.inventoryTimer === null) return;
    this.clearTimer(this.inventoryTimer);
    this.inventoryTimer = null;
  }
}

export function useMonitorStore(options?: MonitorStoreOptions) {
  const store = useMemo(() => new MonitorStore(options), []);
  const enabled = options?.enabled ?? true;
  const state = useSyncExternalStore(store.subscribe, store.getSnapshot, store.getSnapshot);
  useEffect(() => {
    if (!enabled) return undefined;
    store.start();
    return store.stop;
  }, [enabled, store]);
  return {
    ...state,
    resync: store.resync,
  };
}
