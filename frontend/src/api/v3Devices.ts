export const DEVICES_ENDPOINT = '/api/v3/devices';
export const CSRF_HEADER = 'X-CSRF-Token';

export type ConnectionStatus = 'unknown' | 'online' | 'stale' | 'offline';
export type OperationMode = 'active' | 'maintenance' | 'disabled';
export type DeviceImportance = 'low' | 'normal' | 'high' | 'critical';
export type DeviceProfileSource = 'unclassified' | 'physical' | 'virtual' | 'gateway';
export type DeviceLifecycleStatus = 'active' | 'retired';

export interface DeviceListItem {
  device_id: string;
  display_name: string;
  device_type: string;
  area_id: string | null;
  importance: DeviceImportance;
  profile_source: DeviceProfileSource;
  profile_version: number;
  operation_mode: OperationMode;
  retired_at: string | null;
  retirement_reason: string | null;
  connection_status: ConnectionStatus;
  ip_address: string | null;
  state_version: number;
  last_received_at: string | null;
  lifecycle_status: DeviceLifecycleStatus;
}

export interface DeviceReferences {
  state_observations: number;
  mqtt_boot_sessions: number;
  mqtt_cursor: number;
  non_management_events: number;
  management_audits: number;
  current_state_placeholder: number;
  future_references: Record<string, number>;
}

export interface DeviceDetail extends Omit<DeviceListItem, 'last_received_at'> {
  mac_address: string;
  created_at: string;
  updated_at: string;
  observed_at: string | null;
  received_at: string | null;
  sources: string[];
  references: DeviceReferences;
  can_delete: boolean;
  delete_blocking_reasons: string[];
  credential_revocation_required?: boolean;
  credential_reverification_required?: boolean;
}

export interface DeviceListResponse {
  items: DeviceListItem[];
  total: number;
  limit: number;
  offset: number;
}

export interface DeviceListQuery {
  search?: string;
  connection_status?: ConnectionStatus;
  operation_mode?: OperationMode;
  area_id?: string;
  retired?: boolean;
  limit?: number;
  offset?: number;
}

export interface CreateDeviceInput {
  device_id: string;
  mac: string;
  display_name: string;
  device_type: string;
  area_id?: string | null;
  importance: DeviceImportance;
  profile_source: Exclude<DeviceProfileSource, 'unclassified'>;
}

export interface UpdateDeviceInput {
  display_name?: string;
  device_type?: string;
  area_id?: string | null;
  importance?: DeviceImportance;
  expected_profile_version: number;
}

interface OperationModeUpdateInput {
  operation_mode: OperationMode;
  expected_profile_version: number;
}

export interface DeleteDeviceResponse {
  device_id: string;
  deleted: true;
  retained_management_audit: true;
}

export type DeviceApiErrorKind =
  | 'bad_request'
  | 'unauthorized'
  | 'forbidden'
  | 'not_found'
  | 'conflict'
  | 'too_large'
  | 'unavailable'
  | 'network'
  | 'invalid_response'
  | 'http'
  | 'aborted';

export class DeviceApiError extends Error {
  readonly kind: DeviceApiErrorKind;
  readonly status: number | null;
  readonly code: string | null;
  readonly requestId: string | null;
  readonly details: Record<string, unknown> | null;

  constructor(
    kind: DeviceApiErrorKind,
    message: string,
    options: {
      status?: number;
      code?: string;
      requestId?: string;
      details?: Record<string, unknown>;
      cause?: unknown;
    } = {},
  ) {
    super(message, { cause: options.cause });
    this.name = 'DeviceApiError';
    this.kind = kind;
    this.status = options.status ?? null;
    this.code = options.code ?? null;
    this.requestId = options.requestId ?? null;
    this.details = options.details ?? null;
  }
}

type JsonObject = Record<string, unknown>;

const CONNECTIONS = ['unknown', 'online', 'stale', 'offline'] as const;
const MODES = ['active', 'maintenance', 'disabled'] as const;
const IMPORTANCE = ['low', 'normal', 'high', 'critical'] as const;
const SOURCES = ['unclassified', 'physical', 'virtual', 'gateway'] as const;
const LIFECYCLES = ['active', 'retired'] as const;

function invalid(message: string): never {
  throw new DeviceApiError('invalid_response', message);
}

function object(value: unknown, label: string): JsonObject {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    invalid(`${label} 必须是对象`);
  }
  return value as JsonObject;
}

function text(value: unknown, label: string): string {
  if (typeof value !== 'string' || !value) invalid(`${label} 必须是非空字符串`);
  return value as string;
}

function nullableText(value: unknown, label: string): string | null {
  if (value === null) return null;
  return text(value, label);
}

