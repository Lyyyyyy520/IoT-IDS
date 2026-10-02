import { mobileApi, MobileApiError, parseHelpRequest, parseNotice, parseNoticeCollection, parseSupportContact,
  parseMobileDeviceDetail, parseMobileDeviceTraffic, parseMobileDeviceHistory, parseMobileTopology,
  parseMobileNoticeReplay } from './api';
import { safeEmailUrl, safePhoneUrl } from './supportContactUtils';

const config = { baseUrl: 'https://mobile.example.test', insecureLan: false };
const sampleNotice = {
  incident_id: 'inc-1', user_title: '设备服务提醒', user_summary: '请留意设备服务状态。', severity: 'high',
  affected_devices: [{ device_id: 'living-room', display_name: '客厅设备', device_type: 'sensor', area_id: 'home' }],
  first_seen_at: '2026-09-24T01:00:00Z', updated_at: '2026-09-24T01:05:00Z', status: 'recovering',
  public_progress: '管理员正在协助恢复设备服务。', read: true, first_read_at: '2026-09-24T01:02:00Z',
  acknowledged: false, acknowledged_at: null, resolved_at: null,
};
const help = { help_request_id: 'help_1', incident_id: 'inc-1', device_id: 'living-room', category: 'device_issue',
  user_message: '请协助看看设备。', status: 'waiting_for_user', public_response: '请补充设备使用时间。',
  created_at: '2026-09-24T01:00:00Z', updated_at: '2026-09-24T01:05:00Z', closed_at: null, request_version: 2 };

it('strictly parses only the public notice contract and rejects admin/internal fields', () => {
  expect(parseNotice(sampleNotice).affected_devices[0].display_name).toBe('客厅设备');
  expect(() => parseNotice({ ...sampleNotice, admin_summary: 'private' })).toThrow();
  expect(() => parseNotice({ ...sampleNotice, suspected_source: 'secret' })).toThrow();
});

it('parses cursor snapshots, tombstones, public contact and mobile help fields', () => {
  const snapshot = parseNoticeCollection({ mode: 'snapshot', notices: [sampleNotice], tombstones: [], next_cursor: '12:3', snapshot_required: false });
  expect(snapshot.next_cursor).toBe('12:3');
  expect(parseNoticeCollection({ mode: 'delta', notices: [], tombstones: [{ incident_id: 'inc-1', change_id: 13, reason: 'resolved' }], next_cursor: '13:3', snapshot_required: false }).tombstones).toHaveLength(1);
  expect(() => parseNoticeCollection({ mode: 'delta', notices: [], tombstones: [], next_cursor: '12', snapshot_required: false })).toThrow();
  expect(parseSupportContact({ available: false, reason: 'support_contact_not_configured' })).toEqual({ available: false, reason: 'support_contact_not_configured' });
  expect(parseSupportContact({ available: true, display_name: '物业服务台', phone: null, email: 'help@example.test', working_hours: null, public_note: null, config_version: 1, updated_at: '2026-09-24T01:00:00Z' }).available).toBe(true);
  expect(parseHelpRequest(help).status).toBe('waiting_for_user');
  expect(() => parseHelpRequest({ ...help, internal_note: 'private' })).toThrow();
});

it('uses bearer-only requests, encodes identifiers/cursors, forwards abort, and preserves idempotency key', async () => {
  const fetchMock = jest.fn().mockResolvedValue({ ok: true, status: 200, json: async () => ({ mode: 'delta', notices: [], tombstones: [], next_cursor: '13:3', snapshot_required: false }) });
  global.fetch = fetchMock as typeof fetch;
  const abort = new AbortController();
  await mobileApi.notices(config, 'access-only-in-memory', { after: '12:3', view: 'all', limit: 50, signal: abort.signal });
  const [url, options] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
  expect(url).toBe('https://mobile.example.test/api/v3/mobile/notices?after=12%3A3&view=all&limit=50');
  expect(options.credentials).toBe('omit');
  expect(options.headers).toEqual({ Authorization: 'Bearer access-only-in-memory' });
  expect(options.signal).toBe(abort.signal);

  fetchMock.mockResolvedValueOnce({ ok: true, status: 201, json: async () => ({ ...help, idempotent_replay: false }) });
  await mobileApi.createHelpRequest(config, 'access', { category: 'device_issue', user_message: '请协助看看设备。' }, 'request-key-0001');
  expect(fetchMock.mock.calls[1][1].headers).toEqual({ 'Content-Type': 'application/json', Authorization: 'Bearer access', 'Idempotency-Key': 'request-key-0001' });
});

it('keeps HTTP errors structured without exposing server messages or request bodies', async () => {
  global.fetch = jest.fn().mockResolvedValue({ ok: false, status: 409, json: async () => ({ error: { code: 'idempotency_conflict', message: 'user body secret', request_id: 'req-42' } }) }) as typeof fetch;
  await expect(mobileApi.helpRequests(config, 'access')).rejects.toMatchObject({ status: 409, code: 'idempotency_conflict', requestId: 'req-42' });
  try { await mobileApi.helpRequests(config, 'access'); } catch (error) {
    expect((error as Error).message).not.toContain('user body secret');
    expect((error as Error).message).not.toContain('access');
  }
  expect(MobileApiError).toBeDefined();
});

