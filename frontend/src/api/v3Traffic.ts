export const V3_DEVICES_ENDPOINT = '/api/v3/devices';

export type TrafficResolution = 'auto' | 'minute' | '5minute' | 'hour';
export type TrafficDirection = 'tx' | 'rx';
export type PeerSort = 'bytes' | 'packets';
export type DetectionReadiness = 'warming_up' | 'ready' | 'degraded';
export type ProtocolEvidence =
  | 'network_protocol'
  | 'application_protocol'
  | 'inferred_application_protocol';

export interface TrafficWindow { from: string; to: string }
export interface TrafficAvailability {
  available: boolean;
  reason: string | null;
  latest_sample_at: string | null;
}
export interface TrafficRealtimeUnavailable {
  available: false;
  readiness: DetectionReadiness;
  reason: string;
  window_seconds: number;
  as_of: string;
}
export interface TrafficRealtimeAvailable {
  available: true;
  readiness: 'ready';
  reason: null;
  window_seconds: number;
  as_of: string;
  tx_bytes_per_second: number;
  rx_bytes_per_second: number;
  tx_packets_per_second: number;
  rx_packets_per_second: number;
  tx_flows_per_second: number;
  rx_flows_per_second: number;
}
export type TrafficRealtime = TrafficRealtimeUnavailable | TrafficRealtimeAvailable;

export interface TrafficTotals {
  tx_bytes: number;
  rx_bytes: number;
  tx_packets: number;
  rx_packets: number;
  tx_flows: number;
  rx_flows: number;
}
export interface TrafficPoint extends TrafficTotals { bucket_start: string }
export interface ProtocolAggregate extends TrafficTotals {
  protocol: string;
  evidence: ProtocolEvidence;
}
export interface TrafficResponse {
  api_version: 'v3';
  traffic_schema_version: 1;
  generated_at: string;
  device_id: string;
  window: TrafficWindow;
  resolution: Exclude<TrafficResolution, 'auto'>;
  protocol_filter: string | null;
  availability: TrafficAvailability;
  freshness: {
    historical_source: 'sqlite_minute_aggregates';
    latest_sample_at: string | null;
  };
  realtime: TrafficRealtime;
  summary: TrafficTotals | null;
  series: TrafficPoint[];
  protocols: ProtocolAggregate[];
  data_quality: {
    unassigned_samples_in_window: number;
    has_unassigned_or_missing_data: boolean;
    note: string | null;
  };
}

export interface PeerItem {
  peer_device_id: string | null;
  peer_ip: string | null;
  peer_ip_visible: boolean;
  direction: TrafficDirection;
  protocol: string;
  bytes: number;
  packets: number;
  flows: number;
  first_seen: string;
  last_seen: string;
}
export interface PeersResponse {
  api_version: 'v3';
  traffic_schema_version: 1;
  generated_at: string;
  device_id: string;
  window: TrafficWindow;
  filters: { direction: TrafficDirection | null; protocol: string | null };
  sort: PeerSort;
  availability: { available: boolean; reason: string | null };
  peers: PeerItem[];
  pagination: { limit: number; offset: number; total: number; has_more: boolean };
}

export interface TrafficQuery {
  from: Date;
  to: Date;
  resolution?: TrafficResolution;
  protocol?: string;
}
export interface PeersQuery {
  from: Date;
  to: Date;
  direction?: TrafficDirection;
  protocol?: string;
  sort?: PeerSort;
  limit?: number;
  offset?: number;
}

export type TrafficApiErrorKind =
  | 'bad_request' | 'unauthorized' | 'forbidden' | 'not_found'
  | 'unavailable' | 'network' | 'invalid_response' | 'http' | 'aborted';

export class TrafficApiError extends Error {
  readonly kind: TrafficApiErrorKind;
  readonly status: number | null;
  readonly code: string | null;
  readonly requestId: string | null;

  constructor(kind: TrafficApiErrorKind, message: string, options: {
    status?: number; code?: string; requestId?: string; cause?: unknown;
  } = {}) {
    super(message, { cause: options.cause });
    this.name = 'TrafficApiError';
    this.kind = kind;
    this.status = options.status ?? null;
    this.code = options.code ?? null;
    this.requestId = options.requestId ?? null;
  }
}

type JsonObject = Record<string, unknown>;
const RESOLUTIONS = ['minute', '5minute', 'hour'] as const;
const READINESS = ['warming_up', 'ready', 'degraded'] as const;
const DIRECTIONS = ['tx', 'rx'] as const;
const SORTS = ['bytes', 'packets'] as const;
const EVIDENCE = [
  'network_protocol', 'application_protocol', 'inferred_application_protocol',
] as const;

