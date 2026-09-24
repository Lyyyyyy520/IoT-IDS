import React from 'react';
import { AppState, Text } from 'react-native';
import { act, render, screen, waitFor } from '@testing-library/react-native';
import { MobileProvider, useMobile } from './MobileContext';
import { mobileApi, MobileApiError } from './api';
import { clearLegacyAuthentication, readServerConfig } from './config';
import { secureStorage } from './storage';

jest.mock('./config', () => ({ ServerConfigError: class ServerConfigError extends Error {},
  clearLegacyAuthentication: jest.fn(), readServerConfig: jest.fn(), saveServerConfig: jest.fn() }));
jest.mock('./storage', () => ({ secureStorage: {
  getRefresh: jest.fn(), setRefresh: jest.fn(), clearRefresh: jest.fn(), clientId: jest.fn(), clearClientId: jest.fn(),
} }));
jest.mock('./api', () => {
  const actual = jest.requireActual('./api');
  return { ...actual, mobileApi: { claim: jest.fn(), refresh: jest.fn(), session: jest.fn(), overview: jest.fn(), logout: jest.fn(), notices: jest.fn(), supportContact: jest.fn() } };
});

const server = { baseUrl: 'https://example.test', insecureLan: false };
const rotated = { session_id: 's', access_token: 'access', refresh_token: 'rotated',
  access_expires_at: '2099-01-01T00:00:00Z', refresh_expires_at: '2099-02-01T00:00:00Z', token_generation: 2 };
const session = { session_id: 's', user: { user_id: 2, username: 'resident', role: 'user' },
  client_instance_id: 'client-1234', client_display_name: 'phone', token_generation: 2 };
const overview = { generated_at: '2026-09-21T04:00:00Z', user: { user_id: 2, username: 'resident' },
  devices: [], security_capability: { available: false, reason: 'incident_pipeline_not_ready' } };
let latest: ReturnType<typeof useMobile>;
function Probe() {
  const state = useMobile();
  latest = state;
  return <><Text testID="phase">{state.phase}</Text><Text testID="device-count">{state.overview?.devices.length ?? 'none'}</Text></>;
}

beforeEach(() => {
  jest.clearAllMocks();
  (clearLegacyAuthentication as jest.Mock).mockResolvedValue(undefined);
  (readServerConfig as jest.Mock).mockResolvedValue(server);
  (secureStorage.getRefresh as jest.Mock).mockResolvedValue(null);
  (secureStorage.setRefresh as jest.Mock).mockResolvedValue(undefined);
  (secureStorage.clearRefresh as jest.Mock).mockResolvedValue(undefined);
  (mobileApi.refresh as jest.Mock).mockResolvedValue(rotated);
  (mobileApi.session as jest.Mock).mockResolvedValue(session);
(mobileApi.overview as jest.Mock).mockResolvedValue(overview);
(mobileApi.logout as jest.Mock).mockResolvedValue({ session_id: 's', revoked: true, already_revoked: false });
});

