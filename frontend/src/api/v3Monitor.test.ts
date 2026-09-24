import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  MonitorApiError,
  fetchMonitorSnapshot,
  parseMonitorEvent,
  parseMonitorSnapshot,
} from './v3Monitor';
import { validSnapshot } from '../test/monitorFixtures';

function response(status: number, body: unknown): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: vi.fn().mockResolvedValue(body),
  } as unknown as Response;
}

afterEach(() => vi.unstubAllGlobals());

describe('v3 monitor runtime contracts', () => {
  it('parses a valid monitor snapshot without inventing fields', () => {
    expect(parseMonitorSnapshot(validSnapshot)).toEqual(validSnapshot);
  });

  it('rejects malformed snapshot timestamps and schemas', () => {
    expect(() => parseMonitorSnapshot({ ...validSnapshot, generated_at: 'yesterday' }))
      .toThrow(MonitorApiError);
    expect(() => parseMonitorSnapshot({ ...validSnapshot, schema_version: 2 }))
      .toThrow('版本不受支持');
  });

  it('parses a complete device state projection from SSE', () => {
    const event = parseMonitorEvent('device.telemetry_updated', JSON.stringify({
      event_id: 6,
      event_type: 'device.telemetry_updated',
      occurred_at: '2026-09-20T02:00:01Z',
      device_id: 'camera-01',
      state_version: 6,
      payload: {
        connection_status: 'online',
        observation_id: 22,
        source: 'mqtt',
        ip_address: '192.168.4.22',
        observed_at: '2026-09-20T02:00:01Z',
        received_at: '2026-09-20T02:00:01Z',
        sources: ['mqtt', 'probe-a'],
      },
    }));
    expect(event.event_type).toBe('device.telemetry_updated');
    if (event.event_type !== 'device.telemetry_updated') throw new Error('unexpected event type');
    expect(event.payload.ip_address).toBe('192.168.4.22');
    expect(event.payload.sources).toEqual(['mqtt', 'probe-a']);
  });

  it('parses a retained-safe device inventory event', () => {
    const event = parseMonitorEvent('device.inventory_changed', JSON.stringify({
      event_id: 7,
      event_type: 'device.inventory_changed',
      occurred_at: '2026-09-20T02:00:01Z',
      device_id: null,
      state_version: 3,
      payload: {
        action: 'retired',
        device_id: 'camera-01',
        profile_version: 3,
      },
    }));
    expect(event).toMatchObject({
      event_type: 'device.inventory_changed',
      device_id: null,
      payload: { action: 'retired', device_id: 'camera-01', profile_version: 3 },
    });
  });

  it('parses discovery events without exposing candidate identity evidence', () => {
    const event = parseMonitorEvent('device.discovered', JSON.stringify({
      event_id: 8,
      event_type: 'device.discovered',
      occurred_at: '2026-09-20T02:00:01Z',
      device_id: null,
      state_version: 1,
      payload: {
        candidate_id: 'candidate-01', status: 'pending', candidate_version: 1,
        mac_address: 'AA:BB:CC:DD:EE:FF', ip_address: '192.0.2.9',
      },
    }));
    expect(event).toMatchObject({
      event_type: 'device.discovered', device_id: null,
      payload: { candidate_id: 'candidate-01', status: 'pending', candidate_version: 1 },
    });
    expect(JSON.stringify(event)).not.toContain('AA:BB:CC:DD:EE:FF');
    expect(JSON.stringify(event)).not.toContain('192.0.2.9');
  });

  it('parses incident capability data and a redacted incident SSE summary', () => {
    const snapshot = parseMonitorSnapshot({
      ...validSnapshot,
      capabilities: {
        ...validSnapshot.capabilities,
        incident: {
          available: true,
          reason: 'recorded_incident_workflow_available',
          semantics: 'no_recorded_incidents_is_not_a_safety_assurance',
        },
      },
      incidents: {
        active: [{
          incident_id: 'inc_0123456789abcdef0123456789abcdef',
          incident_type: 'device_anomaly',
          severity: 'high',
          status: 'open',
          source: 'manual',
          admin_title: '设备通信异常',
          first_seen_at: '2026-09-20T02:00:00Z',
          updated_at: '2026-09-20T02:00:00Z',
          incident_version: 1,
        }],
        recent: [],
        empty_meaning: 'no_recorded_incidents_not_proven_safe',
      },
    });
    expect(snapshot.incidents?.active[0].status).toBe('open');
    expect(snapshot.capabilities.graph.available).toBe(false);

    const event = parseMonitorEvent('incident.opened', JSON.stringify({
      event_id: 8,
      event_type: 'incident.opened',
      occurred_at: '2026-09-20T02:00:01Z',
      device_id: null,
      state_version: 1,
      payload: {
        incident_id: 'inc_0123456789abcdef0123456789abcdef',
        status: 'open',
        severity: 'high',
        source: 'manual',
        incident_version: 1,
        affected_device_ids: ['camera-01'],
      },
    }));
    expect(event.event_type).toBe('incident.opened');
    expect(JSON.stringify(event)).not.toContain('admin_details');
  });

  it.each([
    [401, 'unauthorized'],
    [403, 'forbidden'],
    [503, 'unavailable'],
  ] as const)('classifies HTTP %s distinctly', async (status, kind) => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response(status, {
      error: { code: `status_${status}`, message: '拒绝访问', request_id: 'req-1' },
    })));
    await expect(fetchMonitorSnapshot()).rejects.toMatchObject({ kind, status, requestId: 'req-1' });
  });

  it('distinguishes network and invalid-response failures', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('offline')));
    await expect(fetchMonitorSnapshot()).rejects.toMatchObject({ kind: 'network' });

    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response(200, { ok: true })));
    await expect(fetchMonitorSnapshot()).rejects.toMatchObject({ kind: 'invalid_response' });
  });
});
