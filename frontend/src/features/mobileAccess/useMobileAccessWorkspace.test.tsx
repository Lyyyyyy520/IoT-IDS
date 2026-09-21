import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  MobileAccessApiError,
  type MobileAccessApi,
  type MobileScopesResponse,
  type MobileSession,
  type MobileUser,
} from '../../api/v3MobileAccess';
import { listItem, listResponse } from '../../test/deviceFixtures';
import { useMobileAccessWorkspace } from './useMobileAccessWorkspace';

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((accept, fail) => { resolve = accept; reject = fail; });
  return { promise, resolve, reject };
}

function mobileUser(overrides: Partial<MobileUser> = {}): MobileUser {
  return {
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
    device_scope_count: 1,
    area_scope_count: 0,
    active_session_count: 1,
    revoked_session_count: 0,
    unused_pairing: { available: false, expires_at: null },
    ...overrides,
  };
}

function scopeResponse(overrides: Partial<MobileScopesResponse> = {}): MobileScopesResponse {
  return {
    user: { user_id: 7, username: 'resident_7', role: 'user' },
    scope_version: 2,
    scopes: [{
      scope_kind: 'device',
      scope_value: 'camera-01',
      scope_version: 2,
      created_at: '2026-09-21T01:00:00+00:00',
    }],
    ...overrides,
  };
}

function mobileSession(overrides: Partial<MobileSession> = {}): MobileSession {
  return {
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
    ...overrides,
  };
}

function fakeApi(overrides: Partial<MobileAccessApi> = {}): MobileAccessApi {
  return {
    listUsers: vi.fn().mockResolvedValue({
      items: [mobileUser()], total: 1, limit: 50, offset: 0, has_more: false,
    }),
    createUser: vi.fn().mockResolvedValue(mobileUser()),
    getUser: vi.fn().mockResolvedValue(mobileUser()),
    updateUser: vi.fn().mockResolvedValue(mobileUser({ profile_version: 2 })),
    getScopes: vi.fn().mockResolvedValue(scopeResponse()),
    replaceScopes: vi.fn().mockResolvedValue(scopeResponse({ scope_version: 3 })),
    startPairing: vi.fn().mockResolvedValue({
      pairing_id: 'pairing-7',
      pairing_code: 'ABC-123',
      expires_at: '2026-09-21T01:05:00+00:00',
      user: { user_id: 7, username: 'resident_7' },
    }),
    listSessions: vi.fn().mockResolvedValue({
      sessions: [mobileSession()],
      pagination: { limit: 25, offset: 0, total: 1, has_more: false },
    }),
    revokeSession: vi.fn().mockResolvedValue({
      session_id: 'session-7', revoked: true, already_revoked: false,
      revoked_reason: 'admin_revoked',
    }),
    ...overrides,
  };
}