describe('startup restoration', () => {
  it('clears old cookie auth and opens pairing when no refresh token exists', async () => {
    await render(<MobileProvider><Probe /></MobileProvider>);
    await waitFor(() => expect(screen.getByTestId('phase').props.children).toBe('unpaired'));
    expect(clearLegacyAuthentication).toHaveBeenCalledTimes(1);
    expect(mobileApi.refresh).not.toHaveBeenCalled();
  });
  it('rotates stored refresh, loads session and only authorized overview', async () => {
    (secureStorage.getRefresh as jest.Mock).mockResolvedValue('stored-secret');
    await render(<MobileProvider><Probe /></MobileProvider>);
    await waitFor(() => expect(screen.getByTestId('phase').props.children).toBe('authenticated'));
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 0)); });
    expect(secureStorage.setRefresh).toHaveBeenCalledWith('rotated');
    expect(mobileApi.session).toHaveBeenCalledWith(server, 'access', expect.anything());
    expect(mobileApi.overview).toHaveBeenCalledWith(server, 'access', expect.anything());
    expect(screen.getByTestId('device-count').props.children).toBe(0);
  });
  it('keeps SecureStore refresh on startup offline and never uses cached safety data', async () => {
    (secureStorage.getRefresh as jest.Mock).mockResolvedValue('stored-secret');
    (mobileApi.refresh as jest.Mock).mockRejectedValue(new MobileApiError('network', 0, 'network_unavailable'));
    await render(<MobileProvider><Probe /></MobileProvider>);
    await waitFor(() => expect(screen.getByTestId('phase').props.children).toBe('offline-with-session'));
    expect(secureStorage.clearRefresh).not.toHaveBeenCalled();
    expect(screen.getByTestId('device-count').props.children).toBe('none');
  });
  it('requires pairing after refresh replay and clears local credentials', async () => {
    (secureStorage.getRefresh as jest.Mock).mockResolvedValue('stored-secret');
    (mobileApi.refresh as jest.Mock).mockRejectedValue(new MobileApiError('http', 401, 'refresh_token_replay'));
    await render(<MobileProvider><Probe /></MobileProvider>);
    await waitFor(() => expect(screen.getByTestId('phase').props.children).toBe('re-pair-required'));
    expect(secureStorage.clearRefresh).toHaveBeenCalled();
  });
  it('does not mistake unreadable SecureStore for ordinary offline mode', async () => {
    (secureStorage.getRefresh as jest.Mock).mockRejectedValue(new Error('native unavailable'));
    await render(<MobileProvider><Probe /></MobileProvider>);
    await waitFor(() => expect(screen.getByTestId('phase').props.children).toBe('re-pair-required'));
    expect(mobileApi.refresh).not.toHaveBeenCalled();
  });
  it('does not replay refresh during React StrictMode effect remount', async () => {
    (secureStorage.getRefresh as jest.Mock).mockResolvedValue('stored-secret');
    await render(<React.StrictMode><MobileProvider><Probe /></MobileProvider></React.StrictMode>);
    await waitFor(() => expect(screen.getByTestId('phase').props.children).toBe('authenticated'));
    expect(mobileApi.refresh).toHaveBeenCalledTimes(1);
  });
  it('ignores a slow old overview response after logout and clears the local scope', async () => {
    (secureStorage.getRefresh as jest.Mock).mockResolvedValue('stored-secret');
    await render(<MobileProvider><Probe /></MobileProvider>);
    await waitFor(() => expect(screen.getByTestId('phase').props.children).toBe('authenticated'));
    let resolveOld!: (value: typeof overview) => void;
    (mobileApi.overview as jest.Mock).mockImplementationOnce(() => new Promise(resolve => { resolveOld = resolve; }));
    let signal: AbortSignal | undefined;
    await act(async () => {
      const syncing = latest.sync();
      for (let index = 0; index < 10 && !resolveOld; index += 1) await Promise.resolve();
      expect(resolveOld).toBeDefined();
      signal = (mobileApi.overview as jest.Mock).mock.calls[1][2] as AbortSignal;
      const loggingOut = latest.logout();
      resolveOld(overview);
      await Promise.all([syncing, loggingOut]);
    });
    await waitFor(() => expect(screen.getByTestId('phase').props.children).toBe('unpaired'));
    expect(signal?.aborted).toBe(true);
    expect(screen.getByTestId('device-count').props.children).toBe('none');
    expect(secureStorage.clearRefresh).toHaveBeenCalled();
  });
  it('pauses polling in background and refreshes tokens, session and overview on foreground', async () => {
    let onChange: ((next: 'active' | 'background') => void) | undefined;
    let tick: (() => void) | undefined;
    const subscription = jest.spyOn(AppState, 'addEventListener').mockImplementation((_kind, listener) => {
      onChange = listener as typeof onChange;
      return { remove: jest.fn() };
    });
    const interval = jest.spyOn(global, 'setInterval').mockImplementation(((handler: () => void) => {
      tick = handler;
      return 1 as unknown as ReturnType<typeof setInterval>;
    }) as typeof setInterval);
    (secureStorage.getRefresh as jest.Mock).mockResolvedValue('stored-secret');
    (mobileApi.overview as jest.Mock).mockResolvedValue({ ...overview,
      security_capability: { available: true, reason: 'incident_pipeline_available' } });
    (mobileApi.notices as jest.Mock).mockResolvedValue({ mode: 'snapshot', notices: [], tombstones: [], next_cursor: '0:0', snapshot_required: false });
    (mobileApi.supportContact as jest.Mock).mockResolvedValue({ available: false, reason: 'support_contact_not_configured' });
    const view = await render(<MobileProvider><Probe /></MobileProvider>);
    await waitFor(() => expect(screen.getByTestId('phase').props.children).toBe('authenticated'));
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 0)); });
    await act(async () => {
      onChange?.('background');
      tick?.();
    });
    expect(mobileApi.overview).toHaveBeenCalledTimes(1);
    expect(mobileApi.notices).not.toHaveBeenCalled();
    await act(async () => {
      onChange?.('active');
    });
    await waitFor(() => {
      expect(mobileApi.overview).toHaveBeenCalledTimes(2);
      expect(mobileApi.notices).toHaveBeenCalledTimes(1);
      expect(mobileApi.supportContact).toHaveBeenCalledTimes(1);
      expect(latest.busy).toBe(false);
    });
    expect(mobileApi.refresh).toHaveBeenCalledTimes(2);
    expect(mobileApi.session).toHaveBeenCalledTimes(2);
    await act(async () => { view.unmount(); await Promise.resolve(); });
    interval.mockRestore();
    subscription.mockRestore();
  });

});
