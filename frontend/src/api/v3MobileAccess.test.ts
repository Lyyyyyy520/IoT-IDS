import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
  MobileAccessApiError,
  createMobileAccessApi,
  parseMobileUser,
  parseSessionList,
} from './v3MobileAccess';

const user = {
  user_id: 7,
  username: 'resident_7',
  display_name: '住户七',
  mobile_only: true,
  account_status: 'active',
  profile_version: 1,
  created_at: '2026-09-21T01:00:00+00:00',
  updated_at: '2026-09-21T01:00:00+00:00',
  disabled_at: null,
  disabled_reason: null,
  device_scope_count: 0,
  area_scope_count: 0,
  active_session_count: 0,
  revoked_session_count: 0,
  unused_pairing: { available: false, expires_at: null },
};

const session = {
  session_id: 'session-7',
  user_id: 7,
  username: 'resident_7',
  client_instance_id: 'phone-instance',
  client_display_name: '住户手机',
  created_at: '2026-09-21T01:00:00+00:00',
  issued_at: '2026-09-21T01:00:00+00:00',
  last_seen_at: '2026-09-21T01:01:00+00:00',
  access_expires_at: '2026-09-21T01:16:00+00:00',
  refresh_expires_at: '2026-10-21T01:00:00+00:00',
  revoked_at: null,
  revoked_reason: null,
  token_generation: 1,
};

function response(body: unknown, status = 200, headers: Record<string, string> = {}) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json', ...headers },
  });
}

beforeEach(() => {
  window.localStorage.clear();
  vi.restoreAllMocks();
});

describe('v3 mobile access response parsing', () => {
  it('parses strict mobile-user and session responses', () => {
    expect(parseMobileUser(user)).toMatchObject({
      user_id: 7, username: 'resident_7', account_status: 'active',
    });
    expect(parseSessionList({
      sessions: [session],
      pagination: { limit: 25, offset: 0, total: 1, has_more: false },
    }).sessions[0]).toMatchObject({ session_id: 'session-7', user_id: 7 });
  });

  it('rejects malformed users and any token material in session responses', () => {
    expect(() => parseMobileUser({ ...user, profile_version: '1' })).toThrow(
      expect.objectContaining({ kind: 'invalid_response' }),
    );
    expect(() => parseSessionList({
      sessions: [{ ...session, refresh_token: 'must-not-cross-admin-api' }],
      pagination: { limit: 25, offset: 0, total: 1, has_more: false },
    })).toThrow(expect.objectContaining({ kind: 'invalid_response' }));
  });
});

describe('v3 mobile access API client', () => {
  it('reads CSRF from a safe GET, keeps it in memory, and sends it on writes', async () => {
    const setItem = vi.spyOn(Storage.prototype, 'setItem');
    const fetcher = vi.fn()
      .mockResolvedValueOnce(response(
        { items: [user], total: 1, limit: 1, offset: 0, has_more: false },
        200,
        { 'X-CSRF-Token': 'csrf-memory-only' },
      ))
      .mockResolvedValueOnce(response(user, 201));
    const api = createMobileAccessApi(fetcher as typeof fetch);

    await expect(api.createUser({
      username: 'resident_7', display_name: '住户七',
    })).resolves.toMatchObject({ user_id: 7 });

    expect(fetcher).toHaveBeenCalledTimes(2);
    expect(fetcher.mock.calls[0][0]).toBe('/api/v3/mobile-users?limit=1&offset=0');
    const writeInit = fetcher.mock.calls[1][1] as RequestInit;
    expect(fetcher.mock.calls[1][0]).toBe('/api/v3/mobile-users');
    expect(writeInit.credentials).toBe('include');
    expect(writeInit.headers).toMatchObject({
      'Content-Type': 'application/json',
      'X-CSRF-Token': 'csrf-memory-only',
    });
    expect(setItem).not.toHaveBeenCalled();
    expect(window.localStorage).toHaveLength(0);
  });

  it('reuses CSRF obtained by a successful user GET without an extra refresh', async () => {
    const fetcher = vi.fn()
      .mockResolvedValueOnce(response(user, 200, { 'X-CSRF-Token': 'csrf-from-get' }))
      .mockResolvedValueOnce(response({ ...user, display_name: '新名称', profile_version: 2 }));
    const api = createMobileAccessApi(fetcher as typeof fetch);
    await api.getUser(7);
    await api.updateUser(7, { expected_profile_version: 1, display_name: '新名称' });
    expect(fetcher).toHaveBeenCalledTimes(2);
    expect((fetcher.mock.calls[1][1] as RequestInit).headers).toMatchObject({
      'X-CSRF-Token': 'csrf-from-get',
    });
  });

  it('uses relative encoded URLs and bounded query values', async () => {
    const fetcher = vi.fn().mockResolvedValue(response({
      items: [], total: 0, limit: 25, offset: 25, has_more: false,
    }));
    const api = createMobileAccessApi(fetcher as typeof fetch);
    await api.listUsers({
      search: '住户 7', account_status: 'active', mobile_only: true, limit: 25, offset: 25,
    });
    const url = String(fetcher.mock.calls[0][0]);
    expect(url).toBe(
      '/api/v3/mobile-users?search=%E4%BD%8F%E6%88%B7+7&account_status=active&mobile_only=true&limit=25&offset=25',
    );
    expect(url.startsWith('/api/')).toBe(true);
  });

  it.each([
    [400, 'bad_request'],
    [401, 'unauthorized'],
    [403, 'forbidden'],
    [404, 'not_found'],
    [409, 'conflict'],
    [413, 'too_large'],
    [429, 'rate_limited'],
    [503, 'unavailable'],
  ] as const)('maps HTTP %s to %s without exposing internals', async (status, kind) => {
    const fetcher = vi.fn().mockResolvedValue(response({
      error: { code: 'stable_code', message: '可读错误', request_id: 'request-7' },
    }, status));
    const api = createMobileAccessApi(fetcher as typeof fetch);
    await expect(api.listUsers()).rejects.toMatchObject({
      kind, status, code: 'stable_code', requestId: 'request-7', message: '可读错误',
    });
  });

  it('separates abort, network, and invalid-response failures', async () => {
    const aborted = createMobileAccessApi(vi.fn().mockRejectedValue(
      new DOMException('cancelled', 'AbortError'),
    ) as typeof fetch);
    await expect(aborted.listUsers()).rejects.toMatchObject({ kind: 'aborted' });

    const network = createMobileAccessApi(vi.fn().mockRejectedValue(
      new TypeError('socket details must not be shown'),
    ) as typeof fetch);
    await expect(network.listUsers()).rejects.toMatchObject({
      kind: 'network', message: '无法连接后端服务',
    });

    const invalid = createMobileAccessApi(
      vi.fn().mockResolvedValue(new Response('not-json', { status: 200 })) as typeof fetch,
    );
    await expect(invalid.listUsers()).rejects.toBeInstanceOf(MobileAccessApiError);
    await expect(invalid.listUsers()).rejects.toMatchObject({ kind: 'invalid_response' });
  });
});