function invalid(message: string): never {
  throw new TrafficApiError('invalid_response', message);
}
function object(value: unknown, label: string): JsonObject {
  if (!value || typeof value !== 'object' || Array.isArray(value)) invalid(`${label} 必须是对象`);
  return value as JsonObject;
}
function text(value: unknown, label: string): string {
  if (typeof value !== 'string' || !value.trim()) invalid(`${label} 必须是非空字符串`);
  return value as string;
}
function nullableText(value: unknown, label: string): string | null {
  return value === null ? null : text(value, label);
}
function boolean(value: unknown, label: string): boolean {
  if (typeof value !== 'boolean') invalid(`${label} 必须是布尔值`);
  return value as boolean;
}
function number(value: unknown, label: string): number {
  if (typeof value !== 'number' || !Number.isFinite(value) || value < 0) {
    invalid(`${label} 必须是有限非负数`);
  }
  return value as number;
}
function integer(value: unknown, label: string, minimum = 0): number {
  const parsed = number(value, label);
  if (!Number.isInteger(parsed) || parsed < minimum) invalid(`${label} 必须是整数`);
  return parsed;
}
function oneOf<T extends readonly string[]>(value: unknown, allowed: T, label: string): T[number] {
  if (typeof value !== 'string' || !allowed.includes(value)) invalid(`${label} 枚举值无效`);
  return value as T[number];
}
function timestamp(value: unknown, label: string): string {
  const parsed = text(value, label);
  if (!/(Z|[+-]\d{2}:\d{2})$/.test(parsed) || Number.isNaN(Date.parse(parsed))) {
    invalid(`${label} 必须是带时区的 ISO 8601 时间`);
  }
  return parsed;
}
function nullableTimestamp(value: unknown, label: string): string | null {
  return value === null ? null : timestamp(value, label);
}
function literal<T extends string | number>(value: unknown, expected: T, label: string): T {
  if (value !== expected) invalid(`${label} 版本不受支持`);
  return expected;
}
function totals(value: unknown, label: string): TrafficTotals {
  const raw = object(value, label);
  return {
    tx_bytes: integer(raw.tx_bytes, `${label}.tx_bytes`),
    rx_bytes: integer(raw.rx_bytes, `${label}.rx_bytes`),
    tx_packets: integer(raw.tx_packets, `${label}.tx_packets`),
    rx_packets: integer(raw.rx_packets, `${label}.rx_packets`),
    tx_flows: integer(raw.tx_flows, `${label}.tx_flows`),
    rx_flows: integer(raw.rx_flows, `${label}.rx_flows`),
  };
}
function windowValue(value: unknown, label: string): TrafficWindow {
  const raw = object(value, label);
  return { from: timestamp(raw.from, `${label}.from`), to: timestamp(raw.to, `${label}.to`) };
}
function readiness(value: unknown, label: string): DetectionReadiness {
  return oneOf(value, READINESS, label);
}

