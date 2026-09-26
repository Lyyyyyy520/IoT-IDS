import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  CSRF_HEADER,
  DeviceApiError,
  V3DevicesClient,
  parseDeviceDetail,
  parseDeviceListResponse,
} from './v3Devices';
import { detailEnvelope, deviceDetail, listResponse } from '../test/deviceFixtures';

function response(status: number, body: unknown, headers: Record<string, string> = {}): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: vi.fn().mockResolvedValue(body),
    headers: {
      get: (name: string) => Object.entries(headers)
        .find(([key]) => key.toLowerCase() === name.toLowerCase())?.[1] ?? null,
    },
  } as unknown as Response;
}

function errorBody(code = 'failure') {
  return { error: { code, message: `message-${code}`, request_id: 'request-7' } };
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe('v3 devices runtime contract', () => {
  it('parses real list and detail response shapes', () => {
    expect(parseDeviceListResponse(listResponse()).items[0]).toMatchObject({
      device_id: 'camera-01',
      profile_version: 3,
      connection_status: 'online',
    });
    expect(parseDeviceDetail(deviceDetail)).toMatchObject({
      mac_address: 'AA:BB:CC:DD:EE:01',
      references: { state_observations: 4 },
      can_delete: false,
    });
  });

  it('rejects malformed success responses instead of crashing the page', () => {
    expect(() => parseDeviceListResponse({ items: [{}], total: 1, limit: 24, offset: 0 }))
      .toThrow(DeviceApiError);
    expect(() => parseDeviceDetail({ ...deviceDetail, profile_version: '3' }))
      .toThrow('profile_version');
  });

  it('captures CSRF from a successful GET and sends it on writes without persistent storage', async () => {
    const storageSpy = vi.spyOn(Storage.prototype, 'setItem');
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response(200, listResponse(), { [CSRF_HEADER]: 'csrf-memory-only' }))
      .mockResolvedValueOnce(response(201, detailEnvelope()));
    vi.stubGlobal('fetch', fetchMock);
    const client = new V3DevicesClient();

    await client.listDevices();
    await client.createDevice({
      device_id: 'camera-01',
      mac: 'AA:BB:CC:DD:EE:01',
      display_name: '东门摄像头',
      device_type: 'camera',
      area_id: 'east-gate',
      importance: 'high',
      profile_source: 'physical',
    });

    const write = fetchMock.mock.calls[1][1] as RequestInit;
    expect(write.method).toBe('POST');
    expect(write.credentials).toBe('include');
    expect(write.headers).toMatchObject({
      'Content-Type': 'application/json',
      [CSRF_HEADER]: 'csrf-memory-only',
    });
    expect(storageSpy).not.toHaveBeenCalled();
  });

  it('performs one safe GET before a write when the in-memory CSRF token is absent', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response(200, listResponse(), { [CSRF_HEADER]: 'fresh-token' }))
      .mockResolvedValueOnce(response(201, detailEnvelope()));
    vi.stubGlobal('fetch', fetchMock);
    const client = new V3DevicesClient();

    await client.createDevice({
      device_id: 'camera-01', mac: 'AA:BB:CC:DD:EE:01', display_name: '东门摄像头',
      device_type: 'camera', area_id: null, importance: 'normal', profile_source: 'physical',
    });

    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(fetchMock.mock.calls[0][0]).toBe('/api/v3/devices?limit=1&offset=0');
    expect((fetchMock.mock.calls[0][1] as RequestInit).method).toBe('GET');
    expect((fetchMock.mock.calls[1][1] as RequestInit).headers).toMatchObject({
      [CSRF_HEADER]: 'fresh-token',
    });
  });

  it('does not send a write if the safe GET omits the CSRF header', async () => {
    const fetchMock = vi.fn().mockResolvedValue(response(200, listResponse()));
    vi.stubGlobal('fetch', fetchMock);
    const client = new V3DevicesClient();

    await expect(client.restoreDevice('camera-01', 3)).rejects.toMatchObject({
      kind: 'invalid_response',
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it.each([
    [400, 'bad_request'],
    [401, 'unauthorized'],
    [403, 'forbidden'],
    [404, 'not_found'],
    [409, 'conflict'],
    [413, 'too_large'],
    [503, 'unavailable'],
  ] as const)('maps HTTP %s to %s with request metadata', async (status, kind) => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response(status, errorBody(`status_${status}`))));
    const client = new V3DevicesClient();
    await expect(client.listDevices()).rejects.toMatchObject({
      kind,
      status,
      code: `status_${status}`,
      requestId: 'request-7',
    });
  });

  it('preserves stable conflict codes for identity, version, history, and confirmation errors', async () => {
    for (const code of [
      'device_id_conflict',
      'device_identity_conflict',
      'profile_version_conflict',
      'device_has_history',
      'confirmation_mismatch',
    ]) {
      vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response(409, errorBody(code))));
      const client = new V3DevicesClient();
      await expect(client.listDevices()).rejects.toMatchObject({ kind: 'conflict', code });
    }
  });

  it('distinguishes network failures and invalid JSON responses', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('offline details')));
    await expect(new V3DevicesClient().listDevices()).rejects.toMatchObject({ kind: 'network' });

    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: true, status: 200, headers: { get: () => null },
      json: vi.fn().mockRejectedValue(new SyntaxError('private parser detail')),
    }));
    await expect(new V3DevicesClient().listDevices()).rejects.toMatchObject({
      kind: 'invalid_response',
      message: '服务返回了无法解析的 JSON',
    });
  });

  it('sends expected profile_version and parses server-returned state without guessing', async () => {
    const updated = { ...deviceDetail, profile_version: 9, display_name: '服务器名称' };
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response(200, listResponse(), { [CSRF_HEADER]: 'csrf' }))
      .mockResolvedValueOnce(response(200, detailEnvelope(updated)));
    vi.stubGlobal('fetch', fetchMock);
    const client = new V3DevicesClient();
    await client.listDevices();
    const result = await client.updateDevice('camera-01', {
      display_name: '本地草稿', expected_profile_version: 3,
    });
    const body = JSON.parse((fetchMock.mock.calls[1][1] as RequestInit).body as string);
    expect(body.expected_profile_version).toBe(3);
    expect(result.profile_version).toBe(9);
    expect(result.display_name).toBe('服务器名称');
  });
});