it('allows only validated telephone and email URLs', () => {
  expect(safePhoneUrl('+86 (010) 1234-5678')).toBe('tel:+8601012345678');
  expect(safePhoneUrl('javascript:alert(1)')).toBeNull();
  expect(safeEmailUrl('help@example.test')).toBe('mailto:help@example.test');
  expect(safeEmailUrl('javascript:alert@example.test\r\n')).toBeNull();
});

const deviceDetail = {
  device_id: 'door-1', display_name: '门口设备', device_type: 'sensor', area_id: 'home',
  connection_status: 'unknown', operation_mode: 'maintenance', retired: false, retired_at: null,
  last_updated_at: '2026-09-24T01:00:00Z', last_seen_at: null, availability_status: 'maintenance',
  status_text: { connection: '尚无可用的连接记录', operation: '维护模式' }, availability_text: '设备处于维护模式',
  security_capability: { available: true, reason: null, active_notice_count: 0, recent_notices: [],
    gnn: { available: false, reason: 'gnn_capability_unavailable' } },
  traffic_capability: { available: true, reason: null },
};
const emptyTraffic = {
  device_id: 'door-1', window: '15m', query_window: { from: '2026-09-24T00:45:00Z', to: '2026-09-24T01:00:00Z' },
  generated_at: '2026-09-24T01:00:00Z', is_historical: false,
  availability: { status: 'no_samples', available: false, reason: 'no_samples' },
  freshness: { status: 'unavailable', latest_sample_at: null },
  current_rate: { status: 'warming_up', label: '正在积累数据', window_seconds: 120, as_of: '2026-09-24T01:00:00Z',
    uploaded_bytes_per_second: null, downloaded_bytes_per_second: null, uploaded_packets_per_second: null, downloaded_packets_per_second: null },
  summary: null, trend_resolution_seconds: 60, trend: [], protocols: [],
  data_quality: { complete: null, message: '统计可能不完整。' },
};

it('accepts only the scoped mobile device allowlist and keeps connection/mode separate', () => {
  expect(parseMobileDeviceDetail(deviceDetail)).toMatchObject({
    connection_status: 'unknown', operation_mode: 'maintenance', retired: false,
  });
  expect(() => parseMobileDeviceDetail({ ...deviceDetail, mac: 'AA:BB:CC:DD:EE:FF' })).toThrow();
  expect(() => parseMobileDeviceDetail({ ...deviceDetail, security_capability: { ...deviceDetail.security_capability, score: 0.8 } })).toThrow();
});

it('distinguishes no samples from a warming rate and rejects admin traffic fields', () => {
  expect(parseMobileDeviceTraffic(emptyTraffic)).toMatchObject({
    availability: { status: 'no_samples', available: false }, summary: null,
    current_rate: { status: 'warming_up', uploaded_bytes_per_second: null },
  });
  expect(() => parseMobileDeviceTraffic({ ...emptyTraffic, peers: [] })).toThrow();
});

it('accepts a real zero counter but rejects contradictory no-sample and warming payloads', () => {
  const observedZero = {
    ...emptyTraffic,
    availability: { status: 'available', available: true, reason: null },
    freshness: { status: 'fresh', latest_sample_at: '2026-09-24T00:59:00Z' },
    current_rate: { status: 'available', label: '实时数据可用', window_seconds: 60,
      as_of: '2026-09-24T01:00:00Z', uploaded_bytes_per_second: 0,
      downloaded_bytes_per_second: 0, uploaded_packets_per_second: 0,
      downloaded_packets_per_second: 0 },
    summary: { uploaded_bytes: 0, downloaded_bytes: 0, uploaded_packets: 0, downloaded_packets: 0 },
    trend: [{ bucket_start: '2026-09-24T00:59:00Z', uploaded_bytes: 0, downloaded_bytes: 0,
      uploaded_packets: 0, downloaded_packets: 0 }],
  };
  expect(parseMobileDeviceTraffic(observedZero).summary?.uploaded_bytes).toBe(0);
  expect(() => parseMobileDeviceTraffic({ ...observedZero,
    availability: { status: 'no_samples', available: false, reason: 'no_samples' } })).toThrow();
  expect(() => parseMobileDeviceTraffic({ ...emptyTraffic,
    current_rate: { ...emptyTraffic.current_rate, uploaded_bytes_per_second: 1 } })).toThrow();
});

