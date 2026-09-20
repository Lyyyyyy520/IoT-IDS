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