function integer(value: unknown, label: string, minimum = 0): number {
  if (!Number.isInteger(value) || (value as number) < minimum) {
    invalid(`${label} 必须是大于等于 ${minimum} 的整数`);
  }
  return value as number;
}

function boolean(value: unknown, label: string): boolean {
  if (typeof value !== 'boolean') invalid(`${label} 必须是布尔值`);
  return value as boolean;
}

function oneOf<T extends readonly string[]>(value: unknown, allowed: T, label: string): T[number] {
  if (typeof value !== 'string' || !allowed.includes(value)) {
    invalid(`${label} 包含不支持的枚举值`);
  }
  return value as T[number];
}

function timestamp(value: unknown, label: string, nullable = false): string | null {
  if (nullable && value === null) return null;
  const parsed = text(value, label);
  if (!/(Z|[+-]\d{2}:\d{2})$/.test(parsed) || Number.isNaN(Date.parse(parsed))) {
    invalid(`${label} 必须是带时区的 ISO 8601 时间`);
  }
  return parsed;
}

function stringArray(value: unknown, label: string): string[] {
  if (!Array.isArray(value)) invalid(`${label} 必须是字符串数组`);
  return value.map((item, index) => text(item, `${label}[${index}]`));
}

function numberMap(value: unknown, label: string): Record<string, number> {
  const raw = object(value, label);
  return Object.fromEntries(
    Object.entries(raw).map(([key, count]) => [key, integer(count, `${label}.${key}`)]),
  );
}

function parseBaseDevice(raw: JsonObject) {
  return {
    device_id: text(raw.device_id, 'device.device_id'),
    display_name: text(raw.display_name, 'device.display_name'),
    device_type: text(raw.device_type, 'device.device_type'),
    area_id: nullableText(raw.area_id, 'device.area_id'),
    importance: oneOf(raw.importance, IMPORTANCE, 'device.importance'),
    profile_source: oneOf(raw.profile_source, SOURCES, 'device.profile_source'),
    profile_version: integer(raw.profile_version, 'device.profile_version', 1),
    operation_mode: oneOf(raw.operation_mode, MODES, 'device.operation_mode'),
    retired_at: timestamp(raw.retired_at, 'device.retired_at', true),
    retirement_reason: nullableText(raw.retirement_reason, 'device.retirement_reason'),
    connection_status: oneOf(raw.connection_status, CONNECTIONS, 'device.connection_status'),
    ip_address: nullableText(raw.ip_address, 'device.ip_address'),
    state_version: integer(raw.state_version, 'device.state_version'),
    lifecycle_status: oneOf(raw.lifecycle_status, LIFECYCLES, 'device.lifecycle_status'),
  };
}

export function parseDeviceListItem(value: unknown): DeviceListItem {
  const raw = object(value, 'device');
  return {
    ...parseBaseDevice(raw),
    last_received_at: timestamp(raw.last_received_at, 'device.last_received_at', true),
  };
}

function parseReferences(value: unknown): DeviceReferences {
  const raw = object(value, 'device.references');
  return {
    state_observations: integer(raw.state_observations, 'references.state_observations'),
    mqtt_boot_sessions: integer(raw.mqtt_boot_sessions, 'references.mqtt_boot_sessions'),
    mqtt_cursor: integer(raw.mqtt_cursor, 'references.mqtt_cursor'),
    non_management_events: integer(raw.non_management_events, 'references.non_management_events'),
    management_audits: integer(raw.management_audits, 'references.management_audits'),
    current_state_placeholder: integer(raw.current_state_placeholder, 'references.current_state_placeholder'),
    future_references: numberMap(raw.future_references, 'references.future_references'),
  };
}

export function parseDeviceDetail(value: unknown): DeviceDetail {
  const raw = object(value, 'device');
  const result: DeviceDetail = {
    ...parseBaseDevice(raw),
    mac_address: text(raw.mac_address, 'device.mac_address'),
    created_at: timestamp(raw.created_at, 'device.created_at') as string,
    updated_at: timestamp(raw.updated_at, 'device.updated_at') as string,
    observed_at: timestamp(raw.observed_at, 'device.observed_at', true),
    received_at: timestamp(raw.received_at, 'device.received_at', true),
    sources: stringArray(raw.sources, 'device.sources'),
    references: parseReferences(raw.references),
    can_delete: boolean(raw.can_delete, 'device.can_delete'),
    delete_blocking_reasons: stringArray(raw.delete_blocking_reasons, 'device.delete_blocking_reasons'),
  };
  if (raw.credential_revocation_required !== undefined) {
    result.credential_revocation_required = boolean(
      raw.credential_revocation_required,
      'device.credential_revocation_required',
    );
  }
  if (raw.credential_reverification_required !== undefined) {
    result.credential_reverification_required = boolean(
      raw.credential_reverification_required,
      'device.credential_reverification_required',
    );
  }
  return result;
}

