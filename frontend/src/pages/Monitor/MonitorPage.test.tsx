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
  it('shows graph and incident unavailable reasons without a fake topology or safety claim', () => {
    renderPage();
    expect(screen.queryByRole('button', { name: '旧版功能' })).not.toBeInTheDocument();
    expect(screen.getByTestId('graph-capability')).toHaveTextContent('后端尚未提供图快照能力');
    expect(screen.getByTestId('incident-capability')).toHaveTextContent('后端尚未提供安全事件存储能力');
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
    expect(screen.getByRole('status')).toHaveTextContent('数据可能过期');
    expect(screen.getAllByText('东门摄像头').length).toBeGreaterThan(0);
  });

  it('deep-links a real monitor device into the device management workspace', () => {
    renderPage();
    fireEvent.click(screen.getByRole('button', { name: '查看或管理 东门摄像头' }));
    expect(screen.getByTestId('device-destination')).toHaveTextContent(
      '/devices?device_id=camera-01',
    );
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
    fireEvent.click(screen.getByRole('button', { name: /门厅设备需要复核/ }));
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
