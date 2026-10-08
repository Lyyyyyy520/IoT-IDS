import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { validSnapshot } from '../../test/monitorFixtures';
import MonitorPage from './index';

let monitorState: any;

vi.mock('../../contexts/AuthContext', () => ({
  useAuth: () => ({
    user: { username: 'operator-a', role: 'operator' },
    logout: vi.fn(),
  }),
}));

vi.mock('../../features/monitor/monitorStore', () => ({
  useMonitorStore: () => monitorState,
}));

function DeviceDestination() {
  const location = useLocation();
  return <div data-testid="device-destination">{location.pathname}{location.search}</div>;
}

function renderPage() {
  return render(
    <MemoryRouter
      initialEntries={['/monitor']}
      future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
    >
      <Routes>
        <Route path="/monitor" element={<MonitorPage />} />
        <Route path="/devices" element={<DeviceDestination />} />
        <Route path="/incidents" element={<DeviceDestination />} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  monitorState = {
    snapshot: validSnapshot,
    phase: 'ready',
    realtime: 'connected',
    stale: false,
    lastSyncedAt: '2026-09-20T02:00:02Z',
    lastEventId: 5,
    recentEvents: [],
    error: null,
    resyncReason: null,
    resync: vi.fn(),
  };
});

describe('MonitorPage real states', () => {
  it('renders the real graph capability and the current incident unavailable reason without a fake topology', () => {
    renderPage();
    expect(screen.queryByRole('button', { name: '旧版功能' })).not.toBeInTheDocument();
    // 图能力已可用：渲染真实图（canvas），不显示“暂不可用”文案或伪造拓扑
    expect(screen.getByTestId('graph-capability')).not.toHaveTextContent('GNN 图暂不可用');
    // 事件能力未迁移时给出明确原因
    expect(screen.getByTestId('incident-capability')).toHaveTextContent('事件存储尚未完成迁移');
    expect(screen.getByText(/不能代表“没有攻击”/)).toBeInTheDocument();
  });

  it('renders a legal empty snapshot without demonstration devices or healthy components', () => {
    monitorState.snapshot = { ...validSnapshot, event_cursor: 0, devices: [], system_components: [] };
    renderPage();
    expect(screen.getByText('没有匹配的真实设备')).toBeInTheDocument();
    expect(screen.getByText('尚未收到组件状态')).toBeInTheDocument();
    expect(screen.queryByText('东门摄像头')).not.toBeInTheDocument();
  });

  it('keeps visible real data and warns when SSE is disconnected', () => {
    monitorState.realtime = 'disconnected';
    monitorState.stale = true;
    renderPage();
    expect(screen.getByText(/数据可能过期/)).toBeInTheDocument();
    expect(screen.getAllByText('东门摄像头').length).toBeGreaterThan(0);
  });

  it('deep-links a real monitor device into the device management workspace', () => {
    renderPage();
    fireEvent.click(screen.getByRole('button', { name: '查看或管理 东门摄像头' }));
    expect(screen.getByTestId('device-destination')).toHaveTextContent(
      '/devices?device_id=camera-01',
    );
  });

  it('builds the monitor report URL from the active device filters and selected range', () => {
    renderPage();
    fireEvent.change(screen.getByPlaceholderText('搜索名称、类型、区域或 IP'), {
      target: { value: '门厅' },
    });
    fireEvent.change(screen.getByLabelText('按连接状态筛选'), { target: { value: 'online' } });
    fireEvent.change(screen.getByLabelText('按类型筛选'), { target: { value: 'camera' } });
    fireEvent.change(screen.getByLabelText('按告警状态筛选'), { target: { value: 'alerted' } });
    fireEvent.change(screen.getByLabelText('趋势范围'), { target: { value: '6h' } });

    const link = screen.getByRole('link', { name: '导出报表 CSV' });
    const url = new URL(link.getAttribute('href')!, 'http://localhost');
    expect(url.pathname).toBe('/api/v3/monitor/export');
    expect(url.searchParams.get('search')).toBe('门厅');
    expect(url.searchParams.get('connection_status')).toBe('online');
    expect(url.searchParams.get('device_type')).toBe('camera');
    expect(url.searchParams.get('risk_filter')).toBe('alerted');
    expect(url.searchParams.get('range')).toBe('6h');
  });

  it('shows real incident summaries and deep-links into incident management', () => {
    const summary = {
      incident_id: 'incident-42',
      incident_type: 'device_anomaly',
      severity: 'high' as const,
      status: 'open' as const,
      source: 'rule' as const,
      admin_title: '门厅设备需要复核',
      first_seen_at: '2026-09-20T01:59:00Z',
      updated_at: '2026-09-20T02:00:00Z',
      resolved_at: null,
      incident_version: 1,
      affected_device_ids: ['camera-01'],
    };
    monitorState.snapshot = {
      ...validSnapshot,
      capabilities: {
        ...validSnapshot.capabilities,
        incident: { available: true, reason: null },
      },
      incidents: {
        active: [summary],
        recent: [],
        empty_meaning: 'no_recorded_incidents_not_proven_safe',
      },
    };
    renderPage();
    expect(screen.getByText('门厅设备需要复核')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '在拓扑中定位事件 门厅设备需要复核' }));
    expect(screen.getAllByText(/事件定位：门厅设备需要复核/).length).toBeGreaterThan(0);
    expect(screen.getByRole('button', { name: '清除定位' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '查看事件 门厅设备需要复核' }));
    expect(screen.getByTestId('device-destination')).toHaveTextContent(
      '/incidents?incident_id=incident-42',
    );
  });

  it.each([
    ['unauthorized', '登录状态已失效', '401'],
    ['forbidden', '当前账号权限不足', '403'],
    ['unavailable', '监视数据库或服务尚未准备', '503'],
    ['network', '无法连接后端服务', 'ERR'],
    ['invalid_response', '后端响应格式不符合契约', 'ERR'],
  ])('renders the %s initial error explicitly', (kind, title, code) => {
    monitorState = {
      ...monitorState,
      snapshot: null,
      phase: 'error',
      realtime: 'disconnected',
      error: { kind, message: `failure-${kind}`, requestId: 'request-7' },
    };
    renderPage();
    expect(screen.getByRole('alert')).toHaveTextContent(title);
    expect(screen.getByRole('alert')).toHaveTextContent(code);
    expect(screen.getByRole('alert')).toHaveTextContent('request-7');
  });
});
