export const MOBILE_USERS_ENDPOINT = '/api/v3/mobile-users';
export const MOBILE_SESSIONS_ENDPOINT = '/api/v3/mobile-sessions';
export const MOBILE_CSRF_HEADER = 'X-CSRF-Token';

export type MobileAccountStatus = 'active' | 'disabled';
export type MobileScopeKind = 'device' | 'area';
export type MobileSessionStatus = 'active' | 'revoked' | 'expired';

export interface MobileUser {
  user_id: number;
  username: string;
  display_name: string;
  mobile_only: boolean;
  account_status: MobileAccountStatus;
  profile_version: number;
  created_at: string | null;
  updated_at: string | null;
  disabled_at: string | null;
  disabled_reason: string | null;
  device_scope_count: number;
  area_scope_count: number;
  active_session_count: number;
  revoked_session_count: number;
  unused_pairing: { available: boolean; expires_at: string | null };
}

export interface MobileUserListResponse {
  items: MobileUser[];
  total: number;
  limit: number;
  offset: number;
  has_more: boolean;
}

export interface MobileScope {
  scope_kind: MobileScopeKind;
  scope_value: string;
  scope_version: number;
  created_at: string;
}

export interface MobileScopesResponse {
  user: { user_id: number; username: string; role: 'user' };
  scope_version: number;
  scopes: MobileScope[];
}

export interface PairingStartResponse {
  pairing_id: string;
  pairing_code: string;
  expires_at: string;
  user: { user_id: number; username: string };
}

export interface MobileSession {
  session_id: string;
  user_id: number;
  username: string | null;
  client_instance_id: string;
  client_display_name: string;
  created_at: string;
  issued_at: string;
  last_seen_at: string;
  access_expires_at: string;
  refresh_expires_at: string;
  revoked_at: string | null;
  revoked_reason: string | null;
  token_generation: number;
}

export interface MobileSessionListResponse {
  sessions: MobileSession[];
  pagination: { limit: number; offset: number; total: number; has_more: boolean };
}

export type MobileAccessErrorKind =
  | 'bad_request' | 'unauthorized' | 'forbidden' | 'not_found' | 'conflict'
  | 'too_large' | 'rate_limited' | 'unavailable' | 'network'
  | 'invalid_response' | 'http' | 'aborted';

export class MobileAccessApiError extends Error {
  readonly kind: MobileAccessErrorKind;
  readonly status: number | null;
  readonly code: string | null;
  readonly requestId: string | null;

  constructor(
    kind: MobileAccessErrorKind,
    message: string,
    options: { status?: number; code?: string; requestId?: string; cause?: unknown } = {},
  ) {
    super(message, { cause: options.cause });
    this.name = 'MobileAccessApiError';
    this.kind = kind;
    this.status = options.status ?? null;
    this.code = options.code ?? null;
    this.requestId = options.requestId ?? null;
  }
}

type JsonObject = Record<string, unknown>;

function invalid(message: string): never {
  throw new MobileAccessApiError('invalid_response', message);
}

function object(value: unknown, label: string): JsonObject {
  if (!value || typeof value !== 'object' || Array.isArray(value)) invalid(`${label} 必须是对象`);
  return value as JsonObject;
}

function text(value: unknown, label: string, nullable = false): string | null {
  if (nullable && value === null) return null;
  if (typeof value !== 'string') invalid(`${label} 必须是字符串`);
  return value as string;
}

function integer(value: unknown, label: string): number {
  if (!Number.isSafeInteger(value) || Number(value) < 0) invalid(`${label} 必须是非负整数`);
  return Number(value);
}

function boolean(value: unknown, label: string): boolean {
  if (typeof value !== 'boolean') invalid(`${label} 必须是布尔值`);
  return value;
}

function oneOf<T extends string>(value: unknown, values: readonly T[], label: string): T {
  if (typeof value !== 'string' || !values.includes(value as T)) invalid(`${label} 无效`);
  return value as T;
}

export function parseMobileUser(value: unknown): MobileUser {
  const row = object(value, 'mobile user');
  const pairing = object(row.unused_pairing, 'unused_pairing');
  return {
    user_id: integer(row.user_id, 'user_id'),
    username: text(row.username, 'username') as string,
    display_name: text(row.display_name, 'display_name') as string,
    mobile_only: boolean(row.mobile_only, 'mobile_only'),
    account_status: oneOf(row.account_status, ['active', 'disabled'], 'account_status'),
    profile_version: integer(row.profile_version, 'profile_version'),
    created_at: text(row.created_at, 'created_at', true),
    updated_at: text(row.updated_at, 'updated_at', true),
    disabled_at: text(row.disabled_at, 'disabled_at', true),
    disabled_reason: text(row.disabled_reason, 'disabled_reason', true),
    device_scope_count: integer(row.device_scope_count, 'device_scope_count'),
    area_scope_count: integer(row.area_scope_count, 'area_scope_count'),
    active_session_count: integer(row.active_session_count, 'active_session_count'),
    revoked_session_count: integer(row.revoked_session_count, 'revoked_session_count'),
    unused_pairing: {
      available: boolean(pairing.available, 'unused_pairing.available'),
      expires_at: text(pairing.expires_at, 'unused_pairing.expires_at', true),
    },
  };
}

