import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  INCIDENT_CSRF_HEADER,
  IncidentApiError,
  V3IncidentsClient,
  parseHelpDetail,
  parseIncidentDetail,
  parseIncidentList,
  parseSupportContact,
} from './v3Incidents';
import { helpDetail, incidentDetail, incidentListResponse } from '../test/incidentFixtures';

function response(status: number, body: unknown, headers: Record<string, string> = {}): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: vi.fn().mockResolvedValue(body),
    headers: { get: (name: string) => Object.entries(headers).find(([key]) => key.toLowerCase() === name.toLowerCase())?.[1] ?? null },
  } as unknown as Response;
}

const errorBody = (code: string) => ({ error: { code, message: `message-${code}`, request_id: 'req-error' } });

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

describe('v3 incident runtime contracts', () => {
  it('parses incident, help, and support responses and rejects malformed values', () => {
    expect(parseIncidentList(incidentListResponse())).toMatchObject({ event_cursor: 17, total: 1 });
    expect(parseIncidentDetail(incidentDetail)).toMatchObject({
      incident_id: incidentDetail.incident_id,
      devices: [{ incident_role: 'affected' }, { incident_role: 'suspected_source' }],
    });
    expect(parseHelpDetail(helpDetail).user_message).toContain('<script>');
    expect(parseSupportContact({ available: false, reason: 'support_contact_not_configured' })).toEqual({ available: false, reason: 'support_contact_not_configured' });
    expect(() => parseIncidentList({ ...incidentListResponse(), event_cursor: '17' })).toThrow(IncidentApiError);
    expect(() => parseIncidentDetail({ ...incidentDetail, source: 'gnn' })).toThrow('source');
  });

  it('encodes filters, timestamps, pagination, and identifiers in relative URLs', async () => {
    const fetchMock = vi.fn().mockResolvedValue(response(200, incidentListResponse(), { [INCIDENT_CSRF_HEADER]: 'csrf' }));
    vi.stubGlobal('fetch', fetchMock);
    await new V3IncidentsClient().listIncidents({
      search: '门厅', status: 'open', device_id: 'camera/01', from: '2026-09-23T00:00:00.000Z', limit: 25, offset: 50,
    });
    const url = String(fetchMock.mock.calls[0][0]);
    expect(url).toContain('/api/v3/incidents?');
    expect(url).toContain('search=%E9%97%A8%E5%8E%85');
    expect(url).toContain('device_id=camera%2F01');
    expect(url).toContain('from=2026-09-23T00%3A00%3A00.000Z');
  });

  it('keeps CSRF in memory and sends versions on every write', async () => {
    const storageSpy = vi.spyOn(Storage.prototype, 'setItem');
    const transitioned = { ...incidentDetail, status: 'acknowledged', incident_version: 2 };
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response(200, incidentListResponse(), { [INCIDENT_CSRF_HEADER]: 'memory-csrf' }))
      .mockResolvedValueOnce(response(200, transitioned));
    vi.stubGlobal('fetch', fetchMock);
    const client = new V3IncidentsClient();
    await client.listIncidents();
    const result = await client.transitionIncident(incidentDetail.incident_id, 'ack', {
      expected_incident_version: 1, public_progress: '管理员已开始核查。', admin_details: '内部记录',
    });
    const request = fetchMock.mock.calls[1][1] as RequestInit;
    expect(request.headers).toMatchObject({ 'Content-Type': 'application/json', [INCIDENT_CSRF_HEADER]: 'memory-csrf' });
    expect(JSON.parse(request.body as string)).toMatchObject({ expected_incident_version: 1 });
    expect(result.incident_version).toBe(2);
    expect(storageSpy).not.toHaveBeenCalled();
  });

  it('performs a safe GET before a write when CSRF is absent', async () => {
    const contact = { available: true, display_name: '社区管理员', phone: null, email: 'help@example.test', working_hours: null, public_note: null, enabled: true, config_version: 1, updated_by: 1, updated_at: '2026-09-23T02:00:00Z' };
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response(200, incidentListResponse({ items: [] }), { [INCIDENT_CSRF_HEADER]: 'fresh' }))
      .mockResolvedValueOnce(response(200, contact));
    vi.stubGlobal('fetch', fetchMock);
    await new V3IncidentsClient().updateSupportContact({ display_name: '社区管理员', email: 'help@example.test', enabled: true, expected_config_version: 0 });
    expect(fetchMock.mock.calls[0][0]).toBe('/api/v3/incidents?limit=1&offset=0');
    expect((fetchMock.mock.calls[1][1] as RequestInit).method).toBe('PUT');
  });

  it.each([
    [400, 'bad_request'], [401, 'unauthorized'], [403, 'forbidden'], [404, 'not_found'],
    [409, 'conflict'], [413, 'too_large'], [429, 'rate_limited'], [503, 'unavailable'],
  ] as const)('maps HTTP %s to %s without exposing raw failures', async (status, kind) => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response(status, errorBody(`status_${status}`))));
    await expect(new V3IncidentsClient().listIncidents()).rejects.toMatchObject({ kind, status, requestId: 'req-error' });
  });

  it('distinguishes network, abort, and invalid JSON failures', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValueOnce(new TypeError('private network detail')));
    await expect(new V3IncidentsClient().listIncidents()).rejects.toMatchObject({ kind: 'network' });
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, status: 200, headers: { get: () => null }, json: vi.fn().mockRejectedValue(new SyntaxError('private body')) }));
    await expect(new V3IncidentsClient().listIncidents()).rejects.toMatchObject({ kind: 'invalid_response' });
  });
});
