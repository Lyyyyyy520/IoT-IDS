export const MONITOR_ENDPOINT = '/api/v3/monitor';
export const EVENTS_ENDPOINT = '/api/v3/events';

export type ConnectionStatus = 'unknown' | 'online' | 'stale' | 'offline';
export type OperationMode = 'active' | 'maintenance' | 'disabled';
export type ComponentReadiness = 'warming_up' | 'ready' | 'degraded';
export type RealtimeEventType =
  | 'device.connection_changed'
  | 'device.telemetry_updated'
  | 'device.inventory_changed'
  | 'device.discovered'
  | 'system.component_changed'
  | 'incident.opened'
  | 'incident.updated'
  | 'incident.recovering'
  | 'incident.resolved';

export interface MonitorDevice {
  device_id: string;
  display_name: string;
  device_type: string;
  area_id: string | null;
  operation_mode: OperationMode;
  connection_status: ConnectionStatus;
  ip_address: string | null;
  state_version: number;
  observed_at: string | null;
  received_at: string | null;
  sources: string[];
}

export interface SystemComponentHealth {
  component_id: string;
  readiness: ComponentReadiness;
  started_at: string;
  ready_at: string | null;
  reason: string | null;
  state_version: number;
  updated_at: string;
}

export interface MonitorCapability {
  available: boolean;
  reason: string | null;
  semantics?: string;
}

export type IncidentStatus =
  | 'open'
  | 'acknowledged'
  | 'recovering'
  | 'resolved'
  | 'false_positive';

export interface MonitorIncidentSummary {
  incident_id: string;
  incident_type: string;
  severity: 'info' | 'low' | 'medium' | 'high' | 'critical';
  status: IncidentStatus;
  source: 'manual' | 'rule' | 'system';
  admin_title: string;
  first_seen_at: string;
  updated_at: string;
  resolved_at?: string | null;
  incident_version: number;
}

export interface MonitorIncidentData {
  active: MonitorIncidentSummary[];
  recent: MonitorIncidentSummary[];
  empty_meaning: 'no_recorded_incidents_not_proven_safe';
}

export interface MonitorSnapshot {
  api_version: 'v3';
  schema_version: 4;
  generated_at: string;
  event_cursor: number;
  devices: MonitorDevice[];
  system_components: SystemComponentHealth[];
  capabilities: {
    graph: MonitorCapability;
    incident: MonitorCapability;
  };
  incidents?: MonitorIncidentData | null;
}

export interface ApiErrorEnvelope {
  error: {
    code: string;
    message: string;
    request_id: string;
  };
}

interface DeviceStateProjection {
  connection_status: ConnectionStatus;
  ip_address: string | null;
  observed_at: string | null;
  received_at: string | null;
  sources: string[];
}

export interface DeviceConnectionChangedEvent {
  event_id: number;
  event_type: 'device.connection_changed';
  occurred_at: string;
  device_id: string;
  state_version: number;
  payload: DeviceStateProjection & {
    from: ConnectionStatus;
    to: ConnectionStatus;
    source: string;
  };
}

export interface DeviceTelemetryUpdatedEvent {
  event_id: number;
  event_type: 'device.telemetry_updated';
  occurred_at: string;
  device_id: string;
  state_version: number;
  payload: DeviceStateProjection & {
    observation_id: number;
    source: string;
  };
}

export interface SystemComponentChangedEvent {
  event_id: number;
  event_type: 'system.component_changed';
  occurred_at: string;
  device_id: null;
  state_version: number;
  payload: {
    component_id: string;
    from: ComponentReadiness | null;
    to: ComponentReadiness;
    previous_reason: string | null;
    readiness: ComponentReadiness;
    started_at: string;
    ready_at: string | null;
    reason: string | null;
    updated_at: string;
  };
}

export type DeviceInventoryAction =
  | 'created'
  | 'updated'
  | 'operation_mode_changed'
  | 'retired'
  | 'restored'
  | 'deleted';

export interface DeviceInventoryChangedEvent {
  event_id: number;
  event_type: 'device.inventory_changed';
  occurred_at: string;
  device_id: null;
  state_version: number;
  payload: {
    action: DeviceInventoryAction;
    device_id: string;
    profile_version: number;
  };
}

export interface DeviceDiscoveredEvent {
  event_id: number;
  event_type: 'device.discovered';
  occurred_at: string;
  device_id: null;
  state_version: number;
  payload: {
    candidate_id: string;
    status: 'pending' | 'ignored' | 'claimed' | 'conflict';
    candidate_version: number;
  };
}

