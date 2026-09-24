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
  devices: MobileDevice[]; security_capability: SecurityCapability;
};
export type NoticeStatus = 'open' | 'acknowledged' | 'recovering' | 'resolved' | 'false_positive';
export type NoticeSeverity = 'info' | 'low' | 'medium' | 'high' | 'critical';
export type NoticeDevice = { device_id: string; display_name: string; device_type: string; area_id: string | null };
export type MobileNotice = {
  incident_id: string; user_title: string; user_summary: string; severity: NoticeSeverity;
  affected_devices: NoticeDevice[]; first_seen_at: string; updated_at: string;
  status: NoticeStatus; public_progress: string; read: boolean; first_read_at: string | null;
  acknowledged: boolean; acknowledged_at: string | null; resolved_at: string | null;
};
export type NoticeTombstone = { incident_id: string; change_id: number; reason: string };
export type NoticeCollection = {
  mode: 'snapshot' | 'delta'; notices: MobileNotice[]; tombstones: NoticeTombstone[];
  next_cursor: string; snapshot_required: boolean;
};
export type SecurityCapability = {
  available: boolean; reason: string; semantics?: string;
  gnn?: { available: false; reason: string };
  unread_count?: number; unacknowledged_count?: number; recent_notices?: MobileNotice[];
};
export type SupportContact = { available: false; reason: string } | {
  available: true; display_name: string; phone: string | null; email: string | null;
  working_hours: string | null; public_note: string | null; config_version: number; updated_at: string;
};
export type HelpCategory = 'device_issue' | 'security_question' | 'service_problem' | 'other';
export type HelpStatus = 'open' | 'in_progress' | 'waiting_for_user' | 'closed';
export type MobileHelpRequest = {
  help_request_id: string; incident_id: string | null; device_id: string | null;
  category: HelpCategory; user_message: string; status: HelpStatus; public_response: string | null;
  created_at: string; updated_at: string; closed_at: string | null; request_version: number;
  idempotent_replay?: boolean;
};

export class MobileApiError extends Error {
  constructor(public kind: 'http' | 'network' | 'invalid_response', public status: number,
    public code: string, public requestId?: string) { super(code); this.name = 'MobileApiError'; }
}

const obj = (value: unknown): Json => {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('object');
  return value as Json;
};
const onlyKeys = (value: Json, keys: readonly string[]): void => {
  if (Object.keys(value).some(key => !keys.includes(key))) throw new Error('unexpected field');
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
  const gnn = capability.gnn === undefined ? undefined : obj(capability.gnn);
  if (gnn && (gnn.available !== false || typeof gnn.reason !== 'string')) throw new Error('gnn capability');
  const recent = capability.recent_notices === undefined ? undefined : capability.recent_notices;
  if (recent !== undefined && !Array.isArray(recent)) throw new Error('recent notices');
  if (capability.unread_count !== undefined) int(capability.unread_count);
  if (capability.unacknowledged_count !== undefined) int(capability.unacknowledged_count);
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
    security_capability: {
      available: capability.available, reason: str(capability.reason),
      ...(capability.semantics === undefined ? {} : { semantics: str(capability.semantics) }),
      ...(gnn ? { gnn: { available: false as const, reason: str(gnn.reason) } } : {}),
      ...(capability.unread_count === undefined ? {} : { unread_count: int(capability.unread_count) }),
      ...(capability.unacknowledged_count === undefined ? {} : { unacknowledged_count: int(capability.unacknowledged_count) }),
      ...(recent === undefined ? {} : { recent_notices: (recent as unknown[]).map(parseNotice) }),
    },
  };
}

const NOTICE_KEYS = ['incident_id', 'user_title', 'user_summary', 'severity', 'affected_devices',
  'first_seen_at', 'updated_at', 'status', 'public_progress', 'read', 'first_read_at',
  'acknowledged', 'acknowledged_at', 'resolved_at'] as const;
export function parseNotice(value: unknown): MobileNotice {
  const x = obj(value); onlyKeys(x, NOTICE_KEYS);
  if (!Array.isArray(x.affected_devices) || typeof x.read !== 'boolean' || typeof x.acknowledged !== 'boolean') throw new Error('notice');
  return {
    incident_id: nonempty(x.incident_id), user_title: str(x.user_title), user_summary: str(x.user_summary),
    severity: oneOf(x.severity, ['info', 'low', 'medium', 'high', 'critical']),
    affected_devices: x.affected_devices.map((raw: unknown): NoticeDevice => {
      const d = obj(raw); onlyKeys(d, ['device_id', 'display_name', 'device_type', 'area_id']);
      return { device_id: nonempty(d.device_id), display_name: str(d.display_name), device_type: str(d.device_type), area_id: nullable(d.area_id, str) };
    }),
    first_seen_at: iso(x.first_seen_at), updated_at: iso(x.updated_at),
    status: oneOf(x.status, ['open', 'acknowledged', 'recovering', 'resolved', 'false_positive']),
    public_progress: str(x.public_progress), read: x.read, first_read_at: nullable(x.first_read_at, iso),
    acknowledged: x.acknowledged, acknowledged_at: nullable(x.acknowledged_at, iso), resolved_at: nullable(x.resolved_at, iso),
  };
}
export function parseNoticeCollection(value: unknown): NoticeCollection {
  const x = obj(value); onlyKeys(x, ['mode', 'notices', 'tombstones', 'next_cursor', 'snapshot_required']);
  if (!Array.isArray(x.notices) || !Array.isArray(x.tombstones) || typeof x.snapshot_required !== 'boolean') throw new Error('notice collection');
  const cursor = nonempty(x.next_cursor);
  if (!/^(0|[1-9][0-9]*):(0|[1-9][0-9]*)$/.test(cursor)) throw new Error('cursor');
  return {
    mode: oneOf(x.mode, ['snapshot', 'delta']), notices: x.notices.map(parseNotice),
    tombstones: x.tombstones.map((raw: unknown): NoticeTombstone => {
      const t = obj(raw); onlyKeys(t, ['incident_id', 'change_id', 'reason']);
      return { incident_id: nonempty(t.incident_id), change_id: int(t.change_id), reason: nonempty(t.reason) };
    }), next_cursor: cursor, snapshot_required: x.snapshot_required,
  };
}
export function parseSupportContact(value: unknown): SupportContact {
  const x = obj(value);
  if (x.available === false) {
    onlyKeys(x, ['available', 'reason']);
    return { available: false, reason: nonempty(x.reason) };
  }
  onlyKeys(x, ['available', 'display_name', 'phone', 'email', 'working_hours', 'public_note', 'config_version', 'updated_at']);
  if (x.available !== true) throw new Error('contact availability');
  return { available: true, display_name: str(x.display_name), phone: nullable(x.phone, str), email: nullable(x.email, str),
    working_hours: nullable(x.working_hours, str), public_note: nullable(x.public_note, str),
    config_version: int(x.config_version), updated_at: iso(x.updated_at) };
}
const HELP_KEYS = ['help_request_id', 'incident_id', 'device_id', 'category', 'user_message', 'status',
  'public_response', 'created_at', 'updated_at', 'closed_at', 'request_version', 'idempotent_replay'] as const;