export function parseDeviceListResponse(value: unknown): DeviceListResponse {
  const raw = object(value, 'device list');
  if (!Array.isArray(raw.items)) invalid('device list.items 必须是数组');
  return {
    items: raw.items.map(parseDeviceListItem),
    total: integer(raw.total, 'device list.total'),
    limit: integer(raw.limit, 'device list.limit', 1),
    offset: integer(raw.offset, 'device list.offset'),
  };
}

function parseDeviceEnvelope(value: unknown): DeviceDetail {
  const raw = object(value, 'device response');
  return parseDeviceDetail(raw.device);
}

function parseDeleteResponse(value: unknown): DeleteDeviceResponse {
  const raw = object(value, 'delete response');
  if (raw.deleted !== true || raw.retained_management_audit !== true) {
    invalid('删除响应缺少确认字段');
  }
  return {
    device_id: text(raw.device_id, 'delete response.device_id'),
    deleted: true,
    retained_management_audit: true,
  };
}

interface ParsedErrorEnvelope {
  code: string;
  message: string;
  requestId: string;
  details?: Record<string, unknown>;
}

function parseErrorEnvelope(value: unknown): ParsedErrorEnvelope | null {
  try {
    const wrapper = object(value, 'error response');
    const error = object(wrapper.error, 'error');
    const parsed: ParsedErrorEnvelope = {
      code: text(error.code, 'error.code'),
      message: text(error.message, 'error.message'),
      requestId: text(error.request_id, 'error.request_id'),
    };
    if (error.details !== undefined) parsed.details = object(error.details, 'error.details');
    return parsed;
  } catch {
    return null;
  }
}

function errorKind(status: number): DeviceApiErrorKind {
  if (status === 400) return 'bad_request';
  if (status === 401) return 'unauthorized';
  if (status === 403) return 'forbidden';
  if (status === 404) return 'not_found';
  if (status === 409) return 'conflict';
  if (status === 413) return 'too_large';
  if (status === 503) return 'unavailable';
  return 'http';
}

function isAbort(error: unknown): boolean {
  return error instanceof DOMException && error.name === 'AbortError'
    || error instanceof Error && error.name === 'AbortError';
}

export interface DevicesApi {
  listDevices(query?: DeviceListQuery, signal?: AbortSignal): Promise<DeviceListResponse>;
  getDevice(deviceId: string, signal?: AbortSignal): Promise<DeviceDetail>;
  createDevice(input: CreateDeviceInput, signal?: AbortSignal): Promise<DeviceDetail>;
  updateDevice(deviceId: string, input: UpdateDeviceInput, signal?: AbortSignal): Promise<DeviceDetail>;
  setOperationMode(deviceId: string, mode: OperationMode, expectedVersion: number, signal?: AbortSignal): Promise<DeviceDetail>;
  retireDevice(deviceId: string, reason: string, expectedVersion: number, signal?: AbortSignal): Promise<DeviceDetail>;
  restoreDevice(deviceId: string, expectedVersion: number, signal?: AbortSignal): Promise<DeviceDetail>;
  deleteDevice(deviceId: string, confirmation: string, signal?: AbortSignal): Promise<DeleteDeviceResponse>;
}

export class V3DevicesClient implements DevicesApi {
  private csrfToken: string | null = null;

  private async fetchResponse(url: string, init: RequestInit): Promise<Response> {
    try {
      return await fetch(url, { credentials: 'include', cache: 'no-store', ...init });
    } catch (error) {
      if (isAbort(error)) {
        throw new DeviceApiError('aborted', '设备请求已取消', { cause: error });
      }
      throw new DeviceApiError('network', '无法连接设备管理服务', { cause: error });
    }
  }

  private async body(response: Response): Promise<unknown> {
    try {
      return await response.json();
    } catch (error) {
      throw new DeviceApiError('invalid_response', '服务返回了无法解析的 JSON', {
        status: response.status,
        cause: error,
      });
    }
  }

  private throwHttp(response: Response, body: unknown): never {
    const envelope = parseErrorEnvelope(body);
    throw new DeviceApiError(
      errorKind(response.status),
      envelope?.message ?? `设备请求失败（HTTP ${response.status}）`,
      {
        status: response.status,
        code: envelope?.code,
        requestId: envelope?.requestId,
        details: envelope?.details,
      },
    );
  }

  private captureCsrf(response: Response): void {
    const token = response.headers.get(CSRF_HEADER);
    if (token?.trim()) this.csrfToken = token;
  }

