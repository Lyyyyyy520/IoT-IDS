import AsyncStorage from '@react-native-async-storage/async-storage';
import { mobileApi, MobileApiError, parseLogout, parseOverview, parseRefreshTokens, parseSession, parseTokens } from './api';
import { clearLegacyAuthentication, saveServerConfig, validateServerUrl } from './config';
import { formatPairingCode, normalizePairingCode } from './PairingScreen';
import { messageFor } from './MobileContext';
import { TokenCoordinator } from './tokenCoordinator';

jest.mock('@react-native-async-storage/async-storage', () => ({
  __esModule: true, default: { multiRemove: jest.fn(), getItem: jest.fn(), setItem: jest.fn() },
}));

const config = { baseUrl: 'https://example.test', insecureLan: false };
const claim = {
  session_id: 'session-1', access_token: 'access-secret', refresh_token: 'refresh-secret',
  access_expires_at: '2099-01-01T00:00:00Z', refresh_expires_at: '2099-02-01T00:00:00Z',
  user: { user_id: 2, username: 'resident', role: 'user' as const },
};
const rotated = { ...claim, access_token: 'new-access', refresh_token: 'new-refresh', token_generation: 2 };

describe('pairing and server configuration', () => {
  it('normalizes pasted code without storing it', () => {
    expect(normalizePairingCode('abcd- efgh')).toBe('ABCDEFGH');
    expect(formatPairingCode('abcd efgh')).toBe('ABCD-EFGH');
  });
  it('rejects credentials, paths, fragments and HTTP outside explicit development mode', () => {
    expect(validateServerUrl('https://example.test/', false)).toBe('https://example.test');
    expect(() => validateServerUrl('https://u:p@example.test', false)).toThrow();
    expect(() => validateServerUrl('https://example.test/api', false)).toThrow();
    expect(() => validateServerUrl('https://example.test/#secret', false)).toThrow();
    expect(() => validateServerUrl('http://example.test', false)).toThrow();
    expect(() => validateServerUrl('http://example.test', true)).toThrow();
    expect(validateServerUrl('http://192.168.1.20:5000/', true)).toBe('http://192.168.1.20:5000');
  });
  it('removes legacy auth keys and only persists public server config', async () => {
    await clearLegacyAuthentication();
    await saveServerConfig('https://example.test/', false);
    expect(AsyncStorage.multiRemove).toHaveBeenCalledWith(expect.arrayContaining(['iot_ids_session_cookie', 'iot_ids_refresh_token']));
    expect(AsyncStorage.setItem).toHaveBeenCalledWith('iot_ids_mobile_server_v1', JSON.stringify(config));
  });
});

