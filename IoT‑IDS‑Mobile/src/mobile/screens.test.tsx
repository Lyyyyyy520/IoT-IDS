import React from 'react';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react-native';
import HomeScreen from './HomeScreen';
import DevicesScreen from './DevicesScreen';
import NoticeListScreen, { NoticeDetailScreen } from './NoticeScreens';
import MobileDeviceDetailScreen from './MobileDeviceDetailScreen';
import { SubmitHelpScreen } from './HelpScreens';
import { mobileApi, MobileApiError, type MobileDeviceDetail, type MobileDeviceHistory, type MobileDeviceTraffic,
  type MobileNotice, type MobileNoticeReplay, type MobileTopology } from './api';
import { useMobile } from './MobileContext';
import type { AppLifecycleAdapter } from './appLifecycle';

const mockNavigate = jest.fn();
const mockGoBack = jest.fn();
const mockSetOptions = jest.fn();
const mockNavigation = { navigate: mockNavigate, replace: jest.fn(), goBack: mockGoBack, setOptions: mockSetOptions };
let mockRouteParams: { incidentId: string; deviceId: string } = { incidentId: 'inc-1', deviceId: 'door-1' };

jest.mock('./MobileContext', () => ({ useMobile: jest.fn(), messageFor: (error: { kind?: string }) => error?.kind === 'network' ? '无法连接服务器，请检查网络后重试' : '操作未完成，请重试' }));
jest.mock('@react-navigation/native', () => ({
  useNavigation: () => mockNavigation,
  useIsFocused: () => true,
  useRoute: () => ({ params: mockRouteParams }),
}));
const useMobileMock = useMobile as jest.Mock;
beforeEach(() => { mockNavigate.mockClear(); mockGoBack.mockClear(); mockSetOptions.mockClear(); mockRouteParams = { incidentId: 'inc-1', deviceId: 'door-1' }; });
const base = {
  phase: 'authenticated', config: { baseUrl: 'https://example.test', insecureLan: false },
  session: null, lastSynced: '2026-09-21T04:00:00Z', stale: false,
  busy: false, error: null, requestId: null, sync: jest.fn(),
  pair: jest.fn(), logout: jest.fn(), resetClient: jest.fn(),
};
const overview = {
  generated_at: '2026-09-21T04:00:00Z', user: { user_id: 2, username: 'resident' },
  security_capability: { available: false, reason: 'incident_pipeline_not_ready' },
  devices: [{
    device_id: 'door-1', display_name: '门口设备', device_type: 'sensor', area_id: 'home',
    connection_status: 'unknown', operation_mode: 'disabled', retired: true,
    retired_at: '2026-09-21T03:00:00Z', last_updated_at: '2026-09-21T03:00:00Z',
    availability_status: 'retired',
  }],
};
const notice = (patch: Partial<MobileNotice> = {}): MobileNotice => ({
  incident_id: 'inc-1', user_title: '门口设备提醒', user_summary: '请留意门口设备的服务状态。', severity: 'medium',
  affected_devices: [{ device_id: 'door-1', display_name: '门口设备', device_type: 'sensor', area_id: 'home' }],
  first_seen_at: '2026-09-21T03:00:00Z', updated_at: '2026-09-21T04:00:00Z', status: 'open',
  public_progress: '管理员正在核查。', read: false, first_read_at: null, acknowledged: false,
  acknowledged_at: null, resolved_at: null, ...patch,
});

function fakeLifecycle(initial: 'active' | 'inactive' | 'background' = 'active') {
  let state = initial;
  const listeners = new Set<(next: 'active' | 'inactive' | 'background') => void>();
  const adapter: AppLifecycleAdapter = {
    currentState: () => state,
    subscribe: listener => { listeners.add(listener); return () => listeners.delete(listener); },
  };
  return {
    adapter,
    emit(next: 'active' | 'inactive' | 'background') { state = next; listeners.forEach(listener => listener(next)); },
    listenerCount: () => listeners.size,
  };
}

const detailFor = (deviceId: string, displayName: string): MobileDeviceDetail => ({
  device_id: deviceId, display_name: displayName, device_type: 'sensor', area_id: 'home',
  connection_status: 'unknown', operation_mode: 'active', retired: false, retired_at: null,
  last_updated_at: '2026-09-24T01:00:00Z', last_seen_at: null, availability_status: 'unknown',
  status_text: { connection: '尚无可用的连接记录', operation: '正常运行模式' }, availability_text: '尚无可用的连接记录',
  security_capability: { available: false, reason: 'incident_pipeline_not_ready', active_notice_count: null, recent_notices: [],
    gnn: { available: false, reason: 'gnn_capability_unavailable' } },
  traffic_capability: { available: true, reason: null },
});

const trafficFor = (deviceId: string): MobileDeviceTraffic => ({
  device_id: deviceId, window: '15m', query_window: { from: '2026-09-24T00:45:00Z', to: '2026-09-24T01:00:00Z' },
  generated_at: '2026-09-24T01:00:00Z', is_historical: false,
  availability: { status: 'no_samples', available: false, reason: 'no_samples' },
  freshness: { status: 'unavailable', latest_sample_at: null },
  current_rate: { status: 'warming_up', label: '正在积累数据', window_seconds: 120, as_of: '2026-09-24T01:00:00Z',
    uploaded_bytes_per_second: null, downloaded_bytes_per_second: null, uploaded_packets_per_second: null, downloaded_packets_per_second: null },
  summary: null, trend_resolution_seconds: 60, trend: [], protocols: [], data_quality: { complete: null, message: '统计可能不完整。' },
});

