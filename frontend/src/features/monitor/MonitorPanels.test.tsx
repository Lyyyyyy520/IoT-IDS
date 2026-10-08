import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { buildGraphOption, DeviceDetailsPanel, graphTypeSymbols } from './MonitorPanels';
import { validSnapshot } from '../../test/monitorFixtures';
import type { MonitorGraphSnapshot } from '../../api/v3Monitor';

function graphSeries(graph: MonitorGraphSnapshot, highlighted = new Set<string>()) {
  const option = buildGraphOption(graph, highlighted) as {
    series: Array<{
      data: Array<{ id: string; symbol: string; label: { fontSize: number }; itemStyle: { opacity: number } }>;
      links: Array<{ relation_type: string; lineStyle: { type: string; opacity: number } }>;
    }>;
  };
  return option.series[0];
}

describe('monitor relationship graph', () => {
  it('assigns distinct device shapes and preserves observed versus configured edge styles', () => {
    const graph: MonitorGraphSnapshot = {
      ...validSnapshot.graph!,
      nodes: [
        ...validSnapshot.graph!.nodes,
        { id: 'door-01', label: '门禁', type: 'door', status: 'online', risk_level: 0, risk_name: '正常', ip: null },
      ],
      edges: [
        { ...validSnapshot.graph!.edges[0], relation_type: 'observed_traffic' },
        { source: 'light-01', target: 'door-01', bytes: 0, packets: 0, protocols: ['coap'], relation_type: 'configured_linkage' },
      ],
    };
    const symbols = graphTypeSymbols(graph.nodes);
    const series = graphSeries(graph);

    expect(new Set(Object.values(symbols)).size).toBe(3);
    expect(series.data.every((node) => node.label.fontSize >= 14)).toBe(true);
    expect(series.links[0]).toMatchObject({ relation_type: 'observed_traffic', lineStyle: { type: 'solid' } });
    expect(series.links[1]).toMatchObject({ relation_type: 'configured_linkage', lineStyle: { type: 'dashed' } });
  });

  it('highlights incident devices and their edges while dimming unrelated graph items', () => {
    const graph: MonitorGraphSnapshot = {
      ...validSnapshot.graph!,
      nodes: [
        ...validSnapshot.graph!.nodes,
        { id: 'door-01', label: '门禁', type: 'door', status: 'online', risk_level: 0, risk_name: '正常', ip: null },
      ],
      edges: [
        { ...validSnapshot.graph!.edges[0], relation_type: 'observed_traffic' },
        { source: 'light-01', target: 'door-01', bytes: 0, packets: 0, protocols: ['coap'], relation_type: 'configured_linkage' },
      ],
    };
    const series = graphSeries(graph, new Set(['camera-01']));

    expect(series.data.find((node) => node.id === 'camera-01')?.itemStyle.opacity).toBe(1);
    expect(series.data.find((node) => node.id === 'light-01')?.itemStyle.opacity).toBeLessThan(1);
    expect(series.links[0].lineStyle.opacity).toBe(1);
    expect(series.links[1].lineStyle.opacity).toBeLessThan(1);
  });
});

describe('device behavior evidence scope', () => {
  it('distinguishes missing GNN conclusions from risk results and names unsupported baseline dimensions', () => {
    const device = validSnapshot.devices[0];
    const noResult = { ...device, risk_level: null, risk_name: null, risk_confidence: null, risk_detected_at: null };
    const riskResult = { ...device, risk_level: 2, risk_name: '拒绝服务', risk_confidence: 0.91, risk_detected_at: '2026-09-20T02:00:00Z' };

    const emptyView = render(<DeviceDetailsPanel device={noResult} />);
    expect(screen.getByText(/暂无该设备的 GNN 风险结论/)).toBeInTheDocument();
    expect(screen.getByText(/已接收遥测不等于已完成风险检测/)).toBeInTheDocument();
    expect(screen.getByText(/监视快照未提供运行或配置状态/)).toBeInTheDocument();
    expect(screen.getByText('通信基线尚未接入')).toBeInTheDocument();

    emptyView.rerender(<DeviceDetailsPanel device={{ ...device, risk_level: null, received_at: null, sources: [] }} />);
    expect(screen.getByText(/设备遥测覆盖不足：尚无后端接收的设备观测/)).toBeInTheDocument();
    expect(screen.getByText('尚无后端接收的设备观测')).toBeInTheDocument();

    emptyView.rerender(<DeviceDetailsPanel device={riskResult} />);
    expect(screen.getByText('设备图 GNN 分类')).toBeInTheDocument();
    expect(screen.getByText(/置信度 91%/)).toBeInTheDocument();
    expect(screen.getByText('通信基线尚未接入')).toBeInTheDocument();
    expect(screen.getByText(/尚未计算频率、协议\/端口、连接对象/)).toBeInTheDocument();
  });
});
