import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react-native';
import HomeScreen from './HomeScreen';
import DevicesScreen from './DevicesScreen';
import { useMobile } from './MobileContext';

jest.mock('./MobileContext', () => ({ useMobile: jest.fn() }));
const useMobileMock = useMobile as jest.Mock;
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
  it('filters authorized devices and only opens simplified details', async () => {
    useMobileMock.mockReturnValue({ ...base, overview });
    await render(<DevicesScreen />);
    await fireEvent.press(screen.getByText('门口设备'));
    expect(screen.getByText('详细信息将在后续版本提供')).toBeTruthy();
    expect(screen.queryByText(/IP|MAC|端口|GNN/)).toBeNull();
  });
});
