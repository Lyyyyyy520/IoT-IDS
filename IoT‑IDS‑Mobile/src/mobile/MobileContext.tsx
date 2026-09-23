import React, { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react';
import { AppState } from 'react-native';
import { mobileApi, MobileApiError, type MobileSession, type Overview } from './api';
import { clearLegacyAuthentication, readServerConfig, saveServerConfig, ServerConfigError, type ServerConfig } from './config';
import { secureStorage } from './storage';
import { TokenCoordinator, requiresRepair } from './tokenCoordinator';

export type Phase = 'restoring' | 'unpaired' | 'authenticated' | 'offline-with-session' | 're-pair-required';
type State = {
  phase: Phase; config: ServerConfig | null; session: MobileSession | null;
  overview: Overview | null; lastSynced: string | null; stale: boolean;
  busy: boolean; error: string | null; requestId: string | null; retryCount: number;
};
type Context = State & {
  pair: (address: string, insecureLan: boolean, code: string, name: string) => Promise<boolean>;
  sync: () => Promise<void>; logout: () => Promise<void>; resetClient: () => Promise<void>;
};

export const messageFor = (error: unknown): string => {
  if (error instanceof ServerConfigError) return error.message;
  if (!(error instanceof MobileApiError)) return '操作未完成，请重试';
  if (error.code === 'pairing_claim_rejected') return '配对码无效或不可用，请联系管理员获取新码';
  if (error.status === 429) return '尝试过于频繁，请稍后再试';
  if (error.code === 'https_required') return '服务器要求 HTTPS，请检查服务器地址';
  if (error.code === 'secure_storage_unavailable') return '安全存储不可用，请检查设备后重新配对';
  if (error.kind === 'network') return '无法连接服务器，请检查网络后重试';
  if (error.kind === 'invalid_response') return '服务器响应格式异常，请联系管理员';
  if (error.status === 503) return '服务尚未准备好，请稍后重试';
  if (error.status === 401) return '会话已失效，需要重新配对';
  if (error.status === 403) return '当前账号无权访问移动端';
  return '请求未完成，请联系管理员';
};

const initial: State = {
  phase: 'restoring', config: null, session: null, overview: null,
  lastSynced: null, stale: false, busy: false, error: null, requestId: null, retryCount: 0,
};
const MobileContext = createContext<Context | null>(null);

export function MobileProvider({ children }: { children: React.ReactNode }) {
  const [state, setState] = useState<State>(initial);
  const config = useRef<ServerConfig | null>(null);
  const coordinator = useRef<TokenCoordinator | null>(null);
  const generation = useRef(0);
  const inflight = useRef<Promise<void> | null>(null);
  const active = useRef(true);
  const failures = useRef(0);
  const requestAbort = useRef<AbortController | null>(null);

  const update = (patch: Partial<State>) => setState(previous => ({ ...previous, ...patch }));
  const makeCoordinator = (value: ServerConfig) => {
    config.current = value;
    coordinator.current = new TokenCoordinator(() => value);
    update({ config: value });
    return coordinator.current;
  };

  const performSync = useCallback(async (forceRefresh = false): Promise<void> => {
    if (inflight.current) return inflight.current;
    const auth = coordinator.current, server = config.current;
    if (!auth || !server) return;
    const epoch = generation.current;
    const abort = new AbortController();
    requestAbort.current = abort;
    const operation = (async () => {
      update({ busy: true });
      try {
        if (forceRefresh) await auth.refresh();
        const session = await auth.authorized(token => mobileApi.session(server, token, abort.signal));
        if (session.user.role !== 'user') throw new MobileApiError('http', 401, 'mobile_user_ineligible');
        const overview = await auth.authorized(token => mobileApi.overview(server, token, abort.signal));
        if (overview.user.user_id !== session.user.user_id) {
          throw new MobileApiError('invalid_response', 200, 'invalid_response');
        }
        if (epoch !== generation.current) return;
        failures.current = 0;
        update({ phase: 'authenticated', session, overview, lastSynced: overview.generated_at,
          stale: false, error: null, requestId: null, retryCount: 0 });
      } catch (error) {
        if (epoch !== generation.current) return;
        if (abort.signal.aborted) return;
        failures.current = Math.min(failures.current + 1, 4);
        const repair = requiresRepair(error);
        if (repair) {
          try { await auth.clearLocal(); } catch { /* already invalid */ }
          update({ phase: 're-pair-required', session: null, overview: null, lastSynced: null });
        } else {
          update(previousStateForError(state.phase, state.overview));
        }
        update({ stale: true, error: messageFor(error),
          requestId: error instanceof MobileApiError ? error.requestId ?? null : null,
          retryCount: failures.current });
      } finally { if (epoch === generation.current) update({ busy: false }); }
    })();
    inflight.current = operation;
    try { await operation; } finally {
      if (inflight.current === operation) inflight.current = null;
      if (requestAbort.current === abort) requestAbort.current = null;
    }
  }, [state.phase, state.overview]);

  useEffect(() => {
    let mounted = true;
    (async () => {
      try {
        await clearLegacyAuthentication();
        const server = await readServerConfig();
        if (!mounted) return;
        if (!server) { update({ phase: 'unpaired' }); return; }
        const auth = makeCoordinator(server);
        let refresh: string | null;
        try { refresh = await secureStorage.getRefresh(); }
        catch { throw new MobileApiError('invalid_response', 0, 'secure_storage_unavailable'); }
        if (!mounted) return;
        if (!refresh) { update({ phase: 'unpaired' }); return; }
        await auth.refresh();
        if (mounted) await performSync();
      } catch (error) {
        if (!mounted) return;
        const repair = requiresRepair(error);
        update({ phase: repair ? 're-pair-required' : 'offline-with-session', stale: true,
          error: messageFor(error), requestId: error instanceof MobileApiError ? error.requestId ?? null : null });
      }
    })();
    return () => { mounted = false; generation.current += 1; requestAbort.current?.abort(); };
  // Boot once. All later synchronization is explicit.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const listener = AppState.addEventListener('change', next => {
      const wasActive = active.current;
      active.current = next === 'active';
      if (!active.current) requestAbort.current?.abort();
      if (!wasActive && active.current && coordinator.current &&
          (state.phase === 'authenticated' || state.phase === 'offline-with-session')) {
        const pending = inflight.current;
        if (pending) void pending.then(() => performSync(true), () => performSync(true));
        else void performSync(true);
      }
    });
    const timer = setInterval(() => {
      if (active.current && state.phase === 'authenticated') void performSync();
    }, Math.min(30_000 * 2 ** state.retryCount, 120_000));
    return () => { listener.remove(); clearInterval(timer); };
  }, [performSync, state.phase, state.retryCount]);

  const pair = async (address: string, insecureLan: boolean, code: string, name: string) => {
    if (state.busy) return false;
    update({ busy: true, error: null, requestId: null });
    try {
      const server = await saveServerConfig(address, insecureLan);
      const auth = makeCoordinator(server);
      let clientId: string;
      try { clientId = await secureStorage.clientId(); }
      catch { throw new MobileApiError('invalid_response', 0, 'secure_storage_unavailable'); }
      const tokens = await mobileApi.claim(server, code, clientId, name.trim());
      await auth.acceptClaim(tokens);
      generation.current += 1;
      // Do not enter authenticated UI until session and overview both validate.
      update({ phase: 'restoring', overview: null, session: null });
      await performSync();
      return true;
    } catch (error) {
      update({ phase: 'unpaired', error: messageFor(error),
        requestId: error instanceof MobileApiError ? error.requestId ?? null : null });
      return false;
    } finally { update({ busy: false }); }
  };

  const logout = async () => {
    generation.current += 1;
    requestAbort.current?.abort();
    const auth = coordinator.current;
    coordinator.current = null;
    failures.current = 0;
    update({ phase: 'unpaired', overview: null, session: null, lastSynced: null,
      stale: false, error: null, requestId: null, retryCount: 0 });
    let reached = false;
    try { reached = auth ? await auth.logout() : false; }
    catch {
      update({ phase: 're-pair-required', error: '无法确认本机安全凭据已清除，请检查设备安全存储', requestId: null });
      return;
    }
    if (!reached) update({ error: '本机已注销；网络异常时服务端会话可能仍有效，请联系管理员撤销' });
  };
  const resetClient = async () => { await logout(); await secureStorage.clearClientId(); };
  return <MobileContext.Provider value={{ ...state, pair, sync: () => performSync(), logout, resetClient }}>
    {children}
  </MobileContext.Provider>;
}

function previousStateForError(phase: Phase, overview: Overview | null): Partial<State> {
  return { phase: phase === 'authenticated' && overview ? 'authenticated' : 'offline-with-session',
    stale: true, overview };
}

export function useMobile(): Context {
  const value = useContext(MobileContext);
  if (!value) throw new Error('MobileProvider is required');
  return value;
}