export function parseHelpRequest(value: unknown): MobileHelpRequest {
  const x = obj(value); onlyKeys(x, HELP_KEYS);
  if (x.idempotent_replay !== undefined && typeof x.idempotent_replay !== 'boolean') throw new Error('replay');
  return { help_request_id: nonempty(x.help_request_id), incident_id: nullable(x.incident_id, nonempty),
    device_id: nullable(x.device_id, nonempty), category: oneOf(x.category, ['device_issue', 'security_question', 'service_problem', 'other']),
    user_message: str(x.user_message), status: oneOf(x.status, ['open', 'in_progress', 'waiting_for_user', 'closed']),
    public_response: nullable(x.public_response, str), created_at: iso(x.created_at), updated_at: iso(x.updated_at),
    closed_at: nullable(x.closed_at, iso), request_version: int(x.request_version),
    ...(x.idempotent_replay === undefined ? {} : { idempotent_replay: x.idempotent_replay as boolean }) };
}
export function parseHelpRequestList(value: unknown): { items: MobileHelpRequest[] } {
  const x = obj(value); onlyKeys(x, ['items']);
  if (!Array.isArray(x.items)) throw new Error('items');
  return { items: x.items.map(parseHelpRequest) };
}

async function request<T>(config: ServerConfig, path: string, parse: (v: unknown) => T,
  options: { method?: 'GET' | 'POST'; body?: object; accessToken?: string; signal?: AbortSignal; idempotencyKey?: string } = {}): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${config.baseUrl}${path}`, {
      method: options.method ?? (options.body ? 'POST' : 'GET'), credentials: 'omit', signal: options.signal,
      headers: { ...(options.body ? { 'Content-Type': 'application/json' } : {}),
        ...(options.accessToken ? { Authorization: `Bearer ${options.accessToken}` } : {}),
        ...(options.idempotencyKey ? { 'Idempotency-Key': options.idempotencyKey } : {}) },
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
  notices: (config: ServerConfig, accessToken: string, options: { after?: string; view?: 'active' | 'history' | 'all'; limit?: number; signal?: AbortSignal } = {}) => {
    const query = new URLSearchParams();
    if (options.after) query.set('after', options.after);
    query.set('view', options.view ?? 'all');
    if (options.limit !== undefined) query.set('limit', String(options.limit));
    return request(config, `/api/v3/mobile/notices?${query.toString()}`, parseNoticeCollection, { accessToken, signal: options.signal });
  },
  notice: (config: ServerConfig, accessToken: string, incidentId: string, signal?: AbortSignal) =>
    request(config, `/api/v3/mobile/notices/${encodeURIComponent(incidentId)}`, parseNotice, { accessToken, signal }),
  markNoticeRead: (config: ServerConfig, accessToken: string, incidentId: string, signal?: AbortSignal) =>
    request(config, `/api/v3/mobile/notices/${encodeURIComponent(incidentId)}/read`, parseNotice, { method: 'POST', body: {}, accessToken, signal }),
  acknowledgeNotice: (config: ServerConfig, accessToken: string, incidentId: string, signal?: AbortSignal) =>
    request(config, `/api/v3/mobile/notices/${encodeURIComponent(incidentId)}/acknowledge`, parseNotice, { method: 'POST', body: {}, accessToken, signal }),
  supportContact: (config: ServerConfig, accessToken: string, signal?: AbortSignal) =>
    request(config, '/api/v3/mobile/support-contact', parseSupportContact, { accessToken, signal }),
  createHelpRequest: (config: ServerConfig, accessToken: string, body: { category: HelpCategory; incident_id?: string; device_id?: string; user_message: string }, idempotencyKey: string, signal?: AbortSignal) =>
    request(config, '/api/v3/mobile/help-requests', parseHelpRequest, { method: 'POST', body, accessToken, signal, idempotencyKey }),
  helpRequests: (config: ServerConfig, accessToken: string, signal?: AbortSignal) =>
    request(config, '/api/v3/mobile/help-requests', parseHelpRequestList, { accessToken, signal }),
  helpRequest: (config: ServerConfig, accessToken: string, helpRequestId: string, signal?: AbortSignal) =>
    request(config, `/api/v3/mobile/help-requests/${encodeURIComponent(helpRequestId)}`, parseHelpRequest, { accessToken, signal }),
};
