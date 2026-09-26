import {
  parseDeviceDetail,
  type DeviceDetail,
  type DeviceImportance,
  type DeviceProfileSource,
} from './v3Devices';

export const DISCOVERY_ENDPOINT = '/api/v3/devices/discovered';
const CSRF_HEADER = 'X-CSRF-Token';

export type CandidateStatus = 'pending' | 'ignored' | 'claimed' | 'conflict';
export type DiscoverySource = 'mqtt_unknown' | 'dhcp' | 'arp' | 'probe' | 'other';

export interface DiscoveryObservation {
  source: DiscoverySource;
  observed_at: string;
  received_at: string;
  proposed_device_id: string | null;
  sanitized_metadata: Record<string, string | number>;
  ip_address?: string | null;
  evidence_hash?: string;
}

export interface DiscoveryCandidate {
  candidate_id: string;
  proposed_device_id: string | null;
  status: CandidateStatus;
  conflict: boolean;
  conflict_reason: string | null;
  first_seen_at: string;
  last_seen_at: string;
  source_count: number;
  observation_count: number;
  claimed_device_id: string | null;
  claimed_at: string | null;
  ignored_at: string | null;
  ignored_reason: string | null;
  candidate_version: number;
  updated_at: string;
  sources?: DiscoverySource[];
  device_type_hint?: string | null;
  identity_kind?: 'mac';
  mac_address?: string;
  latest_ip?: string | null;
  conflicting_proposed_device_ids?: string[];
  observations?: DiscoveryObservation[];
}

export interface DiscoveryListResponse {
  items: DiscoveryCandidate[];
  total: number;
  limit: number;
  offset: number;
}

export interface DiscoveryQuery {
  status?: CandidateStatus;
  source?: DiscoverySource;
  search?: string;
  conflict?: boolean;
  first_seen_from?: string;
  first_seen_to?: string;
  last_seen_from?: string;
  last_seen_to?: string;
  limit?: number;
  offset?: number;
}

export interface ClaimCandidateInput {
  expected_candidate_version: number;
  device_id: string;
  display_name: string;
  device_type: string;
  area_id: string | null;
  importance: DeviceImportance;
  profile_source: Exclude<DeviceProfileSource, 'unclassified'>;
  resolve_identity_conflict?: boolean;
}

export interface ClaimCandidateResponse {
  device: DeviceDetail;
  candidate: DiscoveryCandidate;
  credential_provisioning_required: true;
  provisioning_message: string;
}

export type DiscoveryApiErrorKind =
  | 'bad_request' | 'unauthorized' | 'forbidden' | 'not_found' | 'conflict'
  | 'too_large' | 'unavailable' | 'network' | 'invalid_response' | 'http' | 'aborted';

export class DiscoveryApiError extends Error {
  readonly kind: DiscoveryApiErrorKind;
  readonly status: number | null;
  readonly code: string | null;
  readonly requestId: string | null;
  constructor(kind: DiscoveryApiErrorKind, message: string, options: {
    status?: number; code?: string; requestId?: string; cause?: unknown;
  } = {}) {
    super(message, { cause: options.cause });
    this.name = 'DiscoveryApiError';
    this.kind = kind;
    this.status = options.status ?? null;
    this.code = options.code ?? null;
    this.requestId = options.requestId ?? null;
  }
}

type JsonObject = Record<string, unknown>;
const STATUSES = ['pending', 'ignored', 'claimed', 'conflict'] as const;
const SOURCES = ['mqtt_unknown', 'dhcp', 'arp', 'probe', 'other'] as const;

function invalid(label: string): never {
  throw new DiscoveryApiError('invalid_response', `设备发现响应字段无效：${label}`);
}
function object(value: unknown, label: string): JsonObject {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return invalid(label);
  return value as JsonObject;
}
function text(value: unknown, label: string): string {
  if (typeof value !== 'string' || value.length === 0) return invalid(label);
  return value;
}
function timestamp(value: unknown, label: string): string {
  const result = text(value, label);
  if (!/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?(?:Z|[+-]\d\d:\d\d)$/.test(result)
    || !Number.isFinite(Date.parse(result))) return invalid(label);
  return result;
}
function nullableText(value: unknown, label: string): string | null {
  return value === null ? null : text(value, label);
}
function integer(value: unknown, label: string, minimum = 0): number {
  if (!Number.isSafeInteger(value) || (value as number) < minimum) return invalid(label);
  return value as number;
}
function oneOf<T extends readonly string[]>(value: unknown, values: T, label: string): T[number] {
  if (typeof value !== 'string' || !values.includes(value)) return invalid(label);
  return value as T[number];
}
function optional<T>(value: unknown, parse: (item: unknown, label: string) => T, label: string): T | undefined {
  return value === undefined ? undefined : parse(value, label);
}
function stringList<T extends string>(value: unknown, choices: readonly T[], label: string): T[] {
  if (!Array.isArray(value)) return invalid(label);
  return value.map((item, index) => oneOf(item, choices, `${label}[${index}]`));
}
function parseMetadata(value: unknown): Record<string, string | number> {
  const data = object(value, 'sanitized_metadata');
  const result: Record<string, string | number> = {};
  for (const [key, item] of Object.entries(data)) {
    if (!['device_type_hint', 'firmware_version', 'sequence', 'boot_id_digest'].includes(key)) return invalid('sanitized_metadata');
    if (typeof item !== 'string' && typeof item !== 'number') return invalid('sanitized_metadata');
    result[key] = item;
  }
  return result;
}