export function parseMobileUserList(value: unknown): MobileUserListResponse {
  const data = object(value, 'mobile user list');
  if (!Array.isArray(data.items)) invalid('items 必须是数组');
  return {
    items: data.items.map(parseMobileUser),
    total: integer(data.total, 'total'),
    limit: integer(data.limit, 'limit'),
    offset: integer(data.offset, 'offset'),
    has_more: boolean(data.has_more, 'has_more'),
  };
}

export function parseScopes(value: unknown): MobileScopesResponse {
  const data = object(value, 'scopes');
  const user = object(data.user, 'user');
  if (!Array.isArray(data.scopes)) invalid('scopes 必须是数组');
  return {
    user: {
      user_id: integer(user.user_id, 'user.user_id'),
      username: text(user.username, 'user.username') as string,
      role: oneOf(user.role, ['user'], 'user.role'),
    },
    scope_version: integer(data.scope_version, 'scope_version'),
    scopes: data.scopes.map((value) => {
      const item = object(value, 'scope');
      return {
        scope_kind: oneOf(item.scope_kind, ['device', 'area'], 'scope_kind'),
        scope_value: text(item.scope_value, 'scope_value') as string,
        scope_version: integer(item.scope_version, 'scope_version'),
        created_at: text(item.created_at, 'created_at') as string,
      };
    }),
  };
}

export function parsePairing(value: unknown): PairingStartResponse {
  const data = object(value, 'pairing');
  const user = object(data.user, 'pairing.user');
  return {
    pairing_id: text(data.pairing_id, 'pairing_id') as string,
    pairing_code: text(data.pairing_code, 'pairing_code') as string,
    expires_at: text(data.expires_at, 'expires_at') as string,
    user: {
      user_id: integer(user.user_id, 'user_id'),
      username: text(user.username, 'username') as string,
    },
  };
}

export function parseSessionList(value: unknown): MobileSessionListResponse {
  const data = object(value, 'session list');
  const pagination = object(data.pagination, 'pagination');
  if (!Array.isArray(data.sessions)) invalid('sessions 必须是数组');
  const sessions = data.sessions.map((value) => {
    const item = object(value, 'session');
    const forbidden = ['access_token', 'refresh_token', 'access_token_hash', 'refresh_token_hash'];
    if (forbidden.some((key) => key in item)) invalid('session 响应包含禁止的 token 字段');
    return {
      session_id: text(item.session_id, 'session_id') as string,
      user_id: integer(item.user_id, 'user_id'),
      username: text(item.username, 'username', true),
      client_instance_id: text(item.client_instance_id, 'client_instance_id') as string,
      client_display_name: text(item.client_display_name, 'client_display_name') as string,
      created_at: text(item.created_at, 'created_at') as string,
      issued_at: text(item.issued_at, 'issued_at') as string,
      last_seen_at: text(item.last_seen_at, 'last_seen_at') as string,
      access_expires_at: text(item.access_expires_at, 'access_expires_at') as string,
      refresh_expires_at: text(item.refresh_expires_at, 'refresh_expires_at') as string,
      revoked_at: text(item.revoked_at, 'revoked_at', true),
      revoked_reason: text(item.revoked_reason, 'revoked_reason', true),
      token_generation: integer(item.token_generation, 'token_generation'),
    };
  });
  return {
    sessions,
    pagination: {
      limit: integer(pagination.limit, 'limit'),
      offset: integer(pagination.offset, 'offset'),
      total: integer(pagination.total, 'total'),
      has_more: boolean(pagination.has_more, 'has_more'),
    },
  };
}

function errorKind(status: number): MobileAccessErrorKind {
  if (status === 400) return 'bad_request';
  if (status === 401) return 'unauthorized';
  if (status === 403) return 'forbidden';
  if (status === 404) return 'not_found';
  if (status === 409) return 'conflict';
  if (status === 413) return 'too_large';
  if (status === 429) return 'rate_limited';
  if (status === 503) return 'unavailable';
  return 'http';
}

function queryString(values: Record<string, string | number | boolean | undefined>): string {
  const query = new URLSearchParams();
  Object.entries(values).forEach(([key, value]) => {
    if (value !== undefined) query.set(key, String(value));
  });
  const result = query.toString();
  return result ? `?${result}` : '';
}

