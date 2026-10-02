import { StrictMode } from 'react';
import { act, renderHook, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { IncidentsApi } from '../../api/v3Incidents';
import { helpDetail, incidentDetail, incidentListResponse } from '../../test/incidentFixtures';
import { useIncidentWorkspace, type WorkspaceEventSource } from './useIncidentWorkspace';

class FakeSource implements WorkspaceEventSource {
  onopen: ((event: Event) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;
  listeners = new Map<string, EventListener>();
  close = vi.fn();
  addEventListener(type: string, listener: EventListener) { this.listeners.set(type, listener); }
  open() { this.onopen?.(new Event('open')); }
  fail() { this.onerror?.(new Event('error')); }
  emit(type: string, body: unknown) { this.listeners.get(type)?.(new MessageEvent(type, { data: JSON.stringify(body) })); }
}

function api(overrides: Partial<IncidentsApi> = {}): IncidentsApi {
  return {
    listIncidents: vi.fn().mockResolvedValue(incidentListResponse()),
    getIncident: vi.fn().mockResolvedValue(incidentDetail),
    createIncident: vi.fn().mockResolvedValue(incidentDetail),
    transitionIncident: vi.fn().mockResolvedValue(incidentDetail),
    getSupportContact: vi.fn().mockResolvedValue({ available: false, reason: 'support_contact_not_configured' }),
    updateSupportContact: vi.fn(),
    listHelpRequests: vi.fn().mockResolvedValue({ items: [helpDetail], total: 1, limit: 25, offset: 0 }),
    getHelpRequest: vi.fn().mockResolvedValue(helpDetail),
    updateHelpRequest: vi.fn().mockResolvedValue(helpDetail),
    ...overrides,
  };
}

function incidentEvent(id: number) {
  return {
    event_id: id, event_type: 'incident.updated', occurred_at: '2026-09-23T03:00:00Z',
    device_id: null, state_version: 2,
    payload: { incident_id: incidentDetail.incident_id, status: 'acknowledged', severity: 'high', incident_version: 2, updated_at: '2026-09-23T03:00:00Z' },
  };
}

describe('useIncidentWorkspace realtime and request isolation', () => {
  it('loads the incident cursor before opening one SSE under StrictMode and closes it on unmount', async () => {
    const sources: FakeSource[] = [];
    const client = api();
    const view = renderHook(() => useIncidentWorkspace({
      api: client,
      eventSourceFactory: () => { const source = new FakeSource(); sources.push(source); return source; },
    }), { wrapper: StrictMode });
    await waitFor(() => expect(sources).toHaveLength(1));
    expect((client.listIncidents as ReturnType<typeof vi.fn>)).toHaveBeenCalled();
    act(() => sources[0].open());
    expect(view.result.current.realtime).toBe('connected');
    view.unmount();
    expect(sources[0].close).toHaveBeenCalledTimes(1);
  });

  it('debounces incident events, ignores duplicate ids, and reconnects from the refreshed cursor', async () => {
    const sources: FakeSource[] = [];
    const timers: Array<() => void> = [];
    const client = api({
      listIncidents: vi.fn()
        .mockResolvedValueOnce(incidentListResponse({ event_cursor: 17 }))
        .mockResolvedValue(incidentListResponse({ event_cursor: 20 })),
    });
    renderHook(() => useIncidentWorkspace({
      api: client,
      eventSourceFactory: () => { const source = new FakeSource(); sources.push(source); return source; },
      setTimer: ((callback: TimerHandler) => { if (typeof callback === 'function') timers.push(callback as () => void); return 1; }) as typeof window.setTimeout,
      clearTimer: vi.fn(), debounceMs: 0,
    }));
    await waitFor(() => expect(sources).toHaveLength(1));
    act(() => {
      sources[0].emit('incident.updated', incidentEvent(18));
      sources[0].emit('incident.updated', incidentEvent(18));
      // Unsubscribed device/component event types can own intervening IDs.
      sources[0].emit('incident.recovering', { ...incidentEvent(21), event_type: 'incident.recovering', payload: { ...incidentEvent(21).payload, status: 'recovering', incident_version: 3 } });
    });
    expect(timers).toHaveLength(1);
    expect(sources[0].close).not.toHaveBeenCalled();
    act(() => timers[0]());
    await waitFor(() => expect(sources).toHaveLength(2));
    expect(sources[0].close).toHaveBeenCalledTimes(1);
    expect((client.listIncidents as ReturnType<typeof vi.fn>)).toHaveBeenCalledTimes(2);
  });

  it('handles snapshot.required with one full refresh and preserves real data on disconnect', async () => {
    const sources: FakeSource[] = [];
    const timers: Array<() => void> = [];
    const client = api();
    const view = renderHook(() => useIncidentWorkspace({
      api: client,
      eventSourceFactory: () => { const source = new FakeSource(); sources.push(source); return source; },
      setTimer: ((callback: TimerHandler) => { if (typeof callback === 'function') timers.push(callback as () => void); return 1; }) as typeof window.setTimeout,
      clearTimer: vi.fn(), debounceMs: 0,
    }));
    await waitFor(() => expect(sources).toHaveLength(1));
    act(() => sources[0].fail());
    expect(view.result.current.stale).toBe(true);
    expect(view.result.current.incidents).toHaveLength(1);
    act(() => sources[0].emit('snapshot.required', { event_cursor: 17, reason: 'event_log_gap' }));
    expect(timers).toHaveLength(1);
    act(() => timers[0]());
    await waitFor(() => expect((client.listIncidents as ReturnType<typeof vi.fn>)).toHaveBeenCalledTimes(2));
  });

  it('cancels old list requests and does not let an older response replace a new filter result', async () => {
    let resolveOld!: (value: ReturnType<typeof incidentListResponse>) => void;
    const old = new Promise<ReturnType<typeof incidentListResponse>>((resolve) => { resolveOld = resolve; });
    const newer = incidentListResponse({ items: [{ ...incidentDetail, incident_id: 'inc_new', admin_title: '新筛选结果' }] });
    const list = vi.fn().mockReturnValueOnce(old).mockResolvedValue(newer);
    const client = api({ listIncidents: list });
    const view = renderHook(() => useIncidentWorkspace({ api: client, eventSourceFactory: () => new FakeSource(), debounceMs: 0 }));
    act(() => view.result.current.setIncidentFilters({ ...view.result.current.incidentFilters, search: 'new' }));
    await waitFor(() => expect(list).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(view.result.current.incidents[0]?.incident_id).toBe('inc_new'));
    await act(async () => resolveOld(incidentListResponse()));
    expect(view.result.current.incidents[0]?.incident_id).toBe('inc_new');
  });
});