export type IncidentEventType =
  | 'incident.opened'
  | 'incident.updated'
  | 'incident.recovering'
  | 'incident.resolved';

export interface IncidentChangedEvent<
  T extends IncidentEventType = IncidentEventType,
> {
  event_id: number;
  event_type: T;
  occurred_at: string;
  device_id: null;
  state_version: number;
  payload: {
    incident_id: string;
    status: IncidentStatus;
    severity: MonitorIncidentSummary['severity'];
    incident_version: number;
    source?: 'manual' | 'rule' | 'system';
    updated_at?: string;
    affected_device_ids?: string[];
  };
}

export type MonitorRealtimeEvent =
  | DeviceConnectionChangedEvent
  | DeviceTelemetryUpdatedEvent
  | DeviceInventoryChangedEvent
  | DeviceDiscoveredEvent
  | SystemComponentChangedEvent;

export type MonitorStreamEvent =
  | MonitorRealtimeEvent
  | IncidentChangedEvent<'incident.opened'>
  | IncidentChangedEvent<'incident.updated'>
  | IncidentChangedEvent<'incident.recovering'>
  | IncidentChangedEvent<'incident.resolved'>;

export interface SnapshotRequiredEvent {
  event_cursor: number;
  reason: string;
}

export type MonitorApiErrorKind =
  | 'unauthorized'
  | 'forbidden'
  | 'unavailable'
  | 'network'
  | 'invalid_response'
  | 'http'
  | 'aborted';

export class MonitorApiError extends Error {
  readonly kind: MonitorApiErrorKind;
  readonly status: number | null;
  readonly code: string | null;
  readonly requestId: string | null;

  constructor(
    kind: MonitorApiErrorKind,
    message: string,
    options: { status?: number; code?: string; requestId?: string; cause?: unknown } = {},
  ) {
    super(message, { cause: options.cause });
    this.name = 'MonitorApiError';
    this.kind = kind;
    this.status = options.status ?? null;
    this.code = options.code ?? null;
    this.requestId = options.requestId ?? null;
  }
}

type JsonObject = Record<string, unknown>;

function object(value: unknown, label: string): JsonObject {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) {
    throw new MonitorApiError('invalid_response', `${label} 必须是对象`);
  }
  return value as JsonObject;
}

function text(value: unknown, label: string): string {
  if (typeof value !== 'string' || value.length === 0) {
    throw new MonitorApiError('invalid_response', `${label} 必须是非空字符串`);
  }
  return value;
}

function nullableText(value: unknown, label: string): string | null {
  if (value === null) return null;
  return text(value, label);
}

function integer(value: unknown, label: string, minimum = 0): number {
  if (!Number.isSafeInteger(value) || (value as number) < minimum) {
    throw new MonitorApiError('invalid_response', `${label} 必须是安全整数`);
  }
  return value as number;
}

function timestamp(value: unknown, label: string, nullable = false): string | null {
  if (nullable && value === null) return null;
  const parsed = text(value, label);
  if (!/(Z|[+-]\d{2}:\d{2})$/.test(parsed) || Number.isNaN(Date.parse(parsed))) {
    throw new MonitorApiError('invalid_response', `${label} 必须是带时区的 ISO 8601 时间`);
  }
  return parsed;
}

function oneOf<T extends string>(value: unknown, values: readonly T[], label: string): T {
  if (typeof value !== 'string' || !values.includes(value as T)) {
    throw new MonitorApiError('invalid_response', `${label} 值不受支持`);
  }
  return value as T;
}

function stringArray(value: unknown, label: string): string[] {
  if (!Array.isArray(value)) {
    throw new MonitorApiError('invalid_response', `${label} 必须是数组`);
  }
  return value.map((entry, index) => text(entry, `${label}[${index}]`));
}

const CONNECTION_STATUSES = ['unknown', 'online', 'stale', 'offline'] as const;
const OPERATION_MODES = ['active', 'maintenance', 'disabled'] as const;
const READINESS_VALUES = ['warming_up', 'ready', 'degraded'] as const;
const INCIDENT_STATUSES = [
  'open', 'acknowledged', 'recovering', 'resolved', 'false_positive',
] as const;
const INCIDENT_SEVERITIES = ['info', 'low', 'medium', 'high', 'critical'] as const;
const INCIDENT_SOURCES = ['manual', 'rule', 'system'] as const;