export interface MobileAccessApi {
  listUsers(query?: Record<string, string | number | boolean | undefined>, signal?: AbortSignal): Promise<MobileUserListResponse>;
  createUser(input: { username: string; display_name: string }, signal?: AbortSignal): Promise<MobileUser>;
  getUser(userId: number, signal?: AbortSignal): Promise<MobileUser>;
  updateUser(userId: number, input: Record<string, unknown>, signal?: AbortSignal): Promise<MobileUser>;
  getScopes(userId: number, signal?: AbortSignal): Promise<MobileScopesResponse>;
  replaceScopes(userId: number, input: { expected_scope_version: number; scopes: Array<{ scope_kind: MobileScopeKind; scope_value: string }> }, signal?: AbortSignal): Promise<MobileScopesResponse>;
  startPairing(userId: number, signal?: AbortSignal): Promise<PairingStartResponse>;
  listSessions(query?: Record<string, string | number | undefined>, signal?: AbortSignal): Promise<MobileSessionListResponse>;
  revokeSession(sessionId: string, reason: string, signal?: AbortSignal): Promise<{ session_id: string; revoked: true; already_revoked: boolean; revoked_reason: string }>;
}

export function createMobileAccessApi(fetcher: typeof fetch = fetch): MobileAccessApi {
  let csrfToken: string | null = null;

  async function request(path: string, init: RequestInit = {}): Promise<unknown> {
    let response: Response;
    try {
      response = await fetcher(path, { ...init, credentials: 'include' });
    } catch (cause) {
      if (cause instanceof DOMException && cause.name === 'AbortError') {
        throw new MobileAccessApiError('aborted', '请求已取消', { cause });
      }
      throw new MobileAccessApiError('network', '无法连接后端服务', { cause });
    }
    const receivedCsrf = response.headers.get(MOBILE_CSRF_HEADER);
    if (receivedCsrf) csrfToken = receivedCsrf;
    let payload: unknown;
    try {
      payload = await response.json();
    } catch (cause) {
      throw new MobileAccessApiError('invalid_response', '后端返回了无法解析的响应', {
        status: response.status, cause,
      });
    }
    if (!response.ok) {
      const envelope = object(payload, 'error response');
      const error = object(envelope.error, 'error');
      throw new MobileAccessApiError(
        errorKind(response.status),
        typeof error.message === 'string' ? error.message : '请求失败',
        {
          status: response.status,
          code: typeof error.code === 'string' ? error.code : undefined,
          requestId: typeof error.request_id === 'string' ? error.request_id : undefined,
        },
      );
    }
    return payload;
  }

  async function write(path: string, method: string, body: unknown, signal?: AbortSignal) {
    if (!csrfToken) await request(`${MOBILE_USERS_ENDPOINT}?limit=1&offset=0`, { signal });
    if (!csrfToken) throw new MobileAccessApiError('forbidden', '未能取得 CSRF token');
    return request(path, {
      method,
      signal,
      headers: { 'Content-Type': 'application/json', [MOBILE_CSRF_HEADER]: csrfToken },
      body: JSON.stringify(body),
    });
  }

  return {
    listUsers: async (query = {}, signal) => parseMobileUserList(
      await request(MOBILE_USERS_ENDPOINT + queryString(query), { signal }),
    ),
    createUser: async (input, signal) => parseMobileUser(
      await write(MOBILE_USERS_ENDPOINT, 'POST', input, signal),
    ),
    getUser: async (userId, signal) => parseMobileUser(
      await request(`${MOBILE_USERS_ENDPOINT}/${encodeURIComponent(userId)}`, { signal }),
    ),
    updateUser: async (userId, input, signal) => parseMobileUser(
      await write(`${MOBILE_USERS_ENDPOINT}/${encodeURIComponent(userId)}`, 'PATCH', input, signal),
    ),
    getScopes: async (userId, signal) => parseScopes(
      await request(`${MOBILE_USERS_ENDPOINT}/${encodeURIComponent(userId)}/scopes`, { signal }),
    ),
    replaceScopes: async (userId, input, signal) => parseScopes(
      await write(`${MOBILE_USERS_ENDPOINT}/${encodeURIComponent(userId)}/scopes`, 'PUT', input, signal),
    ),
    startPairing: async (userId, signal) => parsePairing(
      await write('/api/v3/pairing/start', 'POST', { user_id: userId }, signal),
    ),
    listSessions: async (query = {}, signal) => parseSessionList(
      await request(MOBILE_SESSIONS_ENDPOINT + queryString(query), { signal }),
    ),
    revokeSession: async (sessionId, reason, signal) => {
      const value = object(
        await write(
          `${MOBILE_SESSIONS_ENDPOINT}/${encodeURIComponent(sessionId)}/revoke`,
          'POST',
          { reason },
          signal,
        ),
        'revoke response',
      );
      if (value.revoked !== true) invalid('revoked 必须为 true');
      return {
        session_id: text(value.session_id, 'session_id') as string,
        revoked: true,
        already_revoked: boolean(value.already_revoked, 'already_revoked'),
        revoked_reason: text(value.revoked_reason, 'revoked_reason') as string,
      };
    },
  };
}

export const mobileAccessApi = createMobileAccessApi();