const historyFor = (deviceId: string, window: MobileDeviceHistory['window'] = '24h'): MobileDeviceHistory => ({
  device_id: deviceId, window, query_window: { from: '2026-09-23T00:00:00Z', to: '2026-09-24T00:00:00Z' },
  generated_at: '2026-09-24T00:00:00Z', availability: { available: false, reason: 'no_state_observations' },
  data_source: 'v3_device_state_observations', latest: null, series: [], truncated: false, bucket_seconds: 173,
  connection_history_available: true, connection_status_changes: [], connection_history_truncated: false,
  connection_history_note: '状态变化只在服务端刷新连接状态时记录。',
});

const replayFor = (incidentId = 'inc-1'): MobileNoticeReplay => ({
  incident_id: incidentId, generated_at: '2026-09-21T04:00:00Z',
  window: { from: '2026-09-20T16:00:00Z', to: '2026-09-21T04:00:00Z' },
  notice: { user_title: '门口设备提醒', user_summary: '管理员正在核查。', severity: 'medium', status: 'open',
    first_seen_at: '2026-09-21T03:00:00Z', public_progress: '正在核查。' },
  incident_timeline: [{ occurred_at: '2026-09-21T03:01:00Z', resulting_status: 'open', public_progress: '正在核查。' }],
  devices: [{ device_id: 'door-1', display_name: '门口设备', device_type: 'sensor', area_id: 'home',
    current_connection_status: 'online', current_last_received_at: '2026-09-21T03:59:00Z', observations: [] }],
  relationships: [], connection_status_changes: [], gaps: [], truncated: false, traffic_available: true,
  capabilities: { connection_status_history: true, camera_media: false, reason: '摄像头画面未留存。' },
});

function setupDetailRequests() {
  const detailSpy = jest.spyOn(mobileApi, 'deviceDetail').mockImplementation(async (_server, _token, deviceId) => detailFor(deviceId, `名称 ${deviceId}`));
  const trafficSpy = jest.spyOn(mobileApi, 'deviceTraffic').mockImplementation(async (_server, _token, deviceId) => trafficFor(deviceId));
  const historySpy = jest.spyOn(mobileApi, 'deviceHistory').mockImplementation(async (_server, _token, deviceId, window = '24h') => historyFor(deviceId, window));
  const requestAuthorized = async (call: (server: { baseUrl: string; insecureLan: boolean }, token: string) => Promise<unknown>) =>
    call({ baseUrl: 'https://example.test', insecureLan: false }, 'memory-only-token');
  useMobileMock.mockReturnValue({ ...base, requestAuthorized, sync: jest.fn() });
  return { detailSpy, trafficSpy, historySpy };
}