function parseDevice(value: unknown, index: number): MonitorDevice {
  const row = object(value, `devices[${index}]`);
  return {
    device_id: text(row.device_id, `devices[${index}].device_id`),
    display_name: text(row.display_name, `devices[${index}].display_name`),
    device_type: text(row.device_type, `devices[${index}].device_type`),
    area_id: nullableText(row.area_id, `devices[${index}].area_id`),
    operation_mode: oneOf(row.operation_mode, OPERATION_MODES, `devices[${index}].operation_mode`),
    connection_status: oneOf(row.connection_status, CONNECTION_STATUSES, `devices[${index}].connection_status`),
    ip_address: nullableText(row.ip_address, `devices[${index}].ip_address`),
    state_version: integer(row.state_version, `devices[${index}].state_version`),
    observed_at: timestamp(row.observed_at, `devices[${index}].observed_at`, true),
    received_at: timestamp(row.received_at, `devices[${index}].received_at`, true),
    sources: stringArray(row.sources, `devices[${index}].sources`),
  };
}

function parseComponent(value: unknown, index: number): SystemComponentHealth {
  const row = object(value, `system_components[${index}]`);
  return {
    component_id: text(row.component_id, `system_components[${index}].component_id`),
    readiness: oneOf(row.readiness, READINESS_VALUES, `system_components[${index}].readiness`),
    started_at: timestamp(row.started_at, `system_components[${index}].started_at`) as string,
    ready_at: timestamp(row.ready_at, `system_components[${index}].ready_at`, true),
    reason: nullableText(row.reason, `system_components[${index}].reason`),
    state_version: integer(row.state_version, `system_components[${index}].state_version`, 1),
    updated_at: timestamp(row.updated_at, `system_components[${index}].updated_at`) as string,
  };
}

function parseCapability(value: unknown, label: string): MonitorCapability {
  const capability = object(value, label);
  if (typeof capability.available !== 'boolean') {
    throw new MonitorApiError('invalid_response', `${label}.available 必须是布尔值`);
  }
  return {
    available: capability.available,
    ...(capability.semantics === undefined
      ? {}
      : { semantics: text(capability.semantics, label + '.semantics') }),
    reason: nullableText(capability.reason, `${label}.reason`),
  };
}

function parseIncidentSummary(
  value: unknown,
  label: string,
): MonitorIncidentSummary {
  const row = object(value, label);
  return {
    incident_id: text(row.incident_id, label + '.incident_id'),
    incident_type: text(row.incident_type, label + '.incident_type'),
    severity: oneOf(row.severity, INCIDENT_SEVERITIES, label + '.severity'),
    status: oneOf(row.status, INCIDENT_STATUSES, label + '.status'),
    source: oneOf(row.source, INCIDENT_SOURCES, label + '.source'),
    admin_title: text(row.admin_title, label + '.admin_title'),
    first_seen_at: timestamp(
      row.first_seen_at, label + '.first_seen_at',
    ) as string,
    updated_at: timestamp(
      row.updated_at, label + '.updated_at',
    ) as string,
    ...(row.resolved_at === undefined
      ? {}
      : {
          resolved_at: timestamp(
            row.resolved_at, label + '.resolved_at', true,
          ),
        }),
    incident_version: integer(
      row.incident_version, label + '.incident_version', 1,
    ),
  };
}

function parseIncidentData(value: unknown): MonitorIncidentData | null {
  if (value === null || value === undefined) return null;
  const data = object(value, 'incidents');
  if (!Array.isArray(data.active) || !Array.isArray(data.recent)) {
    throw new MonitorApiError(
      'invalid_response', 'incidents 列表字段格式错误',
    );
  }
  if (
    data.empty_meaning
    !== 'no_recorded_incidents_not_proven_safe'
  ) {
    throw new MonitorApiError(
      'invalid_response', 'incidents.empty_meaning 不受支持',
    );
  }
  return {
    active: data.active.map((entry, index) =>
      parseIncidentSummary(
        entry, 'incidents.active[' + index + ']',
      )),
    recent: data.recent.map((entry, index) =>
      parseIncidentSummary(
        entry, 'incidents.recent[' + index + ']',
      )),
    empty_meaning: data.empty_meaning,
  };
}