export function parseTrafficResponse(value: unknown): TrafficResponse {
  const raw = object(value, 'traffic response');
  const availability = object(raw.availability, 'availability');
  const freshness = object(raw.freshness, 'freshness');
  const realtime = object(raw.realtime, 'realtime');
  const quality = object(raw.data_quality, 'data_quality');
  if (!Array.isArray(raw.series)) invalid('series 必须是数组');
  if (!Array.isArray(raw.protocols)) invalid('protocols 必须是数组');
  const realtimeAvailable = boolean(realtime.available, 'realtime.available');
  const baseRealtime = {
    available: realtimeAvailable,
    readiness: readiness(realtime.readiness, 'realtime.readiness'),
    reason: realtimeAvailable
      ? (realtime.reason === null ? null : invalid('可用实时窗口 reason 必须为 null'))
      : text(realtime.reason, 'realtime.reason'),
    window_seconds: integer(realtime.window_seconds, 'realtime.window_seconds', 1),
    as_of: timestamp(realtime.as_of, 'realtime.as_of'),
  };
  const parsedRealtime: TrafficRealtime = realtimeAvailable ? {
    ...baseRealtime,
    available: true,
    readiness: literal(realtime.readiness, 'ready', 'realtime.readiness'),
    reason: null,
    tx_bytes_per_second: number(realtime.tx_bytes_per_second, 'realtime.tx_bytes_per_second'),
    rx_bytes_per_second: number(realtime.rx_bytes_per_second, 'realtime.rx_bytes_per_second'),
    tx_packets_per_second: number(realtime.tx_packets_per_second, 'realtime.tx_packets_per_second'),
    rx_packets_per_second: number(realtime.rx_packets_per_second, 'realtime.rx_packets_per_second'),
    tx_flows_per_second: number(realtime.tx_flows_per_second, 'realtime.tx_flows_per_second'),
    rx_flows_per_second: number(realtime.rx_flows_per_second, 'realtime.rx_flows_per_second'),
  } : { ...baseRealtime, available: false } as TrafficRealtimeUnavailable;

  return {
    api_version: literal(raw.api_version, 'v3', 'api_version'),
    traffic_schema_version: literal(raw.traffic_schema_version, 1, 'traffic_schema_version'),
    generated_at: timestamp(raw.generated_at, 'generated_at'),
    device_id: text(raw.device_id, 'device_id'),
    window: windowValue(raw.window, 'window'),
    resolution: oneOf(raw.resolution, RESOLUTIONS, 'resolution'),
    protocol_filter: nullableText(raw.protocol_filter, 'protocol_filter'),
    availability: {
      available: boolean(availability.available, 'availability.available'),
      reason: nullableText(availability.reason, 'availability.reason'),
      latest_sample_at: nullableTimestamp(availability.latest_sample_at, 'availability.latest_sample_at'),
    },
    freshness: {
      historical_source: literal(
        freshness.historical_source, 'sqlite_minute_aggregates', 'freshness.historical_source',
      ),
      latest_sample_at: nullableTimestamp(freshness.latest_sample_at, 'freshness.latest_sample_at'),
    },
    realtime: parsedRealtime,
    summary: raw.summary === null ? null : totals(raw.summary, 'summary'),
    series: raw.series.map((item, index) => {
      const point = object(item, `series[${index}]`);
      return { bucket_start: timestamp(point.bucket_start, `series[${index}].bucket_start`), ...totals(point, `series[${index}]`) };
    }),
    protocols: raw.protocols.map((item, index) => {
      const protocol = object(item, `protocols[${index}]`);
      return {
        protocol: text(protocol.protocol, `protocols[${index}].protocol`),
        evidence: oneOf(protocol.evidence, EVIDENCE, `protocols[${index}].evidence`),
        ...totals(protocol, `protocols[${index}]`),
      };
    }),
    data_quality: {
      unassigned_samples_in_window: integer(
        quality.unassigned_samples_in_window, 'data_quality.unassigned_samples_in_window',
      ),
      has_unassigned_or_missing_data: boolean(
        quality.has_unassigned_or_missing_data, 'data_quality.has_unassigned_or_missing_data',
      ),
      note: nullableText(quality.note, 'data_quality.note'),
    },
  };
}

export function parsePeersResponse(value: unknown): PeersResponse {
  const raw = object(value, 'peers response');
  const filters = object(raw.filters, 'filters');
  const availability = object(raw.availability, 'availability');
  const pagination = object(raw.pagination, 'pagination');
  if (!Array.isArray(raw.peers)) invalid('peers 必须是数组');
  return {
    api_version: literal(raw.api_version, 'v3', 'api_version'),
    traffic_schema_version: literal(raw.traffic_schema_version, 1, 'traffic_schema_version'),
    generated_at: timestamp(raw.generated_at, 'generated_at'),
    device_id: text(raw.device_id, 'device_id'),
    window: windowValue(raw.window, 'window'),
    filters: {
      direction: filters.direction === null ? null : oneOf(filters.direction, DIRECTIONS, 'filters.direction'),
      protocol: nullableText(filters.protocol, 'filters.protocol'),
    },
    sort: oneOf(raw.sort, SORTS, 'sort'),
    availability: {
      available: boolean(availability.available, 'availability.available'),
      reason: nullableText(availability.reason, 'availability.reason'),
    },
    peers: raw.peers.map((item, index) => {
      const peer = object(item, `peers[${index}]`);
      const visible = boolean(peer.peer_ip_visible, `peers[${index}].peer_ip_visible`);
      const peerIp = nullableText(peer.peer_ip, `peers[${index}].peer_ip`);
      if (!visible && peerIp !== null) invalid(`peers[${index}] 隐藏 IP 时必须返回 null`);
      if (visible && peerIp === null) invalid(`peers[${index}] 可见 IP 缺失`);
      return {
        peer_device_id: nullableText(peer.peer_device_id, `peers[${index}].peer_device_id`),
        peer_ip: peerIp,
        peer_ip_visible: visible,
        direction: oneOf(peer.direction, DIRECTIONS, `peers[${index}].direction`),
        protocol: text(peer.protocol, `peers[${index}].protocol`),
        bytes: integer(peer.bytes, `peers[${index}].bytes`),
        packets: integer(peer.packets, `peers[${index}].packets`),
        flows: integer(peer.flows, `peers[${index}].flows`),
        first_seen: timestamp(peer.first_seen, `peers[${index}].first_seen`),
        last_seen: timestamp(peer.last_seen, `peers[${index}].last_seen`),
      };
    }),
    pagination: {
      limit: integer(pagination.limit, 'pagination.limit', 1),
      offset: integer(pagination.offset, 'pagination.offset'),
      total: integer(pagination.total, 'pagination.total'),
      has_more: boolean(pagination.has_more, 'pagination.has_more'),
    },
  };
}