export function parseDiscoveryCandidate(value: unknown): DiscoveryCandidate {
  const candidate = object(value, 'candidate');
  const base: DiscoveryCandidate = {
    candidate_id: text(candidate.candidate_id, 'candidate_id'),
    proposed_device_id: nullableText(candidate.proposed_device_id, 'proposed_device_id'),
    status: oneOf(candidate.status, STATUSES, 'status'),
    conflict: typeof candidate.conflict === 'boolean' ? candidate.conflict : invalid('conflict'),
    conflict_reason: nullableText(candidate.conflict_reason, 'conflict_reason'),
    first_seen_at: timestamp(candidate.first_seen_at, 'first_seen_at'),
    last_seen_at: timestamp(candidate.last_seen_at, 'last_seen_at'),
    source_count: integer(candidate.source_count, 'source_count'),
    observation_count: integer(candidate.observation_count, 'observation_count'),
    claimed_device_id: nullableText(candidate.claimed_device_id, 'claimed_device_id'),
    claimed_at: candidate.claimed_at === null ? null : timestamp(candidate.claimed_at, 'claimed_at'),
    ignored_at: candidate.ignored_at === null ? null : timestamp(candidate.ignored_at, 'ignored_at'),
    ignored_reason: nullableText(candidate.ignored_reason, 'ignored_reason'),
    candidate_version: integer(candidate.candidate_version, 'candidate_version', 1),
    updated_at: timestamp(candidate.updated_at, 'updated_at'),
    sources: optional(candidate.sources, (item, label) => stringList(item, SOURCES, label), 'sources'),
    device_type_hint: candidate.device_type_hint === undefined
      ? undefined : nullableText(candidate.device_type_hint, 'device_type_hint'),
    identity_kind: candidate.identity_kind === undefined
      ? undefined : oneOf(candidate.identity_kind, ['mac'] as const, 'identity_kind'),
    mac_address: candidate.mac_address === undefined ? undefined
      : (/^(?:[0-9A-F]{2}:){5}[0-9A-F]{2}$/.test(text(candidate.mac_address, 'mac_address'))
        ? candidate.mac_address as string : invalid('mac_address')),
    latest_ip: candidate.latest_ip === undefined ? undefined : nullableText(candidate.latest_ip, 'latest_ip'),
    conflicting_proposed_device_ids: candidate.conflicting_proposed_device_ids === undefined
      ? undefined : (Array.isArray(candidate.conflicting_proposed_device_ids)
        ? candidate.conflicting_proposed_device_ids.map((item, index) => text(item, `conflicting_proposed_device_ids[${index}]`))
        : invalid('conflicting_proposed_device_ids')),
  };
  if (candidate.observations !== undefined) {
    if (!Array.isArray(candidate.observations) || candidate.observations.length > 50) return invalid('observations');
    base.observations = candidate.observations.map((raw, index) => {
      const item = object(raw, `observations[${index}]`);
      return {
        source: oneOf(item.source, SOURCES, 'observation.source'),
        observed_at: timestamp(item.observed_at, 'observation.observed_at'),
        received_at: timestamp(item.received_at, 'observation.received_at'),
        proposed_device_id: nullableText(item.proposed_device_id, 'observation.proposed_device_id'),
        sanitized_metadata: parseMetadata(item.sanitized_metadata),
        ...(item.ip_address === undefined ? {} : { ip_address: nullableText(item.ip_address, 'observation.ip_address') }),
        ...(item.evidence_hash === undefined ? {} : { evidence_hash: text(item.evidence_hash, 'observation.evidence_hash') }),
      };
    });
  }
  return base;
}

export function parseDiscoveryList(value: unknown): DiscoveryListResponse {
  const response = object(value, 'discovery list');
  if (!Array.isArray(response.items)) return invalid('items');
  return {
    items: response.items.map(parseDiscoveryCandidate),
    total: integer(response.total, 'total'),
    limit: integer(response.limit, 'limit', 1),
    offset: integer(response.offset, 'offset'),
  };
}

function parseError(value: unknown): { code: string; message: string; requestId: string } | null {
  try {
    const wrapper = object(value, 'error');
    const item = object(wrapper.error, 'error.error');
    return { code: text(item.code, 'error.code'), message: text(item.message, 'error.message'), requestId: text(item.request_id, 'error.request_id') };
  } catch { return null; }
}
function kindForStatus(status: number): DiscoveryApiErrorKind {
  return ({ 400: 'bad_request', 401: 'unauthorized', 403: 'forbidden', 404: 'not_found', 409: 'conflict', 413: 'too_large', 503: 'unavailable' } as Record<number, DiscoveryApiErrorKind>)[status] ?? 'http';
}
function isAbort(error: unknown): boolean {
  return error instanceof Error && error.name === 'AbortError';
}