it('parses real mobile sensor history and rejects unsafe or unbounded fields', () => {
  const history = {
    device_id: 'sensor-01', window: '24h', query_window: { from: '2026-09-23T00:00:00Z', to: '2026-09-24T00:00:00Z' },
    generated_at: '2026-09-24T00:00:00Z', availability: { available: true, reason: null },
    data_source: 'v3_device_state_observations',
    latest: { observation_id: 2, observed_at: '2026-09-23T23:59:58Z', received_at: '2026-09-24T00:00:00Z',
      telemetry: { temp: -2.5, humidity: 42.1, state: 'open' } },
    series: [{ observation_id: 2, observed_at: '2026-09-23T23:59:58Z', received_at: '2026-09-24T00:00:00Z',
      telemetry: { temp: -2.5, humidity: 42.1, state: 'open' } }],
    truncated: false, bucket_seconds: 173, connection_history_available: true,
    connection_status_changes: [{ event_id: 3, occurred_at: '2026-09-23T23:59:00Z', from_status: 'stale', to_status: 'online', source: 'report' }],
    connection_history_truncated: false, connection_history_note: '状态变化只在刷新连接状态时记录。',
  };
  expect(parseMobileDeviceHistory(history).latest?.telemetry.temp).toBe(-2.5);
  expect(() => parseMobileDeviceHistory({ ...history, series: [{ ...history.series[0], telemetry: { ip: '192.0.2.2' } }] })).toThrow();
  expect(() => parseMobileDeviceHistory({ ...history, risk_score: 0.9 })).toThrow();
});

it('accepts only topology edges within the returned node scope', () => {
  const topology = {
    generated_at: '2026-09-24T00:00:00Z',
    window: { from: '2026-09-23T00:00:00Z', to: '2026-09-24T00:00:00Z', range: '24h' },
    nodes: [{ device_id: 'sensor-01', display_name: '温湿度传感器', device_type: 'sensor', area_id: 'home',
      connection_status: 'online', last_received_at: '2026-09-23T23:59:00Z', retired: false,
      active_alert_count: 0, highest_alert_severity: null },
    { device_id: 'door-01', display_name: '门禁', device_type: 'door', area_id: 'home',
      connection_status: 'offline', last_received_at: null, retired: false, active_alert_count: 1, highest_alert_severity: 'high' }],
    edges: [{ source_device_id: 'sensor-01', target_device_id: 'door-01', bytes: 32, packets: 1, flows: 1,
      protocols: ['TCP'], last_seen: '2026-09-23T23:58:00Z' }],
    availability: { available: true, reason: null, source: 'v3_device_traffic_peer_minutes' },
    alert_capability: { available: true, reason: null },
  };
  expect(parseMobileTopology(topology).edges).toHaveLength(1);
  expect(() => parseMobileTopology({ ...topology, edges: [{ ...topology.edges[0], target_device_id: 'hidden-device' }] })).toThrow();
});

it('keeps mobile replay public and scoped, and rejects admin replay fields', () => {
  const replay = {
    incident_id: 'inc-1', generated_at: '2026-09-24T00:00:00Z',
    window: { from: '2026-09-23T00:00:00Z', to: '2026-09-24T00:00:00Z' },
    notice: { user_title: '提醒', user_summary: '公开摘要', severity: 'high', status: 'open',
      first_seen_at: '2026-09-23T18:00:00Z', public_progress: '正在核查。' },
    incident_timeline: [{ occurred_at: '2026-09-23T18:01:00Z', resulting_status: 'open', public_progress: '正在核查。' }],
    devices: [{ device_id: 'sensor-01', display_name: '传感器', device_type: 'sensor', area_id: 'home',
      current_connection_status: 'stale', current_last_received_at: '2026-09-23T17:59:00Z', observations: [] }],
    relationships: [], connection_status_changes: [], gaps: [{ device_id: 'sensor-01', from: '2026-09-23T00:00:00Z',
      to: '2026-09-23T01:00:00Z', reason: 'no_retained_observation' }], truncated: false, traffic_available: true,
    capabilities: { connection_status_history: true, camera_media: false, reason: '摄像头影像未留存。' },
  };
  expect(parseMobileNoticeReplay(replay).capabilities.camera_media).toBe(false);
  expect(() => parseMobileNoticeReplay({ ...replay, devices: [{ ...replay.devices[0], admin_details: 'private' }] })).toThrow();
});

it('encodes mobile device IDs and fixed traffic windows and forwards AbortSignal', async () => {
  const fetchMock = jest.fn().mockResolvedValue({ ok: true, status: 200, json: async () => deviceDetail });
  global.fetch = fetchMock as typeof fetch;
  const abort = new AbortController();
  await mobileApi.deviceDetail(config, 'memory-token', 'door/1', abort.signal);
  expect(fetchMock.mock.calls[0][0]).toContain('/api/v3/mobile/devices/door%2F1');
  expect((fetchMock.mock.calls[0][1] as RequestInit).signal).toBe(abort.signal);
  expect((fetchMock.mock.calls[0][1] as RequestInit).credentials).toBe('omit');

  fetchMock.mockResolvedValueOnce({ ok: true, status: 200, json: async () => emptyTraffic });
  await mobileApi.deviceTraffic(config, 'memory-token', 'door-1', '24h', abort.signal);
  expect(fetchMock.mock.calls[1][0]).toBe('https://mobile.example.test/api/v3/mobile/devices/door-1/traffic?window=24h');
});
