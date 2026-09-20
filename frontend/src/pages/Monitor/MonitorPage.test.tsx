import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
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

function renderPage() {
  return render(
    <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
      <MonitorPage />
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
