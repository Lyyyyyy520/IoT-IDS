import type { ServerConfig } from './config';

type Json = Record<string, unknown>;
export type MobileUser = { user_id: number; username: string; role: 'user' };
export type Tokens = {
  session_id: string; access_token: string; refresh_token: string;
  access_expires_at: string; refresh_expires_at: string; user: MobileUser;
};
export type RefreshTokens = Omit<Tokens, 'user'> & { token_generation: number };
export type MobileSession = {
  session_id: string; user: MobileUser; client_instance_id: string;
  client_display_name: string; token_generation: number;
};
export type LogoutResult = { session_id: string; revoked: true; already_revoked: boolean };
export type MobileDevice = {
  device_id: string; display_name: string; device_type: string; area_id: string | null;
  connection_status: 'online' | 'stale' | 'offline' | 'unknown';
  operation_mode: 'active' | 'maintenance' | 'disabled';
  retired: boolean; retired_at: string | null; last_updated_at: string | null;
  availability_status: 'available' | 'delayed' | 'unavailable' | 'unknown' | 'maintenance' | 'disabled' | 'retired';
};
export type Overview = {
  generated_at: string; user: { user_id: number; username: string };
  devices: MobileDevice[]; security_capability: { available: boolean; reason: string };
};

export class MobileApiError extends Error {
  constructor(public kind: 'http' | 'network' | 'invalid_response', public status: number,
    public code: string, public requestId?: string) { super(code); this.name = 'MobileApiError'; }
}

const obj = (value: unknown): Json => {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('object');
  return value as Json;
};
const str = (value: unknown): string => { if (typeof value !== 'string') throw new Error('string'); return value; };
const nonempty = (value: unknown): string => { const text = str(value); if (!text) throw new Error('empty'); return text; };
const int = (value: unknown): number => { if (!Number.isSafeInteger(value) || (value as number) < 0) throw new Error('integer'); return value as number; };
const iso = (value: unknown): string => {
  const text = str(value);
  if (!/\dT\d/.test(text) || !/(Z|[+-]\d{2}:\d{2})$/.test(text) || Number.isNaN(Date.parse(text))) throw new Error('date');
  return text;
};
const nullable = <T>(value: unknown, parse: (v: unknown) => T): T | null => value === null ? null : parse(value);
const oneOf = <T extends string>(value: unknown, options: readonly T[]): T => {
  if (!options.includes(value as T)) throw new Error('enum'); return value as T;
};
const parseUser = (value: unknown): MobileUser => {
  const x = obj(value); return { user_id: int(x.user_id), username: str(x.username), role: oneOf(x.role, ['user']) };
};
export function parseTokens(value: unknown): Tokens {
  const x = obj(value); return {
    session_id: nonempty(x.session_id), access_token: nonempty(x.access_token), refresh_token: nonempty(x.refresh_token),
    access_expires_at: iso(x.access_expires_at), refresh_expires_at: iso(x.refresh_expires_at), user: parseUser(x.user),
  };
}
export function parseRefreshTokens(value: unknown): RefreshTokens {
  const x = obj(value); return {
    session_id: nonempty(x.session_id), access_token: nonempty(x.access_token), refresh_token: nonempty(x.refresh_token),
    access_expires_at: iso(x.access_expires_at), refresh_expires_at: iso(x.refresh_expires_at),
    token_generation: int(x.token_generation),
  };
}
export function parseSession(value: unknown): MobileSession {
  const x = obj(value); return {
    session_id: nonempty(x.session_id), user: parseUser(x.user), client_instance_id: nonempty(x.client_instance_id),
    client_display_name: str(x.client_display_name), token_generation: int(x.token_generation),
  };
}
export function parseLogout(value: unknown): LogoutResult {
  const x = obj(value);
  if (x.revoked !== true || typeof x.already_revoked !== 'boolean') throw new Error('logout');
  return { session_id: nonempty(x.session_id), revoked: true, already_revoked: x.already_revoked };
}
export function parseOverview(value: unknown): Overview {
  const x = obj(value);
  if (!Array.isArray(x.devices)) throw new Error('devices');
  const user = obj(x.user), capability = obj(x.security_capability);
  if (typeof capability.available !== 'boolean') throw new Error('capability');
  return {
    generated_at: iso(x.generated_at), user: { user_id: int(user.user_id), username: str(user.username) },
    devices: x.devices.map((item: unknown): MobileDevice => {
      const d = obj(item);
      if (typeof d.retired !== 'boolean') throw new Error('retired');
      return {
        device_id: str(d.device_id), display_name: str(d.display_name), device_type: str(d.device_type),
        area_id: nullable(d.area_id, str),
        connection_status: oneOf(d.connection_status, ['online', 'stale', 'offline', 'unknown']),
        operation_mode: oneOf(d.operation_mode, ['active', 'maintenance', 'disabled']),
        retired: d.retired, retired_at: nullable(d.retired_at, iso),
        last_updated_at: nullable(d.last_updated_at, iso),
        availability_status: oneOf(d.availability_status, ['available', 'delayed', 'unavailable', 'unknown', 'maintenance', 'disabled', 'retired']),
      };
    }),
    security_capability: { available: capability.available, reason: str(capability.reason) },
  };
}

async function request<T>(config: ServerConfig, path: string, parse: (v: unknown) => T,
  options: { body?: object; accessToken?: string; signal?: AbortSignal } = {}): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${config.baseUrl}${path}`, {
      method: options.body ? 'POST' : 'GET', credentials: 'omit', signal: options.signal,
      headers: { ...(options.body ? { 'Content-Type': 'application/json' } : {}),
        ...(options.accessToken ? { Authorization: `Bearer ${options.accessToken}` } : {}) },
      ...(options.body ? { body: JSON.stringify(options.body) } : {}),
    });
  } catch (error) {
    if (options.signal?.aborted) throw error;
    throw new MobileApiError('network', 0, 'network_unavailable');
  }
  let data: unknown;
  try { data = await response.json(); } catch { throw new MobileApiError('invalid_response', response.status, 'invalid_response'); }
  if (!response.ok) {
    try {
      const e = obj(obj(data).error);
      throw new MobileApiError('http', response.status, str(e.code), str(e.request_id));
    } catch (error) {
      if (error instanceof MobileApiError) throw error;
      throw new MobileApiError('invalid_response', response.status, 'invalid_response');
    }
  }
  try { return parse(data); } catch { throw new MobileApiError('invalid_response', response.status, 'invalid_response'); }
}

export const mobileApi = {
  claim: (config: ServerConfig, pairing_code: string, client_instance_id: string, client_display_name: string, signal?: AbortSignal) =>
    request(config, '/api/v3/pairing/claim', parseTokens, { body: { pairing_code, client_instance_id, client_display_name }, signal }),
  refresh: (config: ServerConfig, refresh_token: string, signal?: AbortSignal) =>
    request(config, '/api/v3/mobile/token/refresh', parseRefreshTokens, { body: { refresh_token }, signal }),
  session: (config: ServerConfig, accessToken: string, signal?: AbortSignal) =>
    request(config, '/api/v3/mobile/session', parseSession, { accessToken, signal }),
  overview: (config: ServerConfig, accessToken: string, signal?: AbortSignal) =>
    request(config, '/api/v3/mobile/overview', parseOverview, { accessToken, signal }),
  logout: (config: ServerConfig, accessToken: string, signal?: AbortSignal) =>
    request(config, '/api/v3/mobile/logout', parseLogout, { body: {}, accessToken, signal }),
};