export function parseMonitorSnapshot(value: unknown): MonitorSnapshot {
  const snapshot = object(value, 'monitor');
  if (snapshot.api_version !== 'v3' || snapshot.schema_version !== 4) {
    throw new MonitorApiError('invalid_response', 'monitor API 或 schema 版本不受支持');
  }
  if (!Array.isArray(snapshot.devices) || !Array.isArray(snapshot.system_components)) {
    throw new MonitorApiError('invalid_response', 'monitor 列表字段格式错误');
  }
  const capabilities = object(snapshot.capabilities, 'capabilities');
  return {
    api_version: 'v3',
    schema_version: 4,
    generated_at: timestamp(snapshot.generated_at, 'generated_at') as string,
    event_cursor: integer(snapshot.event_cursor, 'event_cursor'),
    devices: snapshot.devices.map(parseDevice),
    system_components: snapshot.system_components.map(parseComponent),
    capabilities: {
      graph: parseCapability(capabilities.graph, 'capabilities.graph'),
      incident: parseCapability(capabilities.incident, 'capabilities.incident'),
    },
    ...(snapshot.incidents === undefined
      ? {}
      : { incidents: parseIncidentData(snapshot.incidents) }),
  };
}

function parseErrorEnvelope(value: unknown): ApiErrorEnvelope | null {
  try {
    const wrapper = object(value, 'error response');
    const error = object(wrapper.error, 'error');
    return {
      error: {
        code: text(error.code, 'error.code'),
        message: text(error.message, 'error.message'),
        request_id: text(error.request_id, 'error.request_id'),
      },
    };
  } catch {
    return null;
  }
}

