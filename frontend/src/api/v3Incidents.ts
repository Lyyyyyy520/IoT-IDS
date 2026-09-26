export const INCIDENTS_ENDPOINT = '/api/v3/incidents';
export const SUPPORT_CONTACT_ENDPOINT = '/api/v3/support-contact';
export const HELP_REQUESTS_ENDPOINT = '/api/v3/help-requests';
export const INCIDENT_CSRF_HEADER = 'X-CSRF-Token';

export type IncidentStatus = 'open' | 'acknowledged' | 'recovering' | 'resolved' | 'false_positive';
export type IncidentSeverity = 'info' | 'low' | 'medium' | 'high' | 'critical';
export type IncidentSource = 'manual' | 'rule' | 'system';
export type IncidentRole = 'affected' | 'suspected_source' | 'observer' | 'unknown';
export type HelpStatus = 'open' | 'in_progress' | 'waiting_for_user' | 'closed';
export type HelpCategory = 'device_issue' | 'security_question' | 'service_problem' | 'other';

export interface IncidentListItem {
  incident_id: string;
  incident_type: string;
  severity: IncidentSeverity;
  status: IncidentStatus;
  source: IncidentSource;
  admin_title: string;
  user_title: string;
  first_seen_at: string;
  last_seen_at: string;
  updated_at: string;
  resolved_at: string | null;
  incident_version: number;
  mobile_published: boolean;
  affected_device_count: number;
  latest_public_progress: string | null;
}

export interface IncidentListResponse {
  items: IncidentListItem[];
  total: number;
  limit: number;
  offset: number;
  event_cursor: number;
}

export interface IncidentDevice {
  device_id: string;
  incident_role: IncidentRole;
  user_visible: boolean;
  display_name: string;
  device_type: string;
  area_id: string | null;
}

export interface IncidentTimelineEntry {
  timeline_id: number;
  action: string;
  actor_user_id: number | null;
  actor_username: string;
  actor_role: string;
  occurred_at: string;
  request_id: string;
  public_progress: string | null;
  admin_details: string | null;
  resulting_status: IncidentStatus;
  incident_version: number;
}

export interface IncidentDetail extends IncidentListItem {
  admin_summary: string;
  user_summary: string;
  created_at: string;
  created_by: number;
  resolution_summary: string | null;
  false_positive_reason: string | null;
  devices: IncidentDevice[];
  timeline: IncidentTimelineEntry[];
  user_preview: {
    user_title: string;
    user_summary: string;
    public_progress: string | null;
  };
}

export interface IncidentQuery {
  search?: string;
  status?: IncidentStatus;
  severity?: IncidentSeverity;
  source?: IncidentSource;
  device_id?: string;
  from?: string;
  to?: string;
  limit?: number;
  offset?: number;
}

export interface CreateIncidentInput {
  incident_type: string;
  severity: IncidentSeverity;
  source: 'manual';
  admin_title: string;
  admin_summary: string;
  user_title: string;
  user_summary: string;
  devices: Array<{ device_id: string; incident_role: IncidentRole; user_visible: boolean }>;
  publish_to_mobile: boolean;
  first_seen_at?: string;
  public_progress?: string;
}

export interface TransitionIncidentInput {
  expected_incident_version: number;
  public_progress?: string;
  admin_details?: string;
  resolution_summary?: string;
  false_positive_reason?: string;
}

export interface HelpRequestItem {
  help_request_id: string;
  user_id: number;
  incident_id: string | null;
  device_id: string | null;
  category: HelpCategory;
  user_message: string;
  status: HelpStatus;
  public_response: string | null;
  internal_note: string | null;
  assigned_to: number | null;
  created_at: string;
  updated_at: string;
  closed_at: string | null;
  request_version: number;
}

export interface HelpTimelineEntry {
  timeline_id: number;
  help_request_id: string;
  action: string;
  actor_user_id: number | null;
  actor_username: string;
  actor_role: string;
  occurred_at: string;
  request_id: string;
  public_response: string | null;
  internal_note: string | null;
  resulting_status: HelpStatus;
  request_version: number;
}

export interface HelpRequestDetail extends HelpRequestItem {
  timeline: HelpTimelineEntry[];
}

export interface HelpRequestListResponse {
  items: HelpRequestItem[];
  total: number;
  limit: number;
  offset: number;
}

export interface HelpRequestQuery {
  status?: HelpStatus;
  category?: HelpCategory;
  user_id?: number;
  device_id?: string;
  incident_id?: string;
  from?: string;
  to?: string;
  limit?: number;
  offset?: number;
}

