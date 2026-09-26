import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { TrafficApiError } from '../../api/v3Traffic';
import { deviceDetail } from '../../test/deviceFixtures';
import { emptyTrafficResponse, peersResponse, trafficResponse } from '../../test/trafficFixtures';
import DeviceTrafficPanel from './DeviceTrafficPanel';

const state = vi.hoisted(() => ({ workspace: null as any }));
vi.mock('./useDeviceTraffic', async (loadOriginal) => {
  const original = await loadOriginal<typeof import('./useDeviceTraffic')>();
  return { ...original, useDeviceTraffic: () => state.workspace };
});
vi.mock('./TrafficTrendChart', () => ({
  default: () => <div data-testid="mock-echarts">真实时间桶折线图</div>,
}));

function workspace(overrides: Record<string, unknown> = {}) {
  return {
    visible: true,
    rangeKey: '1h',
    range: { key: '1h', label: '1 小时', milliseconds: 3_600_000, resolution: 'minute' },
    setRange: vi.fn(),
    realtime: trafficResponse(),
    history: trafficResponse(),
    peers: peersResponse(),
    realtimeError: null,
    historyError: null,
    peersError: null,
    realtimeLoading: false,
    historyLoading: false,
    peersLoading: false,
    realtimeUpdatedAt: '2026-09-21T10:00:00Z',
    historyUpdatedAt: '2026-09-21T10:00:00Z',
    peersUpdatedAt: '2026-09-21T10:00:00Z',
    peerDirection: 'all', setPeerDirection: vi.fn(),
    peerProtocol: '', setPeerProtocol: vi.fn(),
    peerSort: 'bytes', setPeerSort: vi.fn(),
    peerOffset: 0, setPeerOffset: vi.fn(),
    refreshAll: vi.fn(),
    ...overrides,
  };
}

beforeEach(() => { state.workspace = workspace(); });

describe('DeviceTrafficPanel real-data states', () => {
  it('shows warming_up and no_samples without displaying fake zero rates or graph data', () => {
    const empty = emptyTrafficResponse();
    state.workspace = workspace({ realtime: empty, history: empty, peers: peersResponse({
      availability: { available: false, reason: 'no_samples' }, peers: [],
      pagination: { limit: 50, offset: 0, total: 0, has_more: false },
    }) });
    render(<DeviceTrafficPanel device={deviceDetail} isAdmin active onSelectPeer={vi.fn()} />);
    expect(screen.getByText('正在积累实时窗口')).toBeInTheDocument();
    expect(screen.getByText(/尚未收到流量样本/, { selector: 'strong' })).toBeInTheDocument();
    expect(screen.queryByText('0 B/s')).not.toBeInTheDocument();
    expect(screen.queryByTestId('traffic-chart-data')).not.toBeInTheDocument();
  });

  it('distinguishes an available true zero rate from an unknown window', async () => {
    state.workspace.realtime = trafficResponse({ realtime: {
      ...trafficResponse().realtime,
      available: true,
      readiness: 'ready',
      reason: null,
      tx_bytes_per_second: 0, rx_bytes_per_second: 0,
      tx_packets_per_second: 0, rx_packets_per_second: 0,
      tx_flows_per_second: 0, rx_flows_per_second: 0,
    } as ReturnType<typeof trafficResponse>['realtime'] });
    render(<DeviceTrafficPanel device={deviceDetail} isAdmin active onSelectPeer={vi.fn()} />);
    await screen.findByTestId('mock-echarts');
    expect(screen.getAllByText('0.00 B/s')).toHaveLength(2);
    expect(screen.getByText('0.00 包/秒')).toBeInTheDocument();
  });

  it('shows unknown and inferred protocols with percentages', async () => {
    state.workspace.history = trafficResponse({ protocols: [{
      ...trafficResponse().protocols[1], protocol: 'UNKNOWN',
    }, {
      ...trafficResponse().protocols[0], protocol: 'MQTT?',
      evidence: 'inferred_application_protocol',
    }] });
    render(<DeviceTrafficPanel device={deviceDetail} isAdmin active onSelectPeer={vi.fn()} />);
    await screen.findByTestId('mock-echarts');
    expect(screen.getByText('UNKNOWN', { selector: 'strong' })).toBeInTheDocument();
    expect(screen.getByText('MQTT?', { selector: 'strong' })).toBeInTheDocument();
    expect(screen.getByText('推断协议 · inferred')).toBeInTheDocument();
    expect(screen.getAllByText(/%$/).length).toBeGreaterThan(0);
  });

  it('renders admin peer IP, removes it for operator, and links only registered peers', async () => {
    const selectPeer = vi.fn();
    const rendered = render(
      <DeviceTrafficPanel device={deviceDetail} isAdmin active onSelectPeer={selectPeer} />,
    );
    await screen.findByTestId('mock-echarts');
    expect(screen.getByText('192.168.1.1')).toBeInTheDocument();
    expect(screen.getByText('203.0.113.10')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'gateway-01' }));
    expect(selectPeer).toHaveBeenCalledWith('gateway-01');
    expect(screen.getByText('外部通信对象').closest('button')).toBeNull();

    rendered.rerender(
      <DeviceTrafficPanel device={deviceDetail} isAdmin={false} active onSelectPeer={selectPeer} />,
    );
    expect(screen.queryByText('192.168.1.1')).not.toBeInTheDocument();
    expect(screen.queryByText('203.0.113.10')).not.toBeInTheDocument();
  });

  it('retains real content while reporting independent refresh and quality errors', async () => {
    state.workspace = workspace({
      realtimeError: new TrafficApiError('network', 'offline'),
      peersError: new TrafficApiError('unavailable', 'not ready', { requestId: 'req-peer' }),
      history: trafficResponse({
        data_quality: {
          unassigned_samples_in_window: 7,
          has_unassigned_or_missing_data: true,
          note: 'ambiguous',
        },
      }),
    });
    render(<DeviceTrafficPanel device={deviceDetail} isAdmin active onSelectPeer={vi.fn()} />);
    await screen.findByTestId('mock-echarts');
    expect(screen.getByText(/保留最后一次真实数据/)).toBeInTheDocument();
    expect(screen.getByText(/7 个未归属样本/)).toBeInTheDocument();
    expect(screen.getByText(/请求 ID：req-peer/)).toBeInTheDocument();
    expect(screen.getByTestId('traffic-chart-data')).toHaveAttribute('data-points', '2');
  });
});