  private async read<T>(url: string, parser: (value: unknown) => T, signal?: AbortSignal): Promise<T> {
    const response = await this.fetchResponse(url, {
      method: 'GET',
      headers: { Accept: 'application/json' },
      signal,
    });
    const body = await this.body(response);
    if (!response.ok) this.throwHttp(response, body);
    const result = parser(body);
    this.captureCsrf(response);
    return result;
  }

  private async ensureCsrf(signal?: AbortSignal): Promise<string> {
    if (!this.csrfToken) {
      await this.listDevices({ limit: 1, offset: 0 }, signal);
    }
    if (!this.csrfToken) {
      throw new DeviceApiError(
        'invalid_response',
        '设备 GET 响应缺少 CSRF token，写操作未发送',
      );
    }
    return this.csrfToken;
  }

  private async write<T>(
    url: string,
    method: 'POST' | 'PATCH' | 'DELETE',
    payload: unknown,
    parser: (value: unknown) => T,
    signal?: AbortSignal,
  ): Promise<T> {
    const csrf = await this.ensureCsrf(signal);
    const response = await this.fetchResponse(url, {
      method,
      headers: {
        Accept: 'application/json',
        'Content-Type': 'application/json',
        [CSRF_HEADER]: csrf,
      },
      body: JSON.stringify(payload),
      signal,
    });
    const body = await this.body(response);
    if (!response.ok) this.throwHttp(response, body);
    return parser(body);
  }

  async listDevices(query: DeviceListQuery = {}, signal?: AbortSignal): Promise<DeviceListResponse> {
    const params = new URLSearchParams();
    if (query.search) params.set('search', query.search);
    if (query.connection_status) params.set('connection_status', query.connection_status);
    if (query.operation_mode) params.set('operation_mode', query.operation_mode);
    if (query.area_id) params.set('area_id', query.area_id);
    if (query.retired !== undefined) params.set('retired', String(query.retired));
    if (query.limit !== undefined) params.set('limit', String(query.limit));
    if (query.offset !== undefined) params.set('offset', String(query.offset));
    const suffix = params.size ? `?${params.toString()}` : '';
    return this.read(`${DEVICES_ENDPOINT}${suffix}`, parseDeviceListResponse, signal);
  }

  getDevice(deviceId: string, signal?: AbortSignal): Promise<DeviceDetail> {
    return this.read(
      `${DEVICES_ENDPOINT}/${encodeURIComponent(deviceId)}`,
      parseDeviceEnvelope,
      signal,
    );
  }

  createDevice(input: CreateDeviceInput, signal?: AbortSignal): Promise<DeviceDetail> {
    return this.write(DEVICES_ENDPOINT, 'POST', input, parseDeviceEnvelope, signal);
  }

  private patchDevice(
    deviceId: string,
    input: UpdateDeviceInput | OperationModeUpdateInput,
    signal?: AbortSignal,
  ): Promise<DeviceDetail> {
    return this.write(
      `${DEVICES_ENDPOINT}/${encodeURIComponent(deviceId)}`,
      'PATCH',
      input,
      parseDeviceEnvelope,
      signal,
    );
  }

  updateDevice(deviceId: string, input: UpdateDeviceInput, signal?: AbortSignal): Promise<DeviceDetail> {
    return this.patchDevice(deviceId, input, signal);
  }

  setOperationMode(
    deviceId: string,
    mode: OperationMode,
    expectedVersion: number,
    signal?: AbortSignal,
  ): Promise<DeviceDetail> {
    return this.patchDevice(
      deviceId,
      { operation_mode: mode, expected_profile_version: expectedVersion },
      signal,
    );
  }

  retireDevice(
    deviceId: string,
    reason: string,
    expectedVersion: number,
    signal?: AbortSignal,
  ): Promise<DeviceDetail> {
    return this.write(
      `${DEVICES_ENDPOINT}/${encodeURIComponent(deviceId)}/retire`,
      'POST',
      { reason, expected_profile_version: expectedVersion },
      parseDeviceEnvelope,
      signal,
    );
  }

  restoreDevice(deviceId: string, expectedVersion: number, signal?: AbortSignal): Promise<DeviceDetail> {
    return this.write(
      `${DEVICES_ENDPOINT}/${encodeURIComponent(deviceId)}/restore`,
      'POST',
      { expected_profile_version: expectedVersion },
      parseDeviceEnvelope,
      signal,
    );
  }

  deleteDevice(deviceId: string, confirmation: string, signal?: AbortSignal): Promise<DeleteDeviceResponse> {
    return this.write(
      `${DEVICES_ENDPOINT}/${encodeURIComponent(deviceId)}`,
      'DELETE',
      { confirmation },
      parseDeleteResponse,
      signal,
    );
  }
}

export const v3DevicesApi = new V3DevicesClient();