export interface UpdateHelpRequestInput {
  expected_request_version: number;
  status: Exclude<HelpStatus, 'open'>;
  public_response?: string;
  internal_note?: string;
  assigned_to?: number;
}

export type SupportContact =
  | { available: false; reason: string }
  | {
      available: true;
      display_name: string;
      phone: string | null;
      email: string | null;
      working_hours: string | null;
      public_note: string | null;
      enabled: boolean;
      config_version: number;
      updated_by: number;
      updated_at: string;
    };

export interface UpdateSupportContactInput {
  display_name: string;
  phone?: string | null;
  email?: string | null;
  working_hours?: string | null;
  public_note?: string | null;
  enabled: boolean;
  expected_config_version: number;
}

export type IncidentApiErrorKind =
  | 'bad_request' | 'unauthorized' | 'forbidden' | 'not_found' | 'conflict'
  | 'too_large' | 'rate_limited' | 'unavailable' | 'network'
  | 'invalid_response' | 'http' | 'aborted';

export class IncidentApiError extends Error {
  readonly kind: IncidentApiErrorKind;
  readonly status: number | null;
  readonly code: string | null;
  readonly requestId: string | null;

  constructor(
    kind: IncidentApiErrorKind,
    message: string,
    options: { status?: number; code?: string; requestId?: string; cause?: unknown } = {},
  ) {
    super(message, { cause: options.cause });
    this.name = 'IncidentApiError';
    this.kind = kind;
    this.status = options.status ?? null;
    this.code = options.code ?? null;
    this.requestId = options.requestId ?? null;
  }
}

type JsonObject = Record<string, unknown>;
const STATUSES = ['open', 'acknowledged', 'recovering', 'resolved', 'false_positive'] as const;
const SEVERITIES = ['info', 'low', 'medium', 'high', 'critical'] as const;
const SOURCES = ['manual', 'rule', 'system'] as const;
const ROLES = ['affected', 'suspected_source', 'observer', 'unknown'] as const;
const HELP_STATUSES = ['open', 'in_progress', 'waiting_for_user', 'closed'] as const;
const HELP_CATEGORIES = ['device_issue', 'security_question', 'service_problem', 'other'] as const;

function invalid(message: string): never {
  throw new IncidentApiError('invalid_response', message);
}

function object(value: unknown, label: string): JsonObject {
  if (!value || typeof value !== 'object' || Array.isArray(value)) invalid(`${label} 必须是对象`);
  return value as JsonObject;
}

function text(value: unknown, label: string): string {
  if (typeof value !== 'string' || !value) invalid(`${label} 必须是非空字符串`);
  return value;
}

function nullableText(value: unknown, label: string): string | null {
  if (value === null) return null;
  return text(value, label);
}

function integer(value: unknown, label: string, minimum = 0): number {
  if (!Number.isSafeInteger(value) || Number(value) < minimum) invalid(`${label} 必须是有效整数`);
  return Number(value);
}

function nullableInteger(value: unknown, label: string): number | null {
  if (value === null) return null;
  return integer(value, label);
}

function bool(value: unknown, label: string): boolean {
  if (typeof value !== 'boolean') invalid(`${label} 必须是布尔值`);
  return value;
}

