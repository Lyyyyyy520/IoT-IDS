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
export type MobileDeviceNoticeSummary = {
  incident_id: string; user_title: string;
  severity: 'info' | 'low' | 'medium' | 'high' | 'critical';
  status: 'open' | 'acknowledged' | 'recovering'; updated_at: string;
  read: boolean; acknowledged: boolean;
};
export type MobileDeviceDetail = {
  device_id: string; display_name: string; device_type: string; area_id: string | null;
  connection_status: 'online' | 'stale' | 'offline' | 'unknown';
  operation_mode: 'active' | 'maintenance' | 'disabled'; retired: boolean;
  retired_at: string | null; last_updated_at: string | null; last_seen_at: string | null;
  availability_status: 'available' | 'delayed' | 'unavailable' | 'unknown' | 'maintenance' | 'disabled' | 'retired';
  status_text: { connection: string; operation: string }; availability_text: string;
  security_capability: {
    available: boolean; reason: string | null; active_notice_count: number | null;
    recent_notices: MobileDeviceNoticeSummary[];
    gnn: { available: false; reason: string };
  };
  traffic_capability: { available: boolean; reason: string | null };
};
export type MobileTrafficWindow = '15m' | '1h' | '24h';
export type MobileTrafficPoint = {
  bucket_start: string; uploaded_bytes: number; downloaded_bytes: number;
  uploaded_packets: number; downloaded_packets: number;
};
export type MobileDeviceTraffic = {
  device_id: string; window: MobileTrafficWindow;
  query_window: { from: string; to: string }; generated_at: string; is_historical: boolean;
  availability: { status: 'available' | 'no_samples'; available: boolean; reason: string | null };
  freshness: { status: 'fresh' | 'stale' | 'unavailable'; latest_sample_at: string | null };
  current_rate: {
    status: 'available' | 'warming_up'; label: string; window_seconds: number | null;
    as_of: string | null; uploaded_bytes_per_second: number | null;
    downloaded_bytes_per_second: number | null; uploaded_packets_per_second: number | null;
    downloaded_packets_per_second: number | null;
  };
  summary: { uploaded_bytes: number; downloaded_bytes: number; uploaded_packets: number; downloaded_packets: number } | null;
  trend_resolution_seconds: number; trend: MobileTrafficPoint[];
  protocols: { category: 'tcp' | 'udp' | 'network_diagnostics' | 'other'; label: string; bytes: number; packets: number; share_percent: number }[];
  data_quality: { complete: null; message: string };
};
export type MobileHistoryWindow = '1h' | '24h' | '7d';
export type MobileHistoryPoint = {
  observation_id: number; observed_at: string; received_at: string;
  telemetry: Record<string, string | number | boolean | null>;
};
export type MobileConnectionChange = {
  event_id: number; occurred_at: string; from_status: MobileDevice['connection_status'];
  to_status: MobileDevice['connection_status']; source: 'report' | 'timeout';
};
export type MobileDeviceHistory = {
  device_id: string; window: MobileHistoryWindow; query_window: { from: string; to: string };
  generated_at: string; availability: { available: boolean; reason: string | null };
  data_source: 'v3_device_state_observations'; latest: MobileHistoryPoint | null;
  series: MobileHistoryPoint[]; truncated: boolean; bucket_seconds: number;
  connection_history_available: boolean; connection_status_changes: MobileConnectionChange[];
  connection_history_truncated: boolean; connection_history_note: string;
};
export type MobileTopologyWindow = MobileHistoryWindow;
export type MobileTopology = {
  generated_at: string; window: { from: string; to: string; range: MobileTopologyWindow };
  nodes: Array<{ device_id: string; display_name: string; device_type: string; area_id: string | null;
    connection_status: MobileDevice['connection_status']; last_received_at: string | null; retired: boolean;
    active_alert_count: number | null; highest_alert_severity: NoticeSeverity | null }>;
  edges: Array<{ source_device_id: string; target_device_id: string; bytes: number; packets: number;
    flows: number; protocols: string[]; last_seen: string }>;
  availability: { available: boolean; reason: string | null; source: 'v3_device_traffic_peer_minutes' | null };
  alert_capability: { available: boolean; reason: string | null };
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
export type MobileNoticeReplay = {
  incident_id: string; generated_at: string; window: { from: string; to: string };
  notice: Pick<MobileNotice, 'user_title' | 'user_summary' | 'severity' | 'status' | 'first_seen_at' | 'public_progress'>;
  incident_timeline: Array<{ occurred_at: string; resulting_status: NoticeStatus; public_progress: string }>;
  devices: Array<NoticeDevice & { current_connection_status: MobileDevice['connection_status'];
    current_last_received_at: string | null; observations: Array<{ observation_id: number; observed_at: string;
      received_at: string; temperature_c: number | null; humidity_percent: number | null }> }>;
  relationships: Array<{ source_device_id: string; target_device_id: string; bucket_start: string;
    direction: 'tx' | 'rx'; protocol: string; bytes: number; packets: number; flows: number;
    first_seen: string; last_seen: string }>;
  connection_status_changes: Array<MobileConnectionChange & { device_id: string; state_version: number | null }>;
  gaps: Array<{ device_id: string; from: string; to: string;
    reason: 'no_observations_in_window' | 'no_retained_observation' | 'observation_gap_over_stale_threshold' }>;
  truncated: boolean; traffic_available: boolean;
  capabilities: { connection_status_history: boolean; camera_media: false; reason: string };
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

const finiteNonnegative = (value: unknown): number => {
  if (typeof value !== 'number' || !Number.isFinite(value) || value < 0) throw new Error('number');
  return value;
};
const finiteNumber = (value: unknown): number => {
  if (typeof value !== 'number' || !Number.isFinite(value)) throw new Error('number');
  return value;
};
const parseDeviceNoticeSummary = (value: unknown): MobileDeviceNoticeSummary => {
  const x = obj(value); onlyKeys(x, ['incident_id', 'user_title', 'severity', 'status', 'updated_at', 'read', 'acknowledged']);
  if (typeof x.read !== 'boolean' || typeof x.acknowledged !== 'boolean') throw new Error('notice state');
  return { incident_id: nonempty(x.incident_id), user_title: str(x.user_title),
    severity: oneOf(x.severity, ['info', 'low', 'medium', 'high', 'critical']),
    status: oneOf(x.status, ['open', 'acknowledged', 'recovering']), updated_at: iso(x.updated_at),
    read: x.read, acknowledged: x.acknowledged };
};
export function parseMobileDeviceDetail(value: unknown): MobileDeviceDetail {
  const x = obj(value);
  onlyKeys(x, ['device_id', 'display_name', 'device_type', 'area_id', 'connection_status', 'operation_mode',
    'retired', 'retired_at', 'last_updated_at', 'last_seen_at', 'availability_status', 'status_text',
    'availability_text', 'security_capability', 'traffic_capability']);
  if (typeof x.retired !== 'boolean') throw new Error('retired');
  const status = obj(x.status_text); onlyKeys(status, ['connection', 'operation']);
  const security = obj(x.security_capability);
  onlyKeys(security, ['available', 'reason', 'active_notice_count', 'recent_notices', 'gnn']);
  const gnn = obj(security.gnn); onlyKeys(gnn, ['available', 'reason']);
  if (gnn.available !== false || typeof security.available !== 'boolean' || !Array.isArray(security.recent_notices)) throw new Error('security capability');
  const count = security.active_notice_count === null ? null : int(security.active_notice_count);
  if (security.available && count === null) throw new Error('active notice count');
  const traffic = obj(x.traffic_capability); onlyKeys(traffic, ['available', 'reason']);
  if (typeof traffic.available !== 'boolean') throw new Error('traffic capability');
  return {
    device_id: nonempty(x.device_id), display_name: str(x.display_name), device_type: str(x.device_type),
    area_id: nullable(x.area_id, str), connection_status: oneOf(x.connection_status, ['online', 'stale', 'offline', 'unknown']),
    operation_mode: oneOf(x.operation_mode, ['active', 'maintenance', 'disabled']), retired: x.retired,
    retired_at: nullable(x.retired_at, iso), last_updated_at: nullable(x.last_updated_at, iso), last_seen_at: nullable(x.last_seen_at, iso),
    availability_status: oneOf(x.availability_status, ['available', 'delayed', 'unavailable', 'unknown', 'maintenance', 'disabled', 'retired']),
    status_text: { connection: str(status.connection), operation: str(status.operation) }, availability_text: str(x.availability_text),
    security_capability: { available: security.available, reason: nullable(security.reason, str), active_notice_count: count,
      recent_notices: security.recent_notices.map(parseDeviceNoticeSummary),
      gnn: { available: false, reason: nonempty(gnn.reason) } },
    traffic_capability: { available: traffic.available, reason: nullable(traffic.reason, str) },
  };
}
const parseMobileTrafficPoint = (value: unknown): MobileTrafficPoint => {
  const x = obj(value); onlyKeys(x, ['bucket_start', 'uploaded_bytes', 'downloaded_bytes', 'uploaded_packets', 'downloaded_packets']);
  return { bucket_start: iso(x.bucket_start), uploaded_bytes: int(x.uploaded_bytes), downloaded_bytes: int(x.downloaded_bytes),
    uploaded_packets: int(x.uploaded_packets), downloaded_packets: int(x.downloaded_packets) };
};
export function parseMobileDeviceTraffic(value: unknown): MobileDeviceTraffic {
  const x = obj(value);
  onlyKeys(x, ['device_id', 'window', 'query_window', 'generated_at', 'is_historical', 'availability', 'freshness',
    'current_rate', 'summary', 'trend_resolution_seconds', 'trend', 'protocols', 'data_quality']);
  if (typeof x.is_historical !== 'boolean' || !Array.isArray(x.trend) || !Array.isArray(x.protocols)) throw new Error('traffic');
  const bounds = obj(x.query_window); onlyKeys(bounds, ['from', 'to']);
  const availability = obj(x.availability); onlyKeys(availability, ['status', 'available', 'reason']);
  if (typeof availability.available !== 'boolean') throw new Error('availability');
  const freshness = obj(x.freshness); onlyKeys(freshness, ['status', 'latest_sample_at']);
  const rate = obj(x.current_rate);
  onlyKeys(rate, ['status', 'label', 'window_seconds', 'as_of', 'uploaded_bytes_per_second', 'downloaded_bytes_per_second',
    'uploaded_packets_per_second', 'downloaded_packets_per_second']);
  const optionalRate = (v: unknown) => nullable(v, finiteNonnegative);
  if (rate.status === 'available' && [rate.uploaded_bytes_per_second, rate.downloaded_bytes_per_second,
    rate.uploaded_packets_per_second, rate.downloaded_packets_per_second].some(v => v === null || v === undefined)) throw new Error('available rate');
  const summary = x.summary === null ? null : (() => {
    const s = obj(x.summary); onlyKeys(s, ['uploaded_bytes', 'downloaded_bytes', 'uploaded_packets', 'downloaded_packets']);
    return { uploaded_bytes: int(s.uploaded_bytes), downloaded_bytes: int(s.downloaded_bytes),
      uploaded_packets: int(s.uploaded_packets), downloaded_packets: int(s.downloaded_packets) };
  })();
  const quality = obj(x.data_quality); onlyKeys(quality, ['complete', 'message']);
  if (quality.complete !== null) throw new Error('quality');
  const availabilityStatus = oneOf(availability.status, ['available', 'no_samples']);
  const freshnessStatus = oneOf(freshness.status, ['fresh', 'stale', 'unavailable']);
  const latestSampleAt = nullable(freshness.latest_sample_at, iso);
  const trend = x.trend.map(parseMobileTrafficPoint);
  const protocols = x.protocols.map((raw: unknown) => { const p = obj(raw); onlyKeys(p, ['category', 'label', 'bytes', 'packets', 'share_percent']);
    const share = finiteNonnegative(p.share_percent); if (share > 100) throw new Error('share');
    return { category: oneOf(p.category, ['tcp', 'udp', 'network_diagnostics', 'other']), label: str(p.label),
      bytes: int(p.bytes), packets: int(p.packets), share_percent: share }; });
  if (availabilityStatus === 'available') {
    if (!availability.available || summary === null || latestSampleAt === null || freshnessStatus === 'unavailable')
      throw new Error('available data consistency');
  } else if (availability.available || availability.reason !== 'no_samples' || summary !== null ||
    trend.length > 0 || protocols.length > 0 || latestSampleAt !== null || freshnessStatus !== 'unavailable') {
    throw new Error('no sample consistency');
  }
  const resolution = int(x.trend_resolution_seconds);
  if (resolution <= 0) throw new Error('trend resolution');
  const queryFrom = iso(bounds.from), queryTo = iso(bounds.to);
  if (Date.parse(queryFrom) >= Date.parse(queryTo)) throw new Error('query bounds');
  const currentRateStatus = oneOf(rate.status, ['available', 'warming_up']);
  const rateWindow = nullable(rate.window_seconds, int);
  const rateAsOf = nullable(rate.as_of, iso);
  const rateValues = [rate.uploaded_bytes_per_second, rate.downloaded_bytes_per_second,
    rate.uploaded_packets_per_second, rate.downloaded_packets_per_second].map(optionalRate);
  if (currentRateStatus === 'available') {
    if (rateWindow === null || rateWindow <= 0 || rateAsOf === null || rateValues.some(v => v === null))
      throw new Error('available rate consistency');
  } else if (rateValues.some(v => v !== null)) throw new Error('warming rate consistency');
  return { device_id: nonempty(x.device_id), window: oneOf(x.window, ['15m', '1h', '24h']),
    query_window: { from: queryFrom, to: queryTo }, generated_at: iso(x.generated_at), is_historical: x.is_historical,
    availability: { status: availabilityStatus, available: availability.available,
      reason: nullable(availability.reason, str) },
    freshness: { status: freshnessStatus, latest_sample_at: latestSampleAt },
    current_rate: { status: currentRateStatus, label: str(rate.label),
      window_seconds: rateWindow, as_of: rateAsOf,
      uploaded_bytes_per_second: rateValues[0], downloaded_bytes_per_second: rateValues[1],
      uploaded_packets_per_second: rateValues[2], downloaded_packets_per_second: rateValues[3] },
    summary, trend_resolution_seconds: resolution, trend, protocols,
    data_quality: { complete: null, message: str(quality.message) } };
}

const parseTelemetry = (value: unknown): Record<string, string | number | boolean | null> => {
  const x = obj(value);
  const forbidden = /password|passwd|token|secret|credential|auth|jwt|api_?key|signature|(^|_)(ip|mac)($|_)/i;
  const result: Record<string, string | number | boolean | null> = {};
  Object.entries(x).forEach(([key, item]) => {
    if (!key || key.length > 64 || forbidden.test(key)) throw new Error('unsafe telemetry key');
    if (item === null || typeof item === 'boolean' || typeof item === 'string') {
      if (typeof item === 'string' && item.length > 256) throw new Error('telemetry string');
      result[key] = item;
    } else if (typeof item === 'number' && Number.isFinite(item)) result[key] = item;
    else throw new Error('telemetry scalar');
  });
  return result;
};
const parseHistoryPoint = (value: unknown): MobileHistoryPoint => {
  const x = obj(value); onlyKeys(x, ['observation_id', 'observed_at', 'received_at', 'telemetry']);
  return { observation_id: int(x.observation_id), observed_at: iso(x.observed_at),
    received_at: iso(x.received_at), telemetry: parseTelemetry(x.telemetry) };
};
const connectionStatuses = ['online', 'stale', 'offline', 'unknown'] as const;
const parseMobileConnectionChange = (value: unknown): MobileConnectionChange => {
  const x = obj(value); onlyKeys(x, ['event_id', 'occurred_at', 'from_status', 'to_status', 'source']);
  return { event_id: int(x.event_id), occurred_at: iso(x.occurred_at),
    from_status: oneOf(x.from_status, connectionStatuses), to_status: oneOf(x.to_status, connectionStatuses),
    source: oneOf(x.source, ['report', 'timeout']) };
};
export function parseMobileDeviceHistory(value: unknown): MobileDeviceHistory {
  const x = obj(value);
  onlyKeys(x, ['device_id', 'window', 'query_window', 'generated_at', 'availability', 'data_source', 'latest',
    'series', 'truncated', 'bucket_seconds', 'connection_history_available', 'connection_status_changes',
    'connection_history_truncated', 'connection_history_note']);
  const bounds = obj(x.query_window); onlyKeys(bounds, ['from', 'to']);
  const availability = obj(x.availability); onlyKeys(availability, ['available', 'reason']);
  if (typeof availability.available !== 'boolean' || typeof x.truncated !== 'boolean' ||
    typeof x.connection_history_available !== 'boolean' || typeof x.connection_history_truncated !== 'boolean' ||
    !Array.isArray(x.series) || !Array.isArray(x.connection_status_changes)) throw new Error('history');
  const from = iso(bounds.from), to = iso(bounds.to);
  if (Date.parse(from) >= Date.parse(to)) throw new Error('history range');
  const series = x.series.map(parseHistoryPoint);
  const latest = nullable(x.latest, parseHistoryPoint);
  if (availability.available !== series.some(point => Object.keys(point.telemetry).length > 0)) throw new Error('history availability');
  return { device_id: nonempty(x.device_id), window: oneOf(x.window, ['1h', '24h', '7d']),
    query_window: { from, to }, generated_at: iso(x.generated_at),
    availability: { available: availability.available, reason: nullable(availability.reason, str) },
    data_source: oneOf(x.data_source, ['v3_device_state_observations']), latest, series,
    truncated: x.truncated, bucket_seconds: int(x.bucket_seconds),
    connection_history_available: x.connection_history_available,
    connection_status_changes: x.connection_status_changes.map(parseMobileConnectionChange),
    connection_history_truncated: x.connection_history_truncated,
    connection_history_note: str(x.connection_history_note) };
}
export function parseMobileTopology(value: unknown): MobileTopology {
  const x = obj(value); onlyKeys(x, ['generated_at', 'window', 'nodes', 'edges', 'availability', 'alert_capability']);
  if (!Array.isArray(x.nodes) || !Array.isArray(x.edges)) throw new Error('topology arrays');
  const window = obj(x.window); onlyKeys(window, ['from', 'to', 'range']);
  const from = iso(window.from), to = iso(window.to);
  if (Date.parse(from) >= Date.parse(to)) throw new Error('topology range');
  const availability = obj(x.availability); onlyKeys(availability, ['available', 'reason', 'source']);
  if (typeof availability.available !== 'boolean') throw new Error('topology availability');
  const alert = obj(x.alert_capability); onlyKeys(alert, ['available', 'reason']);
  if (typeof alert.available !== 'boolean') throw new Error('alert capability');
  const nodes = x.nodes.map((raw: unknown) => {
    const n = obj(raw); onlyKeys(n, ['device_id', 'display_name', 'device_type', 'area_id', 'connection_status',
      'last_received_at', 'retired', 'active_alert_count', 'highest_alert_severity']);
    if (typeof n.retired !== 'boolean') throw new Error('retired');
    const count = nullable(n.active_alert_count, int);
    const severity = nullable(n.highest_alert_severity, value => oneOf<NoticeSeverity>(value, ['info', 'low', 'medium', 'high', 'critical']));
    if ((count === null) !== (!alert.available) || (count === 0 && severity !== null)) throw new Error('alert state');
    return { device_id: nonempty(n.device_id), display_name: str(n.display_name), device_type: str(n.device_type),
      area_id: nullable(n.area_id, str), connection_status: oneOf(n.connection_status, connectionStatuses),
      last_received_at: nullable(n.last_received_at, iso), retired: n.retired,
      active_alert_count: count, highest_alert_severity: severity };
  });
  const ids = new Set(nodes.map(node => node.device_id));
  const edges = x.edges.map((raw: unknown) => {
    const e = obj(raw); onlyKeys(e, ['source_device_id', 'target_device_id', 'bytes', 'packets', 'flows', 'protocols', 'last_seen']);
    if (!Array.isArray(e.protocols)) throw new Error('protocols');
    const source = nonempty(e.source_device_id), target = nonempty(e.target_device_id);
    if (!ids.has(source) || !ids.has(target)) throw new Error('edge outside node set');
    return { source_device_id: source, target_device_id: target, bytes: int(e.bytes), packets: int(e.packets),
      flows: int(e.flows), protocols: e.protocols.map(nonempty), last_seen: iso(e.last_seen) };
  });
  const edgeSource: MobileTopology['availability']['source'] = nullable(availability.source, value => {
    if (value !== 'v3_device_traffic_peer_minutes') throw new Error('topology source');
    return 'v3_device_traffic_peer_minutes' as const;
  });
  if (availability.available !== (edgeSource !== null) || (availability.available && availability.reason === 'no_observed_device_relationships' && edges.length !== 0)) throw new Error('topology source');
  return { generated_at: iso(x.generated_at), window: { from, to, range: oneOf(window.range, ['1h', '24h', '7d']) },
    nodes, edges, availability: { available: availability.available, reason: nullable(availability.reason, str), source: edgeSource },
    alert_capability: { available: alert.available, reason: nullable(alert.reason, str) } };
}
export function parseMobileNoticeReplay(value: unknown): MobileNoticeReplay {
  const x = obj(value); onlyKeys(x, ['incident_id', 'generated_at', 'window', 'notice', 'incident_timeline', 'devices',
    'relationships', 'connection_status_changes', 'gaps', 'truncated', 'traffic_available', 'capabilities']);
  const bounds = obj(x.window); onlyKeys(bounds, ['from', 'to']);
  const from = iso(bounds.from), to = iso(bounds.to);
  if (Date.parse(from) >= Date.parse(to) || !Array.isArray(x.incident_timeline) || !Array.isArray(x.devices) ||
    !Array.isArray(x.relationships) || !Array.isArray(x.connection_status_changes) || !Array.isArray(x.gaps) ||
    typeof x.truncated !== 'boolean' || typeof x.traffic_available !== 'boolean') throw new Error('replay');
  const noticeValue = obj(x.notice); onlyKeys(noticeValue, ['user_title', 'user_summary', 'severity', 'status', 'first_seen_at', 'public_progress']);
  const notice = { user_title: str(noticeValue.user_title), user_summary: str(noticeValue.user_summary),
    severity: oneOf(noticeValue.severity, ['info', 'low', 'medium', 'high', 'critical']),
    status: oneOf(noticeValue.status, ['open', 'acknowledged', 'recovering', 'resolved', 'false_positive']),
    first_seen_at: iso(noticeValue.first_seen_at), public_progress: str(noticeValue.public_progress) };
  const incidentTimeline = x.incident_timeline.map((raw: unknown) => {
    const item = obj(raw); onlyKeys(item, ['occurred_at', 'resulting_status', 'public_progress']);
    return { occurred_at: iso(item.occurred_at), resulting_status: oneOf(item.resulting_status,
      ['open', 'acknowledged', 'recovering', 'resolved', 'false_positive']), public_progress: str(item.public_progress) };
  });
  const deviceIds = new Set<string>();
  const devices = x.devices.map((raw: unknown) => {
    const d = obj(raw); onlyKeys(d, ['device_id', 'display_name', 'device_type', 'area_id', 'current_connection_status',
      'current_last_received_at', 'observations']);
    if (!Array.isArray(d.observations)) throw new Error('device observations');
    const id = nonempty(d.device_id); deviceIds.add(id);
    const observations = d.observations.map((rawPoint: unknown) => {
      const p = obj(rawPoint); onlyKeys(p, ['observation_id', 'device_id', 'observed_at', 'received_at', 'temperature_c', 'humidity_percent']);
      if (nonempty(p.device_id) !== id) throw new Error('cross-device observation');
      return { observation_id: int(p.observation_id), observed_at: iso(p.observed_at), received_at: iso(p.received_at),
        temperature_c: nullable(p.temperature_c, finiteNumber), humidity_percent: nullable(p.humidity_percent, finiteNonnegative) };
    });
    return { device_id: id, display_name: str(d.display_name), device_type: str(d.device_type), area_id: nullable(d.area_id, str),
      current_connection_status: oneOf(d.current_connection_status, connectionStatuses),
      current_last_received_at: nullable(d.current_last_received_at, iso), observations };
  });
  const relationships = x.relationships.map((raw: unknown) => {
    const r = obj(raw); onlyKeys(r, ['source_device_id', 'target_device_id', 'bucket_start', 'direction', 'protocol', 'bytes', 'packets', 'flows', 'first_seen', 'last_seen']);
    const source = nonempty(r.source_device_id), target = nonempty(r.target_device_id);
    if (!deviceIds.has(source) || !deviceIds.has(target)) throw new Error('relationship outside scope');
    return { source_device_id: source, target_device_id: target, bucket_start: iso(r.bucket_start), direction: oneOf(r.direction, ['tx', 'rx']),
      protocol: str(r.protocol), bytes: int(r.bytes), packets: int(r.packets), flows: int(r.flows), first_seen: iso(r.first_seen), last_seen: iso(r.last_seen) };
  });
  const changes = x.connection_status_changes.map((raw: unknown) => {
    const c = obj(raw); onlyKeys(c, ['device_id', 'event_id', 'occurred_at', 'state_version', 'from_status', 'to_status', 'source']);
    const id = nonempty(c.device_id); if (!deviceIds.has(id)) throw new Error('change outside scope');
    return { ...parseMobileConnectionChange({ event_id: c.event_id, occurred_at: c.occurred_at, from_status: c.from_status,
      to_status: c.to_status, source: c.source }), device_id: id, state_version: nullable(c.state_version, int) };
  });
  const gaps = x.gaps.map((raw: unknown) => {
    const g = obj(raw); onlyKeys(g, ['device_id', 'from', 'to', 'reason']);
    const id = nonempty(g.device_id); if (!deviceIds.has(id)) throw new Error('gap outside scope');
    return { device_id: id, from: iso(g.from), to: iso(g.to), reason: oneOf(g.reason,
      ['no_observations_in_window', 'no_retained_observation', 'observation_gap_over_stale_threshold']) };
  });
  const capabilities = obj(x.capabilities); onlyKeys(capabilities, ['connection_status_history', 'camera_media', 'reason']);
  if (typeof capabilities.connection_status_history !== 'boolean' || capabilities.camera_media !== false) throw new Error('replay capability');
  return { incident_id: nonempty(x.incident_id), generated_at: iso(x.generated_at), window: { from, to }, notice,
    incident_timeline: incidentTimeline, devices, relationships, connection_status_changes: changes, gaps,
    truncated: x.truncated, traffic_available: x.traffic_available,
    capabilities: { connection_status_history: capabilities.connection_status_history, camera_media: false, reason: str(capabilities.reason) } };
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
  noticeReplay: (config: ServerConfig, accessToken: string, incidentId: string,
    bounds: { from?: string; to?: string } = {}, signal?: AbortSignal) => {
    const query = new URLSearchParams();
    if (bounds.from) query.set('from', bounds.from);
    if (bounds.to) query.set('to', bounds.to);
    const encoded = query.toString();
    const suffix = encoded ? `?${encoded}` : '';
    return request(config, `/api/v3/mobile/notices/${encodeURIComponent(incidentId)}/replay${suffix}`,
      parseMobileNoticeReplay, { accessToken, signal });
  },
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
  deviceDetail: (config: ServerConfig, accessToken: string, deviceId: string, signal?: AbortSignal) =>
    request(config, `/api/v3/mobile/devices/${encodeURIComponent(deviceId)}`, parseMobileDeviceDetail, { accessToken, signal }),
  deviceTraffic: (config: ServerConfig, accessToken: string, deviceId: string, window: MobileTrafficWindow = '15m', signal?: AbortSignal) =>
    request(config, `/api/v3/mobile/devices/${encodeURIComponent(deviceId)}/traffic?window=${encodeURIComponent(window)}`,
      parseMobileDeviceTraffic, { accessToken, signal }),
  deviceHistory: (config: ServerConfig, accessToken: string, deviceId: string, window: MobileHistoryWindow = '24h', signal?: AbortSignal) =>
    request(config, `/api/v3/mobile/devices/${encodeURIComponent(deviceId)}/history?window=${encodeURIComponent(window)}`,
      parseMobileDeviceHistory, { accessToken, signal }),
  topology: (config: ServerConfig, accessToken: string, window: MobileTopologyWindow = '24h', signal?: AbortSignal) =>
    request(config, `/api/v3/mobile/topology?window=${encodeURIComponent(window)}`,
      parseMobileTopology, { accessToken, signal }),
};