export class V3DiscoveryClient {
  private csrfToken: string | null = null;

  private async fetch(url: string, init: RequestInit): Promise<Response> {
    try { return await globalThis.fetch(url, { credentials: 'include', cache: 'no-store', ...init }); }
    catch (error) {
      if (isAbort(error)) throw new DiscoveryApiError('aborted', '设备发现请求已取消', { cause: error });
      throw new DiscoveryApiError('network', '无法连接设备发现服务', { cause: error });
    }
  }
  private async body(response: Response): Promise<unknown> {
    try { return await response.json(); }
    catch (error) { throw new DiscoveryApiError('invalid_response', '服务返回了无法解析的 JSON', { status: response.status, cause: error }); }
  }
  private capture(response: Response): void {
    const token = response.headers.get(CSRF_HEADER);
    if (token?.trim()) this.csrfToken = token;
  }
  private throwHttp(response: Response, body: unknown): never {
    const error = parseError(body);
    throw new DiscoveryApiError(kindForStatus(response.status), error?.message ?? `请求失败（HTTP ${response.status}）`, {
      status: response.status, code: error?.code, requestId: error?.requestId,
    });
  }
  async list(query: DiscoveryQuery = {}, signal?: AbortSignal): Promise<DiscoveryListResponse> {
    const params = new URLSearchParams();
    Object.entries(query).forEach(([key, value]) => {
      if (value !== undefined && value !== '') params.set(key, String(value));
    });
    const response = await this.fetch(`${DISCOVERY_ENDPOINT}${params.size ? `?${params}` : ''}`, {
      method: 'GET', headers: { Accept: 'application/json' }, signal,
    });
    const body = await this.body(response);
    if (!response.ok) this.throwHttp(response, body);
    const parsed = parseDiscoveryList(body);
    this.capture(response);
    return parsed;
  }
  async get(candidateId: string, signal?: AbortSignal): Promise<DiscoveryCandidate> {
    const response = await this.fetch(`${DISCOVERY_ENDPOINT}/${encodeURIComponent(candidateId)}`, {
      method: 'GET', headers: { Accept: 'application/json' }, signal,
    });
    const body = await this.body(response);
    if (!response.ok) this.throwHttp(response, body);
    const parsed = parseDiscoveryCandidate(object(body, 'candidate envelope').candidate);
    this.capture(response);
    return parsed;
  }
  private async write<T>(candidateId: string, action: 'claim' | 'ignore' | 'restore', payload: unknown,
    parse: (value: unknown) => T, signal?: AbortSignal): Promise<T> {
    if (!this.csrfToken) await this.list({ limit: 1, offset: 0 }, signal);
    if (!this.csrfToken) throw new DiscoveryApiError('invalid_response', '设备 GET 响应缺少 CSRF token，写操作未发送');
    const response = await this.fetch(`${DISCOVERY_ENDPOINT}/${encodeURIComponent(candidateId)}/${action}`, {
      method: 'POST', headers: { Accept: 'application/json', 'Content-Type': 'application/json', [CSRF_HEADER]: this.csrfToken },
      body: JSON.stringify(payload), signal,
    });
    const body = await this.body(response);
    if (!response.ok) this.throwHttp(response, body);
    return parse(body);
  }
  claim(candidateId: string, input: ClaimCandidateInput, signal?: AbortSignal): Promise<ClaimCandidateResponse> {
    return this.write(candidateId, 'claim', input, (value) => {
      const result = object(value, 'claim response');
      const candidate = parseDiscoveryCandidate(result.candidate);
      if (result.credential_provisioning_required !== true) return invalid('credential_provisioning_required');
      return {
        device: parseDeviceDetail(result.device), candidate,
        credential_provisioning_required: true,
        provisioning_message: text(result.provisioning_message, 'provisioning_message'),
      };
    }, signal);
  }
  ignore(candidateId: string, expectedCandidateVersion: number, reason: string, signal?: AbortSignal): Promise<DiscoveryCandidate> {
    return this.write(candidateId, 'ignore', { expected_candidate_version: expectedCandidateVersion, reason },
      (value) => parseDiscoveryCandidate(object(value, 'ignore response').candidate), signal);
  }
  restore(candidateId: string, expectedCandidateVersion: number, signal?: AbortSignal): Promise<DiscoveryCandidate> {
    return this.write(candidateId, 'restore', { expected_candidate_version: expectedCandidateVersion },
      (value) => parseDiscoveryCandidate(object(value, 'restore response').candidate), signal);
  }
}

export const v3DiscoveryApi = new V3DiscoveryClient();