describe('strict mobile API', () => {
  it('parses actual claim, rotated refresh, session and scoped overview shapes', () => {
    expect(parseTokens(claim).user.role).toBe('user');
    expect(parseRefreshTokens(rotated).token_generation).toBe(2);
    expect(parseSession({ session_id: 's', user: claim.user, client_instance_id: 'client-1234',
      client_display_name: 'phone', token_generation: 1 }).user.role).toBe('user');
    const overview = parseOverview({ generated_at: '2026-09-21T04:00:00Z', user: { user_id: 2, username: 'resident' },
      devices: [], security_capability: { available: false, reason: 'incident_pipeline_not_ready' } });
    expect(overview.devices).toEqual([]);
    expect(() => parseOverview({ ...overview, devices: [{ device_id: 'a' }] })).toThrow();
    expect(() => parseTokens({ ...claim, user: { ...claim.user, role: 'admin' } })).toThrow();
    expect(() => parseTokens({ ...claim, refresh_token: '' })).toThrow();
    expect(parseLogout({ session_id: 's', revoked: true, already_revoked: false }).revoked).toBe(true);
    expect(() => parseLogout({ revoked: false })).toThrow();
  });
  it('uses bearer only, omits cookies and protects invalid responses', async () => {
    const fetchMock = jest.fn().mockResolvedValueOnce({ ok: true, status: 200, json: async () => ({
      session_id: 's', user: claim.user, client_instance_id: 'client-1234', client_display_name: 'phone', token_generation: 1,
    }) }).mockResolvedValueOnce({ ok: true, status: 200, json: async () => ({ devices: [] }) });
    global.fetch = fetchMock as typeof fetch;
    await mobileApi.session(config, 'access-secret');
    expect(fetchMock.mock.calls[0][0]).toBe('https://example.test/api/v3/mobile/session');
    expect(fetchMock.mock.calls[0][1].credentials).toBe('omit');
    expect(fetchMock.mock.calls[0][1].headers).toEqual({ Authorization: 'Bearer access-secret' });
    await expect(mobileApi.overview(config, 'access-secret')).rejects.toMatchObject({ kind: 'invalid_response' });
  });
  it('parses v3 errors and request ids, including rate limits', async () => {
    global.fetch = jest.fn().mockResolvedValue({ ok: false, status: 429,
      json: async () => ({ error: { code: 'mobile_rate_limited', message: 'private', request_id: 'req-1' } }) }) as typeof fetch;
    await expect(mobileApi.claim(config, 'CODE', 'client-1234', 'phone')).rejects.toMatchObject({
      status: 429, code: 'mobile_rate_limited', requestId: 'req-1',
    });
  });
  it('maps private backend errors to safe user messages', () => {
    expect(messageFor(new MobileApiError('http', 401, 'pairing_claim_rejected'))).toMatch(/无效或不可用/);
    expect(messageFor(new MobileApiError('http', 429, 'mobile_rate_limited'))).toMatch(/稍后/);
    expect(messageFor(new MobileApiError('http', 503, 'mobile_store_unavailable'))).toMatch(/尚未准备/);
    expect(messageFor(new MobileApiError('http', 400, 'https_required'))).toMatch(/HTTPS/);
    expect(messageFor(new MobileApiError('network', 0, 'network_unavailable'))).toMatch(/无法连接/);
  });
});

function harness() {
  let stored: string | null = 'refresh-secret';
  const storage = {
    getRefresh: jest.fn(async () => stored),
    setRefresh: jest.fn(async (value: string) => { stored = value; }),
    clearRefresh: jest.fn(async () => { stored = null; }),
  };
  const api = {
    ...mobileApi,
    refresh: jest.fn(async () => rotated),
    logout: jest.fn(async () => ({ session_id: 'session-1', revoked: true as const, already_revoked: false })),
  };
  const coordinator = new TokenCoordinator(() => config, storage, api);
  return { coordinator, storage, api, getStored: () => stored };
}