interface ErrorEnvelope { code: string; message: string; requestId: string }
function parseError(value: unknown): ErrorEnvelope | null {
  try {
    const wrapper = object(value, 'error response');
    const error = object(wrapper.error, 'error');
    return {
      code: text(error.code, 'error.code'),
      message: text(error.message, 'error.message'),
      requestId: text(error.request_id, 'error.request_id'),
    };
  } catch { return null; }
}
function errorKind(status: number): TrafficApiErrorKind {
  if (status === 400) return 'bad_request';
  if (status === 401) return 'unauthorized';
  if (status === 403) return 'forbidden';
  if (status === 404) return 'not_found';
  if (status === 503) return 'unavailable';
  return 'http';
}
function isAbort(error: unknown): boolean {
  return error instanceof DOMException && error.name === 'AbortError'
    || error instanceof Error && error.name === 'AbortError';
}
function dateParameter(value: Date, label: string): string {
  if (!(value instanceof Date) || Number.isNaN(value.getTime())) {
    throw new TrafficApiError('bad_request', `${label} 时间无效`);
  }
  return value.toISOString();
}

export interface TrafficApi {
  getTraffic(deviceId: string, query: TrafficQuery, signal?: AbortSignal): Promise<TrafficResponse>;
  getPeers(deviceId: string, query: PeersQuery, signal?: AbortSignal): Promise<PeersResponse>;
}

export class V3TrafficClient implements TrafficApi {
  private async get<T>(url: string, parser: (value: unknown) => T, signal?: AbortSignal): Promise<T> {
    let response: Response;
    try {
      response = await fetch(url, {
        method: 'GET', credentials: 'include', cache: 'no-store',
        headers: { Accept: 'application/json' }, signal,
      });
    } catch (error) {
      if (isAbort(error)) throw new TrafficApiError('aborted', '流量请求已取消', { cause: error });
      throw new TrafficApiError('network', '无法连接流量聚合服务', { cause: error });
    }
    let body: unknown;
    try { body = await response.json(); } catch (error) {
      throw new TrafficApiError('invalid_response', '流量服务返回了无法解析的 JSON', {
        status: response.status, cause: error,
      });
    }
    if (!response.ok) {
      const error = parseError(body);
      throw new TrafficApiError(
        errorKind(response.status), error?.message ?? `流量请求失败（HTTP ${response.status}）`,
        { status: response.status, code: error?.code, requestId: error?.requestId },
      );
    }
    return parser(body);
  }

  getTraffic(deviceId: string, query: TrafficQuery, signal?: AbortSignal): Promise<TrafficResponse> {
    const params = new URLSearchParams({
      from: dateParameter(query.from, 'from'),
      to: dateParameter(query.to, 'to'),
      resolution: query.resolution ?? 'auto',
    });
    if (query.protocol) params.set('protocol', query.protocol);
    return this.get(
      `${V3_DEVICES_ENDPOINT}/${encodeURIComponent(deviceId)}/traffic?${params}`,
      parseTrafficResponse,
      signal,
    );
  }

  getPeers(deviceId: string, query: PeersQuery, signal?: AbortSignal): Promise<PeersResponse> {
    const params = new URLSearchParams({
      from: dateParameter(query.from, 'from'),
      to: dateParameter(query.to, 'to'),
      direction: query.direction ?? 'all',
      sort: query.sort ?? 'bytes',
      limit: String(query.limit ?? 50),
      offset: String(query.offset ?? 0),
    });
    if (query.protocol) params.set('protocol', query.protocol);
    return this.get(
      `${V3_DEVICES_ENDPOINT}/${encodeURIComponent(deviceId)}/peers?${params}`,
      parsePeersResponse,
      signal,
    );
  }
}

export const v3TrafficApi = new V3TrafficClient();