describe('authorized user screens', () => {
  it('shows only scoped data, unknown and retired, without safety conclusions or secrets', async () => {
    useMobileMock.mockReturnValue({ ...base, overview });
    await render(<HomeScreen />);
    expect(screen.getByText('门口设备')).toBeTruthy();
    expect(screen.getAllByText('未知').length).toBeGreaterThan(0);
    expect(screen.getByText('家人平安，就是最好的安心')).toBeTruthy();
    expect(screen.queryByText(/当前安全|无攻击|192\.168\.|MAC|GNN/)).toBeNull();
  });
  it('shows empty scope without example devices or global data', async () => {
    useMobileMock.mockReturnValue({ ...base, overview: { ...overview, devices: [] } });
    await render(<HomeScreen />);
    expect(screen.getByText('管理员尚未授权设备')).toBeTruthy();
    expect(screen.queryByText('门口设备')).toBeNull();
  });
  it('retains only genuine data with an obvious stale warning', async () => {
    useMobileMock.mockReturnValue({ ...base, overview, stale: true, error: '无法连接服务器' });
    await render(<HomeScreen />);
    expect(screen.getByText(/数据可能已过期/)).toBeTruthy();
    expect(screen.getByText('门口设备')).toBeTruthy();
  });
  it('opens a restricted detail directly from the authorized device list', async () => {
    useMobileMock.mockReturnValue({ ...base, overview });
    await render(<DevicesScreen />);
    await fireEvent.press(screen.getByLabelText('查看设备：门口设备'));
    expect(mockNavigate).toHaveBeenCalledWith('设备详情', { deviceId: 'door-1' });
  });

  it('searches device identifiers and applies type, connection and active-alert filters', async () => {
    const second = { ...overview.devices[0], device_id: 'camera-2', display_name: '走廊摄像头', device_type: 'camera',
      connection_status: 'offline' as const, area_id: 'west' };
    const active = notice({ affected_devices: [notice().affected_devices[0]], status: 'open' });
    useMobileMock.mockReturnValue({ ...base, overview: { ...overview, devices: [...overview.devices, second],
      security_capability: { available: true, reason: 'available' } }, notices: [active] });
    await render(<DevicesScreen />);
    expect(screen.getByText('门口设备')).toBeTruthy();
    expect(screen.getByText('走廊摄像头')).toBeTruthy();
    await fireEvent.changeText(screen.getByLabelText('搜索设备'), 'camera-2');
    expect(screen.getByText('走廊摄像头')).toBeTruthy();
    expect(screen.queryByText('门口设备')).toBeNull();
    await fireEvent.changeText(screen.getByLabelText('搜索设备'), '');
    await fireEvent.press(screen.getByText('camera'));
    expect(screen.getByText('走廊摄像头')).toBeTruthy();
    expect(screen.queryByText('门口设备')).toBeNull();
    await fireEvent.press(screen.getByText('有活动提醒'));
    expect(screen.getByText('没有符合条件的设备')).toBeTruthy();
  });

  it('shows only server returned scoped topology nodes and navigates from a tapped node', async () => {
    const second = { ...overview.devices[0], device_id: 'camera-2', display_name: '走廊摄像头', device_type: 'camera', area_id: 'west' };
    const topology: MobileTopology = {
      generated_at: '2026-09-24T01:00:00Z', window: { from: '2026-09-23T01:00:00Z', to: '2026-09-24T01:00:00Z', range: '24h' },
      nodes: [
        { device_id: 'door-1', display_name: '门口设备', device_type: 'sensor', area_id: 'home', connection_status: 'online',
          last_received_at: '2026-09-24T00:59:00Z', retired: false, active_alert_count: 1, highest_alert_severity: 'high' },
        { device_id: 'camera-2', display_name: '走廊摄像头', device_type: 'camera', area_id: 'west', connection_status: 'offline',
          last_received_at: null, retired: false, active_alert_count: 0, highest_alert_severity: null },
      ],
      edges: [{ source_device_id: 'door-1', target_device_id: 'camera-2', bytes: 12, packets: 2, flows: 1,
        protocols: ['TCP'], last_seen: '2026-09-24T00:58:00Z' }],
      availability: { available: true, reason: null, source: 'v3_device_traffic_peer_minutes' },
      alert_capability: { available: true, reason: null },
    };
    const topologySpy = jest.spyOn(mobileApi, 'topology').mockResolvedValue(topology);
    const requestAuthorized = async (call: (server: { baseUrl: string; insecureLan: boolean }, token: string) => Promise<unknown>) =>
      call({ baseUrl: 'https://example.test', insecureLan: false }, 'memory-only-token');
    useMobileMock.mockReturnValue({ ...base, overview: { ...overview, devices: [...overview.devices, second] },
      notices: [notice()], requestAuthorized });
    await render(<DevicesScreen />);
    await fireEvent.press(screen.getByText('关系视图'));
    expect(await screen.findByText('设备关系')).toBeTruthy();
    await fireEvent.press(screen.getByLabelText('查看关系节点：门口设备，door-1，在线，有提醒'));
    expect(mockNavigate).toHaveBeenCalledWith('设备详情', { deviceId: 'door-1' });
    expect(topologySpy).toHaveBeenCalledWith(expect.anything(), expect.anything(), '24h', expect.anything());
    topologySpy.mockRestore();
  });

  it('opens a restricted device detail from the home device card', async () => {
    useMobileMock.mockReturnValue({ ...base, overview });
    await render(<HomeScreen />);
    await fireEvent.press(screen.getByLabelText('查看设备：门口设备'));
    expect(mockNavigate).toHaveBeenCalledWith('设备详情', { deviceId: 'door-1' });
  });

  it('shows actual reminder copy, device, status and per-user acknowledgement state', async () => {
    const actual = notice();
    useMobileMock.mockReturnValue({ ...base, overview: { ...overview, security_capability: { available: true, reason: 'available' } },
      notices: [actual], noticesStale: false, noticesBusy: false, lastNoticesSynced: actual.updated_at,
      supportContact: { available: false, reason: 'support_contact_not_configured' }, syncNotices: jest.fn(), syncSupportContact: jest.fn() });
    await render(<NoticeListScreen />);
    expect(screen.getByText('门口设备提醒')).toBeTruthy();
    expect(screen.getByText(/等待管理员处理/)).toBeTruthy();
    expect(screen.getByText(/管理员正在核查/)).toBeTruthy();
    expect(screen.queryByText(/攻击源|192\.168\.|MAC|GNN|模型/)).toBeNull();
  });

  it('filters to unread reminders without altering the underlying list', async () => {
    const readNotice = notice({ incident_id: 'inc-read', user_title: '已读提醒', read: true, first_read_at: '2026-09-21T04:01:00Z' });
    const unreadNotice = notice({ incident_id: 'inc-unread', user_title: '未读提醒' });
    useMobileMock.mockReturnValue({ ...base, overview: { ...overview, security_capability: { available: true, reason: 'available' } },
      notices: [readNotice, unreadNotice], noticesStale: false, noticesBusy: false,
      supportContact: { available: false, reason: 'support_contact_not_configured' }, syncNotices: jest.fn(), syncSupportContact: jest.fn() });
    await render(<NoticeListScreen />);
    expect(screen.getByText('已读提醒')).toBeTruthy();
    await fireEvent.press(screen.getByText('未读'));
    expect(screen.getByText('未读提醒')).toBeTruthy();
    expect(screen.queryByText('已读提醒')).toBeNull();
  });

  it('filters reminders by device ID, severity and occurrence range', async () => {
    const urgent = notice({ incident_id: 'inc-urgent', user_title: '摄像头提醒', severity: 'critical',
      affected_devices: [{ device_id: 'cam-9', display_name: '门厅摄像头', device_type: 'camera', area_id: 'home' }],
      first_seen_at: new Date().toISOString() });
    const older = notice({ incident_id: 'inc-old', user_title: '旧门锁提醒', severity: 'low',
      affected_devices: [{ device_id: 'lock-3', display_name: '后门门锁', device_type: 'lock', area_id: 'home' }],
      first_seen_at: '2020-01-01T00:00:00Z' });
    useMobileMock.mockReturnValue({ ...base, overview: { ...overview, security_capability: { available: true, reason: 'available' } },
      notices: [urgent, older], noticesStale: false, noticesBusy: false,
      supportContact: { available: false, reason: 'support_contact_not_configured' }, syncNotices: jest.fn(), syncSupportContact: jest.fn() });
    await render(<NoticeListScreen />);
    await fireEvent.changeText(screen.getByLabelText('搜索提醒设备或内容'), 'cam-9');
    expect(screen.getByText('摄像头提醒')).toBeTruthy();
    expect(screen.queryByText('旧门锁提醒')).toBeNull();
    await fireEvent.changeText(screen.getByLabelText('搜索提醒设备或内容'), '');
    await fireEvent.press(screen.getByText('紧急'));
    expect(screen.getByText('摄像头提醒')).toBeTruthy();
    expect(screen.queryByText('旧门锁提醒')).toBeNull();
    await fireEvent.press(screen.getByText('24 小时'));
    expect(screen.getByText('摄像头提醒')).toBeTruthy();
  });

  it('keeps a neutral empty reminder state and does not produce examples', async () => {
    useMobileMock.mockReturnValue({ ...base, overview: { ...overview, security_capability: { available: true, reason: 'available' } },
      notices: [], noticesStale: false, noticesBusy: false, supportContact: { available: false, reason: 'support_contact_not_configured' },
      syncNotices: jest.fn(), syncSupportContact: jest.fn() });
    await render(<NoticeListScreen />);
    expect(screen.getByText('当前没有已记录提醒')).toBeTruthy();
    expect(screen.queryByText(/当前安全|无攻击/)).toBeNull();
  });

  it('shows feature unavailability without interpreting it as a safety result', async () => {
    useMobileMock.mockReturnValue({ ...base, overview, notices: null, noticesBusy: false,
      supportContact: { available: false, reason: 'support_contact_not_configured' }, syncNotices: jest.fn(), syncSupportContact: jest.fn() });
    await render(<NoticeListScreen />);
    expect(screen.getByText('提醒功能暂不可用')).toBeTruthy();
    expect(screen.queryByText(/当前安全|无攻击/)).toBeNull();
  });

  it('loads detail, marks it read, and keeps acknowledgement distinct from resolution', async () => {
    const readNotice = notice({ read: true, first_read_at: '2026-09-21T04:01:00Z' });
    const ackNotice = { ...readNotice, acknowledged: true, acknowledged_at: '2026-09-21T04:02:00Z' };
    const noticeSpy = jest.spyOn(mobileApi, 'notice').mockResolvedValueOnce(notice());
    const readSpy = jest.spyOn(mobileApi, 'markNoticeRead').mockResolvedValueOnce(readNotice);
    const ackSpy = jest.spyOn(mobileApi, 'acknowledgeNotice').mockResolvedValueOnce(ackNotice);
    const replaySpy = jest.spyOn(mobileApi, 'noticeReplay').mockResolvedValue(replayFor());
    const requestAuthorized = async (call: (server: { baseUrl: string; insecureLan: boolean }, token: string) => Promise<unknown>) =>
      call({ baseUrl: 'https://example.test', insecureLan: false }, 'memory-only-token');
    useMobileMock.mockReturnValue({ ...base, requestAuthorized, applyNotice: jest.fn(), syncNotices: jest.fn(),
      supportContact: { available: false, reason: 'support_contact_not_configured' }, syncSupportContact: jest.fn() });
    await render(<NoticeDetailScreen />);
    expect(await screen.findByText('门口设备提醒')).toBeTruthy();
    expect(readSpy).toHaveBeenCalledTimes(1);
    await fireEvent.press(screen.getByText('我已知晓'));
    expect(ackSpy).toHaveBeenCalledTimes(1);
    expect(screen.getByText(/仅表示你已阅读，不代表事件已经解决/)).toBeTruthy();
    expect(screen.queryByText('事件已解决')).toBeNull();
    expect(await screen.findByText('事件时间轴')).toBeTruthy();
    expect(screen.getByText('提醒进度')).toBeTruthy();
    expect(screen.getByText('摄像头画面未留存，时间轴不包含影像。')).toBeTruthy();
    await fireEvent.press(screen.getByLabelText('查看受影响设备：门口设备'));
    expect(mockNavigate).toHaveBeenCalledWith('设备详情', { deviceId: 'door-1' });
    noticeSpy.mockRestore(); readSpy.mockRestore(); ackSpy.mockRestore(); replaySpy.mockRestore();
  });

  it('shows mobile-only device and traffic states without privileged fields or safety conclusions', async () => {
    const detail = {
      device_id: 'door-1', display_name: '门口设备', device_type: 'sensor', area_id: 'home',
      connection_status: 'unknown' as const, operation_mode: 'maintenance' as const, retired: false,
      retired_at: null, last_updated_at: '2026-09-24T01:00:00Z', last_seen_at: null,
      availability_status: 'maintenance' as const,
      status_text: { connection: '尚无可用的连接记录', operation: '维护模式' }, availability_text: '设备处于维护模式',
      security_capability: { available: true, reason: null, active_notice_count: 0, recent_notices: [],
        gnn: { available: false as const, reason: 'gnn_capability_unavailable' } },
      traffic_capability: { available: true, reason: null },
    };
    const traffic = {
      device_id: 'door-1', window: '15m' as const,
      query_window: { from: '2026-09-24T00:45:00Z', to: '2026-09-24T01:00:00Z' }, generated_at: '2026-09-24T01:00:00Z', is_historical: false,
      availability: { status: 'no_samples' as const, available: false, reason: 'no_samples' },
      freshness: { status: 'unavailable' as const, latest_sample_at: null },
      current_rate: { status: 'warming_up' as const, label: '正在积累数据', window_seconds: 120, as_of: '2026-09-24T01:00:00Z',
        uploaded_bytes_per_second: null, downloaded_bytes_per_second: null, uploaded_packets_per_second: null, downloaded_packets_per_second: null },
      summary: null, trend_resolution_seconds: 60, trend: [], protocols: [],
      data_quality: { complete: null, message: '无法归属的样本不会计入统计。' },
    };
    const detailSpy = jest.spyOn(mobileApi, 'deviceDetail').mockResolvedValue(detail);
    const trafficSpy = jest.spyOn(mobileApi, 'deviceTraffic').mockResolvedValue(traffic);
    const historySpy = jest.spyOn(mobileApi, 'deviceHistory').mockImplementation(async (_server, _token, deviceId, window = '24h') => historyFor(deviceId, window));
    const requestAuthorized = async (call: (server: { baseUrl: string; insecureLan: boolean }, token: string) => Promise<unknown>) =>
      call({ baseUrl: 'https://example.test', insecureLan: false }, 'memory-only-token');
    useMobileMock.mockReturnValue({ ...base, requestAuthorized, sync: jest.fn() });
    await render(<MobileDeviceDetailScreen />);
    expect(await screen.findByText('门口设备')).toBeTruthy();
    expect(screen.getByText('未知')).toBeTruthy();
    expect(screen.getByText(/维护模式/)).toBeTruthy();
    expect(screen.getByText('此时间范围尚未收到流量样本。')).toBeTruthy();
    expect(screen.queryByText(/0 B\/秒|当前安全|遭到攻击|192\.168\.|MAC|peer|GNN|管理端检测明细/)).toBeNull();
    expect(detailSpy).toHaveBeenCalledTimes(1);
    expect(trafficSpy).toHaveBeenCalledTimes(1);
    expect(historySpy).toHaveBeenCalledTimes(1);
    detailSpy.mockRestore(); trafficSpy.mockRestore(); historySpy.mockRestore();
  });

  it('shows the sensor latest values with units and requests a new range for real history plots', async () => {
    const device = detailFor('door-1', '温湿度传感器');
    const point = (id: number, at: string, temp: number, humidity: number) => ({ observation_id: id,
      observed_at: at, received_at: at, telemetry: { temp, humidity } });
    const initial = historyFor('door-1');
    const history: MobileDeviceHistory = { ...initial,
      availability: { available: true, reason: null },
      latest: point(2, '2026-09-23T23:59:00Z', 23.6, 48.4),
      series: [point(1, '2026-09-23T23:30:00Z', 22.8, 47.1), point(2, '2026-09-23T23:59:00Z', 23.6, 48.4)],
    };
    const detailSpy = jest.spyOn(mobileApi, 'deviceDetail').mockResolvedValue(device);
    const trafficSpy = jest.spyOn(mobileApi, 'deviceTraffic').mockResolvedValue(trafficFor('door-1'));
    const historySpy = jest.spyOn(mobileApi, 'deviceHistory').mockImplementation(async (_server, _token, _id, window = '24h') => ({ ...history, window }));
    const requestAuthorized = async (call: (server: { baseUrl: string; insecureLan: boolean }, token: string) => Promise<unknown>) =>
      call({ baseUrl: 'https://example.test', insecureLan: false }, 'memory-only-token');
    useMobileMock.mockReturnValue({ ...base, requestAuthorized, sync: jest.fn() });
    await render(<MobileDeviceDetailScreen />);
    expect((await screen.findAllByText('23.6')).length).toBeGreaterThan(0);
    expect(screen.getAllByText('48.4').length).toBeGreaterThan(0);
    expect(screen.getByText('温度趋势（°C）')).toBeTruthy();
    expect(screen.getByText('湿度趋势（%RH）')).toBeTruthy();
    expect(screen.getByLabelText(/温度 23.6 °C，观测时间/)).toBeTruthy();
    await fireEvent.press(screen.getByText('7 天'));
    await waitFor(() => expect(historySpy).toHaveBeenLastCalledWith(expect.anything(), expect.anything(), 'door-1', '7d', expect.anything()));
    detailSpy.mockRestore(); trafficSpy.mockRestore(); historySpy.mockRestore();
  });

  it('renders measured zero traffic and marks real trend gaps', async () => {
    const detail = {
      device_id: 'door-1', display_name: '门口设备', device_type: 'sensor', area_id: 'home',
      connection_status: 'unknown' as const, operation_mode: 'active' as const, retired: false,
      retired_at: null, last_updated_at: '2026-09-24T01:00:00Z', last_seen_at: null,
      availability_status: 'unknown' as const,
      status_text: { connection: '尚无可用的连接记录', operation: '正常运行模式' }, availability_text: '尚无可用的连接记录',
      security_capability: { available: true, reason: null, active_notice_count: 0, recent_notices: [],
        gnn: { available: false as const, reason: 'gnn_capability_unavailable' } },
      traffic_capability: { available: true, reason: null },
    };
    const traffic = {
      device_id: 'door-1', window: '15m' as const,
      query_window: { from: '2026-09-24T00:45:00Z', to: '2026-09-24T01:00:00Z' },
      generated_at: '2026-09-24T01:00:00Z', is_historical: false,
      availability: { status: 'available' as const, available: true, reason: null },
      freshness: { status: 'fresh' as const, latest_sample_at: '2026-09-24T00:59:00Z' },
      current_rate: { status: 'available' as const, label: '实时数据可用', window_seconds: 60,
        as_of: '2026-09-24T01:00:00Z', uploaded_bytes_per_second: 0, downloaded_bytes_per_second: 0,
        uploaded_packets_per_second: 0, downloaded_packets_per_second: 0 },
      summary: { uploaded_bytes: 0, downloaded_bytes: 0, uploaded_packets: 0, downloaded_packets: 0 },
      trend_resolution_seconds: 300,
      trend: [
        { bucket_start: '2026-09-24T00:45:00Z', uploaded_bytes: 0, downloaded_bytes: 0, uploaded_packets: 0, downloaded_packets: 0 },
        { bucket_start: '2026-09-24T00:55:00Z', uploaded_bytes: 0, downloaded_bytes: 0, uploaded_packets: 0, downloaded_packets: 0 },
      ],
      protocols: [], data_quality: { complete: null, message: '统计可能不完整。' },
    };
    const detailSpy = jest.spyOn(mobileApi, 'deviceDetail').mockResolvedValue(detail);
    const trafficSpy = jest.spyOn(mobileApi, 'deviceTraffic').mockResolvedValue(traffic);
    const historySpy = jest.spyOn(mobileApi, 'deviceHistory').mockImplementation(async (_server, _token, deviceId, window = '24h') => historyFor(deviceId, window));
    const requestAuthorized = async (call: (server: { baseUrl: string; insecureLan: boolean }, token: string) => Promise<unknown>) =>
      call({ baseUrl: 'https://example.test', insecureLan: false }, 'memory-only-token');
    useMobileMock.mockReturnValue({ ...base, requestAuthorized, sync: jest.fn() });
    await render(<MobileDeviceDetailScreen />);
    expect(await screen.findByText(/上传 0 B\/秒 · 下载 0 B\/秒/)).toBeTruthy();
    expect(screen.getByLabelText('此处存在数据缺口')).toBeTruthy();
    expect(screen.getByText(/空白间隔表示没有对应数据/)).toBeTruthy();
    expect(screen.queryByText('此时间范围尚未收到流量样本。')).toBeNull();
    detailSpy.mockRestore(); trafficSpy.mockRestore(); historySpy.mockRestore();
  });

  it('keeps help text in-memory and reuses the same idempotency key for a retry', async () => {
    const help = { help_request_id: 'help-1', incident_id: null, device_id: null, category: 'other' as const,
      user_message: '请协助处理。', status: 'open' as const, public_response: null,
      created_at: '2026-09-21T04:00:00Z', updated_at: '2026-09-21T04:00:00Z', closed_at: null,
      request_version: 1, idempotent_replay: false };
    const createSpy = jest.spyOn(mobileApi, 'createHelpRequest')
      .mockRejectedValueOnce(new MobileApiError('network', 0, 'network_unavailable'))
      .mockResolvedValueOnce(help);
    const requestAuthorized = async (call: (server: { baseUrl: string; insecureLan: boolean }, token: string) => Promise<unknown>) =>
      call({ baseUrl: 'https://example.test', insecureLan: false }, 'memory-only-token');
    useMobileMock.mockReturnValue({ ...base, requestAuthorized, overview: { ...overview, devices: [] }, notices: [] });
    await render(<SubmitHelpScreen />);
    const input = screen.getByLabelText('求助内容');
    await fireEvent.changeText(input, '请协助处理。');
    await fireEvent.press(screen.getByText('提交求助'));
    expect(await screen.findByText(/无法连接服务器/)).toBeTruthy();
    const firstKey = createSpy.mock.calls[0][3];
    expect(typeof firstKey).toBe('string');
    await fireEvent.press(screen.getByText('提交求助'));
    expect(createSpy.mock.calls[1][3]).toBe(firstKey);
    expect(screen.getByLabelText('求助内容').props.value).toBe('');
    createSpy.mockResolvedValueOnce(help);
    await fireEvent.changeText(screen.getByLabelText('求助内容'), '新的求助内容。');
    await fireEvent.press(screen.getByText('提交求助'));
    expect(createSpy.mock.calls[2][3]).not.toBe(firstKey);
    createSpy.mockRestore();
  });
});

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>(done => { resolve = done; });
  return { promise, resolve };
}