describe('single-flight token lifecycle', () => {
  it('keeps access only in memory and refresh only in secure storage', async () => {
    const h = harness();
    await h.coordinator.acceptClaim(claim);
    expect(h.storage.setRefresh).toHaveBeenCalledWith('refresh-secret');
    expect(h.coordinator.getAccessToken()).toBe('access-secret');
    expect(AsyncStorage.setItem).not.toHaveBeenCalledWith(expect.stringContaining('token'), expect.anything());
  });
  it('shares one refresh among concurrent access expiry requests', async () => {
    const h = harness();
    const values = await Promise.all([h.coordinator.authorized(async token => token),
      h.coordinator.authorized(async token => token), h.coordinator.authorized(async token => token)]);
    expect(h.api.refresh).toHaveBeenCalledTimes(1);
    expect(values).toEqual(['new-access', 'new-access', 'new-access']);
    expect(h.getStored()).toBe('new-refresh');
  });
  it('rotates on 401 then retries once, but never refreshes on 403', async () => {
    const h = harness(); await h.coordinator.acceptClaim(claim);
    const call = jest.fn().mockRejectedValueOnce(new MobileApiError('http', 401, 'mobile_token_expired'))
      .mockResolvedValueOnce('ok');
    await expect(h.coordinator.authorized(call)).resolves.toBe('ok');
    expect(call).toHaveBeenCalledTimes(2);
    expect(h.api.refresh).toHaveBeenCalledTimes(1);
    await expect(h.coordinator.authorized(() => Promise.reject(new MobileApiError('http', 403, 'forbidden')))).rejects.toMatchObject({ status: 403 });
    expect(h.api.refresh).toHaveBeenCalledTimes(1);
  });
  it('clears credentials when the single retry reports a revoked session', async () => {
    const h = harness(); await h.coordinator.acceptClaim(claim);
    const call = jest.fn().mockRejectedValueOnce(new MobileApiError('http', 401, 'mobile_token_expired'))
      .mockRejectedValueOnce(new MobileApiError('http', 401, 'mobile_session_revoked'));
    await expect(h.coordinator.authorized(call)).rejects.toMatchObject({ code: 'mobile_session_revoked' });
    expect(call).toHaveBeenCalledTimes(2);
    expect(h.getStored()).toBeNull();
    expect(h.coordinator.getAccessToken()).toBeNull();
  });
  it('retains a session on network error, but clears on replay or revocation', async () => {
    const h = harness();
    h.api.refresh.mockRejectedValueOnce(new MobileApiError('network', 0, 'network_unavailable'));
    await expect(h.coordinator.refresh()).rejects.toMatchObject({ kind: 'network' });
    expect(h.getStored()).toBe('refresh-secret');
    h.api.refresh.mockRejectedValueOnce(new MobileApiError('http', 401, 'refresh_token_replay'));
    await expect(h.coordinator.refresh()).rejects.toMatchObject({ code: 'refresh_token_replay' });
    expect(h.getStored()).toBeNull();
  });
  it('clears instead of reusing an old refresh token if secure rotation fails', async () => {
    const h = harness(); h.storage.setRefresh.mockRejectedValueOnce(new Error('storage failed'));
    await expect(h.coordinator.refresh()).rejects.toMatchObject({ code: 'secure_storage_unavailable' });
    expect(h.getStored()).toBeNull();
    expect(h.coordinator.getAccessToken()).toBeNull();
  });
  it('reports secure storage failure even when deleting an unusable rotated token also fails', async () => {
    const h = harness();
    h.storage.setRefresh.mockRejectedValueOnce(new Error('write failed'));
    h.storage.clearRefresh.mockRejectedValueOnce(new Error('delete failed'));
    await expect(h.coordinator.refresh()).rejects.toMatchObject({ code: 'secure_storage_unavailable' });
    expect(h.coordinator.getAccessToken()).toBeNull();
  });
  it('does not authenticate or fall back to AsyncStorage when claim storage fails', async () => {
    const h = harness(); h.storage.setRefresh.mockRejectedValueOnce(new Error('storage failed'));
    await expect(h.coordinator.acceptClaim(claim)).rejects.toMatchObject({ code: 'secure_storage_unavailable' });
    expect(h.coordinator.getAccessToken()).toBeNull();
    expect(h.api.logout).toHaveBeenCalledWith(config, 'access-secret');
    expect(AsyncStorage.setItem).not.toHaveBeenCalledWith(expect.stringContaining('refresh'), expect.anything());
  });
  it('prevents an in-flight refresh from restoring credentials after logout', async () => {
    const h = harness();
    let release!: (value: typeof rotated) => void;
    h.api.refresh.mockImplementationOnce(() => new Promise(resolve => { release = resolve; }));
    const pending = h.coordinator.refresh();
    await Promise.resolve();
    await h.coordinator.clearLocal();
    release(rotated);
    await expect(pending).rejects.toMatchObject({ code: 're_pair_required' });
    expect(h.getStored()).toBeNull();
    expect(h.coordinator.getAccessToken()).toBeNull();
  });
  it('clears local credentials even when logout cannot reach the server', async () => {
    const h = harness(); await h.coordinator.acceptClaim(claim);
    h.api.logout.mockRejectedValueOnce(new MobileApiError('network', 0, 'network_unavailable'));
    await expect(h.coordinator.logout()).resolves.toBe(false);
    expect(h.getStored()).toBeNull(); expect(h.coordinator.getAccessToken()).toBeNull();
  });
});
