import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  TrafficApiError,
  V3TrafficClient,
  parsePeersResponse,
  parseTrafficResponse,
} from './v3Traffic';
import { emptyTrafficResponse, peersResponse, trafficResponse } from '../test/trafficFixtures';

function response(status: number, body: unknown): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: vi.fn().mockResolvedValue(body),
  } as unknown as Response;
}
function errorBody(code: string) {
  return { error: { code, message: `message-${code}`, request_id: 'traffic-request-1' } };
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe('v3 traffic runtime contract', () => {
  it('parses complete traffic and peers responses, including legal no_samples', () => {
    expect(parseTrafficResponse(trafficResponse()).realtime).toMatchObject({
      available: true, tx_bytes_per_second: 128,
    });
    expect(parseTrafficResponse(emptyTrafficResponse())).toMatchObject({
      availability: { available: false, reason: 'no_samples' },
      realtime: { available: false, readiness: 'warming_up' },
      summary: null,
    });
    expect(parsePeersResponse(peersResponse()).pagination.total).toBe(2);
  });

  it('rejects malformed counters, timestamps, versions, and hidden peer IP leakage', () => {
    expect(() => parseTrafficResponse({ ...trafficResponse(), traffic_schema_version: 2 }))
      .toThrow(TrafficApiError);
    expect(() => parseTrafficResponse({
      ...trafficResponse(), series: [{ ...trafficResponse().series[0], tx_bytes: -1 }],
    })).toThrow('tx_bytes');
    expect(() => parsePeersResponse(peersResponse({ peers: [{
      ...peersResponse().peers[0], peer_ip_visible: false, peer_ip: '192.168.1.1',
    }] }))).toThrow('必须返回 null');
  });

  it('encodes device, UTC window, resolution, protocol and peer pagination', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response(200, trafficResponse()))
      .mockResolvedValueOnce(response(200, peersResponse()));
    vi.stubGlobal('fetch', fetchMock);
    const client = new V3TrafficClient();
    const controller = new AbortController();
    await client.getTraffic('camera / 01', {
      from: new Date('2026-09-21T17:00:00+08:00'),
      to: new Date('2026-09-21T18:00:00+08:00'),
      resolution: 'minute',
      protocol: 'TCP/IP',
    }, controller.signal);
    await client.getPeers('camera / 01', {
      from: new Date('2026-09-21T09:00:00Z'),
      to: new Date('2026-09-21T10:00:00Z'),
      direction: 'rx', protocol: 'UDP', sort: 'packets', limit: 20, offset: 40,
    });
    const trafficUrl = new URL(fetchMock.mock.calls[0][0], 'http://test');
    expect(trafficUrl.pathname).toBe('/api/v3/devices/camera%20%2F%2001/traffic');
    expect(trafficUrl.searchParams.get('from')).toBe('2026-09-21T09:00:00.000Z');
    expect(trafficUrl.searchParams.get('resolution')).toBe('minute');
    expect(trafficUrl.searchParams.get('protocol')).toBe('TCP/IP');
    expect((fetchMock.mock.calls[0][1] as RequestInit)).toMatchObject({
      credentials: 'include', signal: controller.signal,
    });
    const peersUrl = new URL(fetchMock.mock.calls[1][0], 'http://test');
    expect(peersUrl.searchParams.get('direction')).toBe('rx');
    expect(peersUrl.searchParams.get('sort')).toBe('packets');
    expect(peersUrl.searchParams.get('limit')).toBe('20');
    expect(peersUrl.searchParams.get('offset')).toBe('40');
  });

  it.each([
    [400, 'bad_request'], [401, 'unauthorized'], [403, 'forbidden'],
    [404, 'not_found'], [503, 'unavailable'],
  ] as const)('maps HTTP %s to %s without exposing internal response details', async (status, kind) => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response(status, errorBody(`status_${status}`))));
    await expect(new V3TrafficClient().getTraffic('camera-01', {
      from: new Date('2026-09-21T09:00:00Z'), to: new Date('2026-09-21T10:00:00Z'),
    })).rejects.toMatchObject({
      kind, status, code: `status_${status}`, requestId: 'traffic-request-1',
    });
  });

  it('distinguishes network, abort, and invalid JSON failures', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValueOnce(new TypeError('private network detail')));
    const query = {
      from: new Date('2026-09-21T09:00:00Z'), to: new Date('2026-09-21T10:00:00Z'),
    };
    await expect(new V3TrafficClient().getTraffic('camera-01', query))
      .rejects.toMatchObject({ kind: 'network' });
    const aborted = new DOMException('aborted', 'AbortError');
    vi.stubGlobal('fetch', vi.fn().mockRejectedValueOnce(aborted));
    await expect(new V3TrafficClient().getTraffic('camera-01', query))
      .rejects.toMatchObject({ kind: 'aborted' });
    vi.stubGlobal('fetch', vi.fn().mockResolvedValueOnce({
      ok: true, status: 200, json: vi.fn().mockRejectedValue(new SyntaxError('private')),
    }));
    await expect(new V3TrafficClient().getTraffic('camera-01', query))
      .rejects.toMatchObject({ kind: 'invalid_response' });
  });
});
