import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react-native';
import HomeScreen from './HomeScreen';
import DevicesScreen from './DevicesScreen';
import NoticeListScreen, { NoticeDetailScreen } from './NoticeScreens';
import MobileDeviceDetailScreen from './MobileDeviceDetailScreen';
import { SubmitHelpScreen } from './HelpScreens';
import { mobileApi, MobileApiError, type MobileNotice } from './api';
import { useMobile } from './MobileContext';

const mockNavigate = jest.fn();
const mockGoBack = jest.fn();
const mockNavigation = { navigate: mockNavigate, replace: jest.fn(), goBack: mockGoBack };

jest.mock('./MobileContext', () => ({ useMobile: jest.fn(), messageFor: (error: { kind?: string }) => error?.kind === 'network' ? '无法连接服务器，请检查网络后重试' : '操作未完成，请重试' }));
jest.mock('@react-navigation/native', () => ({
  useNavigation: () => mockNavigation,
  useIsFocused: () => true,
  useRoute: () => ({ params: { incidentId: 'inc-1', deviceId: 'door-1' } }),
}));
const useMobileMock = useMobile as jest.Mock;
beforeEach(() => { mockNavigate.mockClear(); mockGoBack.mockClear(); });
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

describe('authorized user screens', () => {
  it('shows only scoped data, unknown and retired, without safety conclusions or secrets', async () => {
    useMobileMock.mockReturnValue({ ...base, overview });
    await render(<HomeScreen />);
    expect(screen.getByText('门口设备')).toBeTruthy();
    expect(screen.getByText('连接：未知')).toBeTruthy();
    expect(screen.getByText('运行模式：disabled · 已退役')).toBeTruthy();
    expect(screen.getByText('安全事件功能尚未接入')).toBeTruthy();
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
    expect(screen.getByText(/状态：等待管理员处理/)).toBeTruthy();
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

  it('keeps a neutral empty reminder state and does not produce examples', async () => {
    useMobileMock.mockReturnValue({ ...base, overview: { ...overview, security_capability: { available: true, reason: 'available' } },
      notices: [], noticesStale: false, noticesBusy: false, supportContact: { available: false, reason: 'support_contact_not_configured' },
      syncNotices: jest.fn(), syncSupportContact: jest.fn() });
    await render(<NoticeListScreen />);
    expect(screen.getByText('当前没有已记录提醒。')).toBeTruthy();
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
    await fireEvent.press(screen.getByLabelText('查看受影响设备：门口设备'));
    expect(mockNavigate).toHaveBeenCalledWith('设备详情', { deviceId: 'door-1' });
    noticeSpy.mockRestore(); readSpy.mockRestore(); ackSpy.mockRestore();
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
    const requestAuthorized = async (call: (server: { baseUrl: string; insecureLan: boolean }, token: string) => Promise<unknown>) =>
      call({ baseUrl: 'https://example.test', insecureLan: false }, 'memory-only-token');
    useMobileMock.mockReturnValue({ ...base, requestAuthorized, sync: jest.fn() });
    await render(<MobileDeviceDetailScreen />);
    expect(await screen.findByText('门口设备')).toBeTruthy();
    expect(screen.getByText('连接状态：尚无可用的连接记录')).toBeTruthy();
    expect(screen.getByText('运行模式：维护模式')).toBeTruthy();
    expect(screen.getByText('此时间范围尚未收到流量样本。')).toBeTruthy();
    expect(screen.queryByText(/0 B\/秒|当前安全|遭到攻击|192\.168\.|MAC|端口|peer|GNN|模型/)).toBeNull();
    expect(detailSpy).toHaveBeenCalledTimes(1);
    expect(trafficSpy).toHaveBeenCalledTimes(1);
    detailSpy.mockRestore(); trafficSpy.mockRestore();
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
    const requestAuthorized = async (call: (server: { baseUrl: string; insecureLan: boolean }, token: string) => Promise<unknown>) =>
      call({ baseUrl: 'https://example.test', insecureLan: false }, 'memory-only-token');
    useMobileMock.mockReturnValue({ ...base, requestAuthorized, sync: jest.fn() });
    await render(<MobileDeviceDetailScreen />);
    expect(await screen.findByText(/上传 0 B\/秒 · 下载 0 B\/秒/)).toBeTruthy();
    expect(screen.getByLabelText('此处存在数据缺口')).toBeTruthy();
    expect(screen.getByText(/空白间隔表示没有对应数据/)).toBeTruthy();
    expect(screen.queryByText('此时间范围尚未收到流量样本。')).toBeNull();
    detailSpy.mockRestore(); trafficSpy.mockRestore();
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