export async function fetchMonitorSnapshot(signal?: AbortSignal): Promise<MonitorSnapshot> {
  let response: Response;
  try {
    response = await fetch(MONITOR_ENDPOINT, {
      credentials: 'include',
      headers: { Accept: 'application/json' },
      cache: 'no-store',
      signal,
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') {
      throw new MonitorApiError('aborted', 'monitor 请求已取消', { cause: error });
    }
    throw new MonitorApiError('network', '无法连接监视服务', { cause: error });
  }

  let body: unknown;
  try {
    body = await response.json();
  } catch (error) {
    throw new MonitorApiError('invalid_response', '服务返回了无法解析的响应', {
      status: response.status,
      cause: error,
    });
  }

  if (!response.ok) {
    const envelope = parseErrorEnvelope(body);
    const kind: MonitorApiErrorKind =
      response.status === 401
        ? 'unauthorized'
        : response.status === 403
          ? 'forbidden'
          : response.status === 503
            ? 'unavailable'
            : 'http';
    throw new MonitorApiError(kind, envelope?.error.message ?? `monitor 请求失败（${response.status}）`, {
      status: response.status,
      code: envelope?.error.code,
      requestId: envelope?.error.request_id,
    });
  }

  return parseMonitorSnapshot(body);
}

function parseProjection(payload: JsonObject): DeviceStateProjection {
  return {
    connection_status: oneOf(payload.connection_status, CONNECTION_STATUSES, 'payload.connection_status'),
    ip_address: nullableText(payload.ip_address, 'payload.ip_address'),
    observed_at: timestamp(payload.observed_at, 'payload.observed_at', true),
    received_at: timestamp(payload.received_at, 'payload.received_at', true),
    sources: stringArray(payload.sources, 'payload.sources'),
  };
}

export function parseMonitorEvent(eventType: RealtimeEventType, data: string): MonitorStreamEvent {
  let raw: unknown;
  try {
    raw = JSON.parse(data);
  } catch (error) {
    throw new MonitorApiError('invalid_response', '实时事件不是合法 JSON', { cause: error });
  }
  const envelope = object(raw, 'event');
  if (envelope.event_type !== eventType) {
    throw new MonitorApiError('invalid_response', '实时事件名与信封不一致');
  }
  const common = {
    event_id: integer(envelope.event_id, 'event.event_id', 1),
    occurred_at: timestamp(envelope.occurred_at, 'event.occurred_at') as string,
    state_version: integer(envelope.state_version, 'event.state_version', 1),
  };
  const payload = object(envelope.payload, 'event.payload');

  if (
    eventType === 'incident.opened'
    || eventType === 'incident.updated'
    || eventType === 'incident.recovering'
    || eventType === 'incident.resolved'
  ) {
    if (envelope.device_id !== null) {
      throw new MonitorApiError(
        'invalid_response', '事件工作流 SSE 不得携带 device_id',
      );
    }
    return {
      ...common,
      event_type: eventType,
      device_id: null,
      payload: {
        incident_id: text(payload.incident_id, 'payload.incident_id'),
        status: oneOf(payload.status, INCIDENT_STATUSES, 'payload.status'),
        severity: oneOf(payload.severity, INCIDENT_SEVERITIES, 'payload.severity'),
        incident_version: integer(
          payload.incident_version, 'payload.incident_version', 1,
        ),
        ...(payload.source === undefined
          ? {}
          : {
              source: oneOf(
                payload.source, INCIDENT_SOURCES, 'payload.source',
              ),
            }),
        ...(payload.updated_at === undefined
          ? {}
          : {
              updated_at: timestamp(
                payload.updated_at, 'payload.updated_at',
              ) as string,
            }),
        ...(payload.affected_device_ids === undefined
          ? {}
          : {
              affected_device_ids: stringArray(
                payload.affected_device_ids,
                'payload.affected_device_ids',
              ),
            }),
      },
    };
  }

  if (eventType === 'device.inventory_changed') {
    if (envelope.device_id !== null) {
      throw new MonitorApiError('invalid_response', '设备清单事件不得使用外键 device_id');
    }
    return {
      ...common,
      event_type: eventType,
      device_id: null,
      payload: {
        action: oneOf(
          payload.action,
          ['created', 'updated', 'operation_mode_changed', 'retired', 'restored', 'deleted'] as const,
          'payload.action',
        ),
        device_id: text(payload.device_id, 'payload.device_id'),
        profile_version: integer(payload.profile_version, 'payload.profile_version', 1),
      },
    };
  }

  if (eventType === 'device.discovered') {
    if (envelope.device_id !== null) {
      throw new MonitorApiError('invalid_response', '设备发现事件不得携带可信 device_id');
    }
    return {
      ...common,
      event_type: eventType,
      device_id: null,
      payload: {
        candidate_id: text(payload.candidate_id, 'payload.candidate_id'),
        status: oneOf(payload.status, ['pending', 'ignored', 'claimed', 'conflict'] as const, 'payload.status'),
        candidate_version: integer(payload.candidate_version, 'payload.candidate_version', 1),
      },
    };
  }

  if (eventType === 'system.component_changed') {
    if (envelope.device_id !== null) {
      throw new MonitorApiError('invalid_response', '组件事件不得携带 device_id');
    }
    return {
      ...common,
      event_type: eventType,
      device_id: null,
      payload: {
        component_id: text(payload.component_id, 'payload.component_id'),
        from: payload.from === null ? null : oneOf(payload.from, READINESS_VALUES, 'payload.from'),
        to: oneOf(payload.to, READINESS_VALUES, 'payload.to'),
        previous_reason: nullableText(payload.previous_reason, 'payload.previous_reason'),
        readiness: oneOf(payload.readiness, READINESS_VALUES, 'payload.readiness'),
        started_at: timestamp(payload.started_at, 'payload.started_at') as string,
        ready_at: timestamp(payload.ready_at, 'payload.ready_at', true),
        reason: nullableText(payload.reason, 'payload.reason'),
        updated_at: timestamp(payload.updated_at, 'payload.updated_at') as string,
      },
    };
  }

  const deviceId = text(envelope.device_id, 'event.device_id');
  const projection = parseProjection(payload);
  if (eventType === 'device.connection_changed') {
    return {
      ...common,
      event_type: eventType,
      device_id: deviceId,
      payload: {
        ...projection,
        from: oneOf(payload.from, CONNECTION_STATUSES, 'payload.from'),
        to: oneOf(payload.to, CONNECTION_STATUSES, 'payload.to'),
        source: text(payload.source, 'payload.source'),
      },
    };
  }
  return {
    ...common,
    event_type: eventType,
    device_id: deviceId,
    payload: {
      ...projection,
      observation_id: integer(payload.observation_id, 'payload.observation_id', 1),
      source: text(payload.source, 'payload.source'),
    },
  };
}

export function parseSnapshotRequired(data: string): SnapshotRequiredEvent {
  let raw: unknown;
  try {
    raw = JSON.parse(data);
  } catch (error) {
    throw new MonitorApiError('invalid_response', 'snapshot.required 不是合法 JSON', { cause: error });
  }
  const event = object(raw, 'snapshot.required');
  return {
    event_cursor: integer(event.event_cursor, 'snapshot.required.event_cursor'),
    reason: text(event.reason, 'snapshot.required.reason'),
  };
}