describe('mobile device detail lifecycle', () => {
  afterEach(() => { jest.useRealTimers(); });

  it('starts detail and traffic refresh while visible and active', async () => {
    const lifecycle = fakeLifecycle();
    const { detailSpy, trafficSpy, historySpy } = setupDetailRequests();
    await render(<MobileDeviceDetailScreen lifecycle={lifecycle.adapter} />);
    await waitFor(() => {
      expect(detailSpy).toHaveBeenCalledTimes(1);
      expect(trafficSpy).toHaveBeenCalledTimes(1);
      expect(historySpy).toHaveBeenCalledTimes(1);
    });
    expect(lifecycle.listenerCount()).toBe(1);
    detailSpy.mockRestore(); trafficSpy.mockRestore(); historySpy.mockRestore();
  });

  it('pauses timers and aborts outstanding requests in background/inactive states', async () => {
    jest.useFakeTimers();
    const lifecycle = fakeLifecycle();
    const pendingDetail = deferred<MobileDeviceDetail>();
    const pendingTraffic = deferred<MobileDeviceTraffic>();
    const pendingHistory = deferred<MobileDeviceHistory>();
    const detailSpy = jest.spyOn(mobileApi, 'deviceDetail').mockReturnValue(pendingDetail.promise);
    const trafficSpy = jest.spyOn(mobileApi, 'deviceTraffic').mockReturnValue(pendingTraffic.promise);
    const historySpy = jest.spyOn(mobileApi, 'deviceHistory').mockReturnValue(pendingHistory.promise);
    const requestAuthorized = async (call: (server: { baseUrl: string; insecureLan: boolean }, token: string) => Promise<unknown>) =>
      call({ baseUrl: 'https://example.test', insecureLan: false }, 'memory-only-token');
    useMobileMock.mockReturnValue({ ...base, requestAuthorized, sync: jest.fn() });
    const view = await render(<MobileDeviceDetailScreen lifecycle={lifecycle.adapter} />);
    const initialDetailCalls = detailSpy.mock.calls.length;
    const initialTrafficCalls = trafficSpy.mock.calls.length;
    const initialHistoryCalls = historySpy.mock.calls.length;
    expect(initialDetailCalls).toBeGreaterThan(0);
    expect(initialTrafficCalls).toBeGreaterThan(0);

    await act(async () => { lifecycle.emit('inactive'); });
    expect(detailSpy.mock.calls.every(call => call[3]?.aborted === true)).toBe(true);
    expect(trafficSpy.mock.calls.every(call => call[4]?.aborted === true)).toBe(true);
    expect(historySpy.mock.calls.every(call => call[4]?.aborted === true)).toBe(true);
    await act(async () => { jest.advanceTimersByTime(60_000); });
    expect(detailSpy).toHaveBeenCalledTimes(initialDetailCalls);
    expect(trafficSpy).toHaveBeenCalledTimes(initialTrafficCalls);
    expect(historySpy).toHaveBeenCalledTimes(initialHistoryCalls);

    await act(async () => { lifecycle.emit('background'); });
    pendingDetail.resolve(detailFor('door-1', '迟到的设备详情'));
    pendingTraffic.resolve(trafficFor('door-1'));
    pendingHistory.resolve(historyFor('door-1'));
    await act(async () => { await Promise.resolve(); });
    await act(async () => { view.unmount(); });
    jest.clearAllTimers();
    expect(jest.getTimerCount()).toBe(0);
    detailSpy.mockRestore(); trafficSpy.mockRestore(); historySpy.mockRestore();
  });

  it('refreshes immediately on foreground, ignores duplicate active events, and leaves no interval after unmount', async () => {
    jest.useFakeTimers();
    const lifecycle = fakeLifecycle();
    const { detailSpy, trafficSpy, historySpy } = setupDetailRequests();
    const view = await render(<MobileDeviceDetailScreen lifecycle={lifecycle.adapter} />);
    const initialDetailCalls = detailSpy.mock.calls.length;
    const initialTrafficCalls = trafficSpy.mock.calls.length;
    const initialHistoryCalls = historySpy.mock.calls.length;
    expect(initialDetailCalls).toBeGreaterThan(0);
    expect(initialTrafficCalls).toBeGreaterThan(0);

    await act(async () => { lifecycle.emit('background'); });
    await act(async () => { jest.advanceTimersByTime(60_000); await Promise.resolve(); });
    expect(detailSpy).toHaveBeenCalledTimes(initialDetailCalls);
    expect(trafficSpy).toHaveBeenCalledTimes(initialTrafficCalls);
    expect(historySpy).toHaveBeenCalledTimes(initialHistoryCalls);

    await act(async () => { lifecycle.emit('active'); });
    const activeDetailCalls = detailSpy.mock.calls.length;
    const activeTrafficCalls = trafficSpy.mock.calls.length;
    const activeHistoryCalls = historySpy.mock.calls.length;
    expect(activeDetailCalls).toBeGreaterThan(initialDetailCalls);
    expect(activeTrafficCalls).toBeGreaterThan(initialTrafficCalls);
    await act(async () => { lifecycle.emit('active'); });
    expect(detailSpy).toHaveBeenCalledTimes(activeDetailCalls);
    expect(trafficSpy).toHaveBeenCalledTimes(activeTrafficCalls);
    expect(historySpy).toHaveBeenCalledTimes(activeHistoryCalls);

    await act(async () => { jest.advanceTimersByTime(8_000); await Promise.resolve(); });
    expect(trafficSpy).toHaveBeenCalledTimes(activeTrafficCalls + 1);
    expect(historySpy).toHaveBeenCalledTimes(activeHistoryCalls);
    expect(detailSpy).toHaveBeenCalledTimes(activeDetailCalls);
    await act(async () => { view.unmount(); });
    await act(async () => { jest.advanceTimersByTime(60_000); });
    expect(detailSpy).toHaveBeenCalledTimes(activeDetailCalls);
    expect(trafficSpy).toHaveBeenCalledTimes(activeTrafficCalls + 1);
    expect(historySpy).toHaveBeenCalledTimes(activeHistoryCalls);
    jest.clearAllTimers();
    expect(jest.getTimerCount()).toBe(0);
    detailSpy.mockRestore(); trafficSpy.mockRestore(); historySpy.mockRestore();
  });

  it('aborts old-device requests and prevents their late results replacing the new device', async () => {
    const lifecycle = fakeLifecycle();
    const oldDetail = deferred<MobileDeviceDetail>();
    const newDetail = deferred<MobileDeviceDetail>();
    const oldTraffic = deferred<MobileDeviceTraffic>();
    const newTraffic = deferred<MobileDeviceTraffic>();
    const oldHistory = deferred<MobileDeviceHistory>();
    const newHistory = deferred<MobileDeviceHistory>();
    const details = new Map([['door-1', oldDetail.promise], ['door-2', newDetail.promise]]);
    const traffic = new Map([['door-1', oldTraffic.promise], ['door-2', newTraffic.promise]]);
    const histories = new Map([['door-1', oldHistory.promise], ['door-2', newHistory.promise]]);
    const detailSpy = jest.spyOn(mobileApi, 'deviceDetail').mockImplementation((_server, _token, deviceId) => details.get(deviceId)!);
    const trafficSpy = jest.spyOn(mobileApi, 'deviceTraffic').mockImplementation((_server, _token, deviceId) => traffic.get(deviceId)!);
    const historySpy = jest.spyOn(mobileApi, 'deviceHistory').mockImplementation((_server, _token, deviceId) => histories.get(deviceId)!);
    const requestAuthorized = async (call: (server: { baseUrl: string; insecureLan: boolean }, token: string) => Promise<unknown>) =>
      call({ baseUrl: 'https://example.test', insecureLan: false }, 'memory-only-token');
    useMobileMock.mockReturnValue({ ...base, requestAuthorized, sync: jest.fn() });
    const view = await render(<MobileDeviceDetailScreen lifecycle={lifecycle.adapter} />);
    const oldDetailCalls = detailSpy.mock.calls.length;
    const oldTrafficCalls = trafficSpy.mock.calls.length;
    const oldHistoryCalls = historySpy.mock.calls.length;

    mockRouteParams = { ...mockRouteParams, deviceId: 'door-2' };
    await view.rerender(<MobileDeviceDetailScreen lifecycle={lifecycle.adapter} />);
    expect(detailSpy.mock.calls.slice(0, oldDetailCalls).every(call => call[3]?.aborted === true)).toBe(true);
    expect(trafficSpy.mock.calls.slice(0, oldTrafficCalls).every(call => call[4]?.aborted === true)).toBe(true);
    expect(historySpy.mock.calls.slice(0, oldHistoryCalls).every(call => call[4]?.aborted === true)).toBe(true);
    expect(detailSpy.mock.calls.slice(oldDetailCalls).some(call => call[2] === 'door-2')).toBe(true);
    expect(trafficSpy.mock.calls.slice(oldTrafficCalls).some(call => call[2] === 'door-2')).toBe(true);
    expect(historySpy.mock.calls.slice(oldHistoryCalls).some(call => call[2] === 'door-2')).toBe(true);

    await act(async () => {
      oldDetail.resolve(detailFor('door-1', '旧设备迟到响应'));
      oldTraffic.resolve(trafficFor('door-1'));
      oldHistory.resolve(historyFor('door-1'));
      await Promise.resolve();
    });
    await act(async () => {
      newDetail.resolve(detailFor('door-2', '当前设备'));
      newTraffic.resolve(trafficFor('door-2'));
      newHistory.resolve(historyFor('door-2'));
    });
    expect(await screen.findByText('当前设备')).toBeTruthy();
    expect(screen.queryByText('旧设备迟到响应')).toBeNull();
    await act(async () => { view.unmount(); });
    detailSpy.mockRestore(); trafficSpy.mockRestore(); historySpy.mockRestore();
  });
});