function oneOf<T extends readonly string[]>(value: unknown, allowed: T, label: string): T[number] {
  if (typeof value !== 'string' || !allowed.includes(value)) invalid(`${label} 枚举值无效`);
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

function array(value: unknown, label: string): unknown[] {
  if (!Array.isArray(value)) invalid(`${label} 必须是数组`);
  return value;
}

export function parseIncidentListItem(value: unknown): IncidentListItem {
  const row = object(value, 'incident');
  return {
    incident_id: text(row.incident_id, 'incident_id'),
    incident_type: text(row.incident_type, 'incident_type'),
    severity: oneOf(row.severity, SEVERITIES, 'severity'),
    status: oneOf(row.status, STATUSES, 'status'),
    source: oneOf(row.source, SOURCES, 'source'),
    admin_title: text(row.admin_title, 'admin_title'),
    user_title: text(row.user_title, 'user_title'),
    first_seen_at: timestamp(row.first_seen_at, 'first_seen_at') as string,
    last_seen_at: timestamp(row.last_seen_at, 'last_seen_at') as string,
    updated_at: timestamp(row.updated_at, 'updated_at') as string,
    resolved_at: timestamp(row.resolved_at, 'resolved_at', true),
    incident_version: integer(row.incident_version, 'incident_version', 1),
    mobile_published: bool(row.mobile_published, 'mobile_published'),
    affected_device_count: integer(row.affected_device_count, 'affected_device_count'),
    latest_public_progress: nullableText(row.latest_public_progress, 'latest_public_progress'),
  };
}

export function parseIncidentList(value: unknown): IncidentListResponse {
  const row = object(value, 'incident list');
  return {
    items: array(row.items, 'items').map(parseIncidentListItem),
    total: integer(row.total, 'total'),
    limit: integer(row.limit, 'limit', 1),
    offset: integer(row.offset, 'offset'),
    event_cursor: integer(row.event_cursor, 'event_cursor'),
  };
}

function parseIncidentDevice(value: unknown): IncidentDevice {
  const row = object(value, 'incident device');
  return {
    device_id: text(row.device_id, 'device_id'),
    incident_role: oneOf(row.incident_role, ROLES, 'incident_role'),
    user_visible: bool(row.user_visible, 'user_visible'),
    display_name: text(row.display_name, 'display_name'),
    device_type: text(row.device_type, 'device_type'),
    area_id: nullableText(row.area_id, 'area_id'),
  };
}

function parseIncidentTimeline(value: unknown): IncidentTimelineEntry {
  const row = object(value, 'incident timeline');
  return {
    timeline_id: integer(row.timeline_id, 'timeline_id', 1),
    action: text(row.action, 'action'),
    actor_user_id: nullableInteger(row.actor_user_id, 'actor_user_id'),
    actor_username: text(row.actor_username, 'actor_username'),
    actor_role: text(row.actor_role, 'actor_role'),
    occurred_at: timestamp(row.occurred_at, 'occurred_at') as string,
    request_id: text(row.request_id, 'request_id'),
    public_progress: nullableText(row.public_progress, 'public_progress'),
    admin_details: nullableText(row.admin_details, 'admin_details'),
    resulting_status: oneOf(row.resulting_status, STATUSES, 'resulting_status'),
    incident_version: integer(row.incident_version, 'incident_version', 1),
  };
}

export function parseIncidentDetail(value: unknown): IncidentDetail {
  const row = object(value, 'incident detail');
  const preview = object(row.user_preview, 'user_preview');
  return {
    ...parseIncidentListItem({
      ...row,
      affected_device_count: array(row.devices, 'devices').filter((item) =>
        object(item, 'device').incident_role === 'affected').length,
      latest_public_progress: preview.public_progress,
    }),
    admin_summary: text(row.admin_summary, 'admin_summary'),
    user_summary: text(row.user_summary, 'user_summary'),
    created_at: timestamp(row.created_at, 'created_at') as string,
    created_by: integer(row.created_by, 'created_by'),
    resolution_summary: nullableText(row.resolution_summary, 'resolution_summary'),
    false_positive_reason: nullableText(row.false_positive_reason, 'false_positive_reason'),
    devices: array(row.devices, 'devices').map(parseIncidentDevice),
    timeline: array(row.timeline, 'timeline').map(parseIncidentTimeline),
    user_preview: {
      user_title: text(preview.user_title, 'user_preview.user_title'),
      user_summary: text(preview.user_summary, 'user_preview.user_summary'),
      public_progress: nullableText(preview.public_progress, 'user_preview.public_progress'),
    },
  };
}

function parseHelpItem(value: unknown): HelpRequestItem {
  const row = object(value, 'help request');
  return {
    help_request_id: text(row.help_request_id, 'help_request_id'),
    user_id: integer(row.user_id, 'user_id', 1),
    incident_id: nullableText(row.incident_id, 'incident_id'),
    device_id: nullableText(row.device_id, 'device_id'),
    category: oneOf(row.category, HELP_CATEGORIES, 'category'),
    user_message: text(row.user_message, 'user_message'),
    status: oneOf(row.status, HELP_STATUSES, 'status'),
    public_response: nullableText(row.public_response, 'public_response'),
    internal_note: nullableText(row.internal_note, 'internal_note'),
    assigned_to: nullableInteger(row.assigned_to, 'assigned_to'),
    created_at: timestamp(row.created_at, 'created_at') as string,
    updated_at: timestamp(row.updated_at, 'updated_at') as string,
    closed_at: timestamp(row.closed_at, 'closed_at', true),
    request_version: integer(row.request_version, 'request_version', 1),
  };
}

function parseHelpTimeline(value: unknown): HelpTimelineEntry {
  const row = object(value, 'help timeline');
  return {
    timeline_id: integer(row.timeline_id, 'timeline_id', 1),
    help_request_id: text(row.help_request_id, 'help_request_id'),
    action: text(row.action, 'action'),
    actor_user_id: nullableInteger(row.actor_user_id, 'actor_user_id'),
    actor_username: text(row.actor_username, 'actor_username'),
    actor_role: text(row.actor_role, 'actor_role'),
    occurred_at: timestamp(row.occurred_at, 'occurred_at') as string,
    request_id: text(row.request_id, 'request_id'),
    public_response: nullableText(row.public_response, 'public_response'),
    internal_note: nullableText(row.internal_note, 'internal_note'),
    resulting_status: oneOf(row.resulting_status, HELP_STATUSES, 'resulting_status'),
    request_version: integer(row.request_version, 'request_version', 1),
  };
}

export function parseHelpList(value: unknown): HelpRequestListResponse {
  const row = object(value, 'help request list');
  return {
    items: array(row.items, 'items').map(parseHelpItem),
    total: integer(row.total, 'total'),
    limit: integer(row.limit, 'limit', 1),
    offset: integer(row.offset, 'offset'),
  };
}

export function parseHelpDetail(value: unknown): HelpRequestDetail {
  const row = object(value, 'help request detail');
  return { ...parseHelpItem(row), timeline: array(row.timeline, 'timeline').map(parseHelpTimeline) };
}

export function parseSupportContact(value: unknown): SupportContact {
  const row = object(value, 'support contact');
  const available = bool(row.available, 'available');
  if (!available) return { available: false, reason: text(row.reason, 'reason') };
  return {
    available: true,
    display_name: text(row.display_name, 'display_name'),
    phone: nullableText(row.phone, 'phone'),
    email: nullableText(row.email, 'email'),
    working_hours: nullableText(row.working_hours, 'working_hours'),
    public_note: nullableText(row.public_note, 'public_note'),
    enabled: bool(row.enabled, 'enabled'),
    config_version: integer(row.config_version, 'config_version', 1),
    updated_by: integer(row.updated_by, 'updated_by', 1),
    updated_at: timestamp(row.updated_at, 'updated_at') as string,
  };
}

function parseError(value: unknown): { code: string; message: string; requestId: string } | null {
  try {
    const error = object(object(value, 'response').error, 'error');
    return {
      code: text(error.code, 'error.code'),
      message: text(error.message, 'error.message'),
      requestId: text(error.request_id, 'error.request_id'),
    };
  } catch {
    return null;
  }
}

function errorKind(status: number): IncidentApiErrorKind {
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

function isAbort(error: unknown): boolean {
  return error instanceof Error && error.name === 'AbortError';
}

function queryString<T extends object>(values: T): string {
  const params = new URLSearchParams();
  Object.entries(values as Record<string, unknown>).forEach(([key, value]) => {
    if ((typeof value === 'string' || typeof value === 'number') && value !== '') {
      params.set(key, String(value));
    }
  });
  return params.size ? `?${params.toString()}` : '';
}

export interface IncidentsApi {
  listIncidents(query?: IncidentQuery, signal?: AbortSignal): Promise<IncidentListResponse>;
  getIncident(id: string, signal?: AbortSignal): Promise<IncidentDetail>;
  createIncident(input: CreateIncidentInput, signal?: AbortSignal): Promise<IncidentDetail>;
  transitionIncident(id: string, action: 'ack' | 'recovering' | 'resolve' | 'false-positive', input: TransitionIncidentInput, signal?: AbortSignal): Promise<IncidentDetail>;
  getSupportContact(signal?: AbortSignal): Promise<SupportContact>;
  updateSupportContact(input: UpdateSupportContactInput, signal?: AbortSignal): Promise<SupportContact>;
  listHelpRequests(query?: HelpRequestQuery, signal?: AbortSignal): Promise<HelpRequestListResponse>;
  getHelpRequest(id: string, signal?: AbortSignal): Promise<HelpRequestDetail>;
  updateHelpRequest(id: string, input: UpdateHelpRequestInput, signal?: AbortSignal): Promise<HelpRequestDetail>;
}

export class V3IncidentsClient implements IncidentsApi {
  private csrfToken: string | null = null;

  private async response(url: string, init: RequestInit): Promise<Response> {
    try {
      return await fetch(url, { credentials: 'include', cache: 'no-store', ...init });
    } catch (error) {
      if (isAbort(error)) throw new IncidentApiError('aborted', '请求已取消', { cause: error });
      throw new IncidentApiError('network', '无法连接事件管理服务', { cause: error });
    }
  }

  private async json(response: Response): Promise<unknown> {
    try {
      return await response.json();
    } catch (error) {
      throw new IncidentApiError('invalid_response', '服务返回了无法解析的 JSON', {
        status: response.status, cause: error,
      });
    }
  }

  private fail(response: Response, body: unknown): never {
    const envelope = parseError(body);
    throw new IncidentApiError(
      errorKind(response.status),
      envelope?.message ?? `事件管理请求失败（HTTP ${response.status}）`,
      { status: response.status, code: envelope?.code, requestId: envelope?.requestId },
    );
  }

  private captureCsrf(response: Response): void {
    const token = response.headers.get(INCIDENT_CSRF_HEADER);
    if (token?.trim()) this.csrfToken = token;
  }

  private async read<T>(url: string, parser: (value: unknown) => T, signal?: AbortSignal): Promise<T> {
    const response = await this.response(url, { method: 'GET', headers: { Accept: 'application/json' }, signal });
    const body = await this.json(response);
    if (!response.ok) this.fail(response, body);
    const parsed = parser(body);
    this.captureCsrf(response);
    return parsed;
  }

  private async csrf(signal?: AbortSignal): Promise<string> {
    if (!this.csrfToken) await this.listIncidents({ limit: 1, offset: 0 }, signal);
    if (!this.csrfToken) throw new IncidentApiError('invalid_response', 'GET 响应缺少 CSRF token，写操作未发送');
    return this.csrfToken;
  }

  private async write<T>(url: string, method: 'POST' | 'PATCH' | 'PUT', payload: unknown, parser: (value: unknown) => T, signal?: AbortSignal): Promise<T> {
    const token = await this.csrf(signal);
    const response = await this.response(url, {
      method,
      headers: { Accept: 'application/json', 'Content-Type': 'application/json', [INCIDENT_CSRF_HEADER]: token },
      body: JSON.stringify(payload), signal,
    });
    const body = await this.json(response);
    if (!response.ok) this.fail(response, body);
    return parser(body);
  }

  listIncidents(query: IncidentQuery = {}, signal?: AbortSignal): Promise<IncidentListResponse> {
    return this.read(`${INCIDENTS_ENDPOINT}${queryString(query)}`, parseIncidentList, signal);
  }

  getIncident(id: string, signal?: AbortSignal): Promise<IncidentDetail> {
    return this.read(`${INCIDENTS_ENDPOINT}/${encodeURIComponent(id)}`, parseIncidentDetail, signal);
  }

  createIncident(input: CreateIncidentInput, signal?: AbortSignal): Promise<IncidentDetail> {
    return this.write(INCIDENTS_ENDPOINT, 'POST', input, parseIncidentDetail, signal);
  }

  transitionIncident(id: string, action: 'ack' | 'recovering' | 'resolve' | 'false-positive', input: TransitionIncidentInput, signal?: AbortSignal): Promise<IncidentDetail> {
    return this.write(`${INCIDENTS_ENDPOINT}/${encodeURIComponent(id)}/${action}`, 'POST', input, parseIncidentDetail, signal);
  }

  getSupportContact(signal?: AbortSignal): Promise<SupportContact> {
    return this.read(SUPPORT_CONTACT_ENDPOINT, parseSupportContact, signal);
  }

  updateSupportContact(input: UpdateSupportContactInput, signal?: AbortSignal): Promise<SupportContact> {
    return this.write(SUPPORT_CONTACT_ENDPOINT, 'PUT', input, parseSupportContact, signal);
  }

  listHelpRequests(query: HelpRequestQuery = {}, signal?: AbortSignal): Promise<HelpRequestListResponse> {
    return this.read(`${HELP_REQUESTS_ENDPOINT}${queryString(query)}`, parseHelpList, signal);
  }

  getHelpRequest(id: string, signal?: AbortSignal): Promise<HelpRequestDetail> {
    return this.read(`${HELP_REQUESTS_ENDPOINT}/${encodeURIComponent(id)}`, parseHelpDetail, signal);
  }

  updateHelpRequest(id: string, input: UpdateHelpRequestInput, signal?: AbortSignal): Promise<HelpRequestDetail> {
    return this.write(`${HELP_REQUESTS_ENDPOINT}/${encodeURIComponent(id)}`, 'PATCH', input, parseHelpDetail, signal);
  }
}

export const v3IncidentsApi = new V3IncidentsClient();