function fakeDeviceApi() {
  return {
    listDevices: vi.fn().mockResolvedValue(listResponse([
      listItem({ device_id: 'camera-01', display_name: '东门摄像头', area_id: 'east-gate' }),
      listItem({ device_id: 'sensor-02', display_name: '温度传感器', area_id: 'home' }),
    ], { total: 2, limit: 100 })),
  };
}

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe('useMobileAccessWorkspace', () => {
  it('debounces user search and applies status/mobile-only filters with stable paging', async () => {
    const api = fakeApi();
    const deviceApi = fakeDeviceApi();
    const { result } = renderHook(() => useMobileAccessWorkspace({
      api, deviceApi, debounceMs: 1, pageSize: 20,
    }));
    await waitFor(() => expect(api.listUsers).toHaveBeenCalledTimes(1));
    act(() => result.current.setFilters({
      search: '住户', accountStatus: 'disabled', mobileOnly: 'true',
    }));
    await waitFor(() => expect(
      vi.mocked(api.listUsers).mock.calls.some(([query]) => query?.search === '住户'),
    ).toBe(true));
    expect(vi.mocked(api.listUsers).mock.calls.at(-1)?.[0]).toEqual({
      search: '住户',
      account_status: 'disabled',
      mobile_only: true,
      limit: 20,
      offset: 0,
    });
    act(() => result.current.setPage(2));
    await waitFor(() => expect(
      vi.mocked(api.listUsers).mock.calls.at(-1)?.[0]?.offset,
    ).toBe(40));
  });

  it('does not let a slow old user response replace newer filtered results', async () => {
    const old = deferred<{
      items: MobileUser[]; total: number; limit: number; offset: number; has_more: boolean;
    }>();
    const fresh = mobileUser({ user_id: 8, username: 'fresh', display_name: '新结果' });
    const api = fakeApi({
      listUsers: vi.fn()
        .mockReturnValueOnce(old.promise)
        .mockResolvedValueOnce({
          items: [fresh], total: 1, limit: 50, offset: 0, has_more: false,
        }),
    });
    const deviceApi = fakeDeviceApi();
    const { result } = renderHook(() => useMobileAccessWorkspace({
      api, deviceApi, debounceMs: 1,
    }));
    await waitFor(() => expect(api.listUsers).toHaveBeenCalledTimes(1));
    act(() => result.current.setFilters({ search: 'fresh' }));
    await waitFor(() => expect(result.current.users[0]?.user_id).toBe(8));
    await act(async () => {
      old.resolve({
        items: [mobileUser()], total: 1, limit: 50, offset: 0, has_more: false,
      });
      await old.promise;
    });
    expect(result.current.users[0]?.user_id).toBe(8);
  });

  it('loads real device/area options and a selected user scope without fixtures as fallback', async () => {
    const api = fakeApi();
    const deviceApi = fakeDeviceApi();
    const { result } = renderHook(() => useMobileAccessWorkspace({
      api, deviceApi,
    }));
    await waitFor(() => expect(result.current.devices).toHaveLength(2));
    act(() => result.current.selectUser(mobileUser()));
    await waitFor(() => expect(result.current.scopes?.scope_version).toBe(2));
    expect(result.current.deviceDraft).toEqual(['camera-01']);
    expect(result.current.areas).toEqual(['east-gate', 'home']);
    expect(api.getUser).toHaveBeenCalledWith(7, expect.any(AbortSignal));
  });

  it('preserves local scope edits on a version conflict and never retries the write', async () => {
    const replaceScopes = vi.fn().mockRejectedValue(
      new MobileAccessApiError('conflict', '范围版本冲突', {
        status: 409, code: 'mobile_scope_version_conflict',
      }),
    );
    const api = fakeApi({ replaceScopes });
    const deviceApi = fakeDeviceApi();
    const { result } = renderHook(() => useMobileAccessWorkspace({
      api, deviceApi,
    }));
    act(() => result.current.selectUser(mobileUser()));
    await waitFor(() => expect(result.current.scopes).not.toBeNull());
    act(() => {
      result.current.setDeviceDraft(['camera-01', 'sensor-02']);
      result.current.setAreaDraft(['home']);
    });
    await act(async () => {
      await expect(result.current.saveScopes()).rejects.toMatchObject({
        code: 'mobile_scope_version_conflict',
      });
    });
    expect(result.current.scopeConflict).toBe(true);
    expect(result.current.deviceDraft).toEqual(['camera-01', 'sensor-02']);
    expect(result.current.areaDraft).toEqual(['home']);
    expect(replaceScopes).toHaveBeenCalledTimes(1);
  });

  it('creates a mobile-only user without role/password input and loads an empty scope', async () => {
    const created = mobileUser({
      user_id: 9,
      username: 'resident_9',
      display_name: '住户九',
      device_scope_count: 0,
      area_scope_count: 0,
    });
    const api = fakeApi({
      createUser: vi.fn().mockResolvedValue(created),
      getUser: vi.fn().mockResolvedValue(created),
      getScopes: vi.fn().mockResolvedValue({
        user: { user_id: 9, username: 'resident_9', role: 'user' },
        scope_version: 0,
        scopes: [],
      }),
    });
    const deviceApi = fakeDeviceApi();
    const { result } = renderHook(() => useMobileAccessWorkspace({ api, deviceApi }));
    await act(async () => {
      await result.current.createUser('resident_9', '住户九');
    });
    expect(api.createUser).toHaveBeenCalledWith(
      { username: 'resident_9', display_name: '住户九' },
      expect.any(AbortSignal),
    );
    await waitFor(() => expect(result.current.scopes?.scopes).toEqual([]));
    expect(result.current.selectedUser).toMatchObject({
      user_id: 9, mobile_only: true, account_status: 'active',
    });
  });

  it('keeps pairing only in page memory, clears it on user switch, and expires it', async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-09-21T01:00:00Z'));
    const api = fakeApi({
      startPairing: vi.fn().mockResolvedValue({
        pairing_id: 'pairing-7',
        pairing_code: 'ONE-TIME',
        expires_at: '2026-09-21T01:00:02+00:00',
        user: { user_id: 7, username: 'resident_7' },
      }),
    });
    const deviceApi = fakeDeviceApi();
    const { result } = renderHook(() => useMobileAccessWorkspace({
      api, deviceApi, now: Date.now,
    }));
    act(() => result.current.selectUser(mobileUser()));
    await act(async () => { await result.current.startPairing(); });
    expect(result.current.pairing?.pairing_code).toBe('ONE-TIME');
    expect(window.localStorage).toHaveLength(0);
    await act(async () => {
      result.current.selectUser(mobileUser({ user_id: 8, username: 'resident_8' }));
      await Promise.resolve();
    });
    expect(result.current.pairing).toBeNull();

    act(() => result.current.selectUser(mobileUser()));
    await act(async () => { await result.current.startPairing(); });
    await act(async () => { vi.advanceTimersByTime(2_100); });
    expect(result.current.pairing).toBeNull();
  });

  it('paginates and filters sessions, then refreshes after idempotent revocation', async () => {
    const api = fakeApi({
      listSessions: vi.fn().mockResolvedValue({
        sessions: [mobileSession()],
        pagination: { limit: 25, offset: 25, total: 60, has_more: true },
      }),
    });
    const deviceApi = fakeDeviceApi();
    const { result } = renderHook(() => useMobileAccessWorkspace({
      api, deviceApi,
    }));
    await waitFor(() => expect(api.listSessions).toHaveBeenCalledTimes(1));
    act(() => result.current.setSessionPage(1));
    await waitFor(() => expect(
      vi.mocked(api.listSessions).mock.calls.at(-1)?.[0]?.offset,
    ).toBe(25));
    act(() => result.current.setSessionStatus('revoked'));
    await waitFor(() => expect(
      vi.mocked(api.listSessions).mock.calls.at(-1)?.[0]?.status,
    ).toBe('revoked'));
    expect(result.current.sessionTotal).toBe(60);
    expect(result.current.sessionHasMore).toBe(true);
    await act(async () => { await result.current.revokeSession('session-7'); });
    expect(api.revokeSession).toHaveBeenCalledWith(
      'session-7', 'admin_revoked', expect.any(AbortSignal),
    );
  });

  it('aborts user, device, detail, session, and mutation requests on unmount', async () => {
    const signals: AbortSignal[] = [];
    const never = new Promise<never>(() => {});
    const capture = (...args: unknown[]) => {
      const signal = args.at(-1);
      if (signal instanceof AbortSignal) signals.push(signal);
      return never;
    };
    const api = fakeApi({
      listUsers: vi.fn(capture),
      getUser: vi.fn(capture),
      getScopes: vi.fn(capture),
      listSessions: vi.fn(capture),
      createUser: vi.fn(capture),
    });
    const deviceApi = { listDevices: vi.fn(capture) };
    const { result, unmount } = renderHook(() => useMobileAccessWorkspace({ api, deviceApi }));
    await waitFor(() => expect(signals.length).toBeGreaterThanOrEqual(3));
    act(() => {
      result.current.selectUser(mobileUser());
      void result.current.createUser('resident_9', '住户九').catch(() => undefined);
    });
    await waitFor(() => expect(signals.length).toBeGreaterThanOrEqual(6));
    unmount();
    expect(signals.every((signal) => signal.aborted)).toBe(true);
  });
});
