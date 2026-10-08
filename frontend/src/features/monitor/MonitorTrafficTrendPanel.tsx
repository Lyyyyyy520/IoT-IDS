import { useEffect, useState } from 'react';
import ReactEChartsCore from 'echarts-for-react/lib/core';
import * as echarts from 'echarts/core';
import { LineChart } from 'echarts/charts';
import { GridComponent, LegendComponent, TooltipComponent } from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';
import { TrafficApiError, v3TrafficApi, type TrafficResponse, type TrafficResolution } from '../../api/v3Traffic';
import type { MonitorDevice } from '../../api/v3Monitor';
import { trafficErrorMessage } from '../traffic/trafficUi';

echarts.use([LineChart, GridComponent, LegendComponent, TooltipComponent, CanvasRenderer]);

const RANGE_CONFIG: Record<string, { label: string; milliseconds: number; resolution: Exclude<TrafficResolution, 'auto'> }> = {
  '15m': { label: '近 15 分钟', milliseconds: 15 * 60_000, resolution: 'minute' },
  '1h': { label: '近 1 小时', milliseconds: 60 * 60_000, resolution: 'minute' },
  '6h': { label: '近 6 小时', milliseconds: 6 * 60 * 60_000, resolution: '5minute' },
  '24h': { label: '近 24 小时', milliseconds: 24 * 60 * 60_000, resolution: '5minute' },
  '7d': { label: '近 7 天', milliseconds: 7 * 24 * 60 * 60_000, resolution: 'hour' },
};

function rateLabel(value: number): string {
  if (value >= 1024 * 1024) return `${(value / (1024 * 1024)).toFixed(1)} MiB/s`;
  if (value >= 1024) return `${(value / 1024).toFixed(1)} KiB/s`;
  return `${value.toFixed(value < 10 ? 1 : 0)} B/s`;
}

function chartSeries(response: TrafficResponse, direction: 'tx' | 'rx'): Array<[string, number | null]> {
  const divisor = response.resolution === 'hour' ? 3600 : response.resolution === '5minute' ? 300 : 60;
  const result: Array<[string, number | null]> = [];
  let previousTime: number | null = null;
  for (const point of response.series) {
    const time = Date.parse(point.bucket_start);
    if (previousTime !== null && time - previousTime > divisor * 1_500) {
      result.push([new Date(previousTime + divisor * 1000).toISOString(), null]);
    }
    result.push([point.bucket_start, (direction === 'tx' ? point.tx_bytes : point.rx_bytes) / divisor]);
    previousTime = time;
  }
  return result;
}

function trafficOption(response: TrafficResponse) {
  const timeLabel = (value: string | number) => new Intl.DateTimeFormat('zh-CN', {
    hour: '2-digit', minute: '2-digit', hour12: false,
  }).format(new Date(value));
  return {
    animation: false,
    grid: { left: 56, right: 12, top: 34, bottom: 28 },
    legend: { data: ['发送', '接收'], top: 0, left: 4, itemWidth: 14, itemHeight: 8, textStyle: { color: '#9db4bf', fontSize: 14 } },
    tooltip: {
      trigger: 'axis',
      textStyle: { fontSize: 14 },
      formatter: (items: Array<{ seriesName: string; value: [string, number | null] }>) => {
        const timestamp = items[0]?.value?.[0];
        if (!timestamp) return '';
        const lines = [timeLabel(timestamp)];
        items.forEach((item) => {
          if (item.value[1] !== null) lines.push(`${item.seriesName}：${rateLabel(item.value[1])}`);
        });
        return lines.join('<br/>');
      },
    },
    xAxis: {
      type: 'time',
      axisLine: { lineStyle: { color: '#31505d' } },
      axisTick: { show: false },
      axisLabel: { color: '#78919d', fontSize: 12, hideOverlap: true, formatter: timeLabel },
      splitLine: { show: false },
    },
    yAxis: {
      type: 'value', min: 0,
      axisLine: { show: false },
      axisLabel: { color: '#78919d', fontSize: 12, formatter: (value: number) => rateLabel(value).replace(/\sB\/s|\sKiB\/s|\sMiB\/s/, '') },
      splitLine: { lineStyle: { color: '#19313d' } },
    },
    series: [
      { name: '发送', type: 'line', showSymbol: false, connectNulls: false, lineStyle: { width: 2, color: '#52c2cc' }, data: chartSeries(response, 'tx') },
      { name: '接收', type: 'line', showSymbol: false, connectNulls: false, lineStyle: { width: 2, color: '#8faee8' }, data: chartSeries(response, 'rx') },
    ],
  };
}

export default function MonitorTrafficTrendPanel({ device, range }: { device: MonitorDevice | null; range: string }) {
  const [response, setResponse] = useState<TrafficResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);
  const config = RANGE_CONFIG[range] ?? RANGE_CONFIG['1h'];

  useEffect(() => {
    setResponse(null);
    setError(null);
    if (!device) {
      setLoading(false);
      return undefined;
    }
    let stopped = false;
    let pending = false;
    let controller: AbortController | null = null;
    const load = async () => {
      if (pending || stopped) return;
      pending = true;
      controller = new AbortController();
      setLoading(true);
      const to = new Date();
      try {
        const result = await v3TrafficApi.getTraffic(device.device_id, {
          from: new Date(to.getTime() - config.milliseconds),
          to,
          resolution: config.resolution,
        }, controller.signal);
        if (!stopped) {
          setResponse(result);
          setError(null);
        }
      } catch (caught) {
        if (stopped || (caught instanceof TrafficApiError && caught.kind === 'aborted')) return;
        setError(trafficErrorMessage(caught));
      } finally {
        pending = false;
        if (!stopped) setLoading(false);
      }
    };
    void load();
    const interval = window.setInterval(() => void load(), 30_000);
    return () => {
      stopped = true;
      window.clearInterval(interval);
      controller?.abort();
    };
  }, [device?.device_id, config.milliseconds, config.resolution, refreshKey]);

  return (
    <section className="monitor-panel traffic-trend-panel" aria-labelledby="monitor-traffic-title">
      <div className="panel-heading traffic-trend-heading">
        <div>
          <p className="eyebrow">NETWORK TRAFFIC</p>
          <h2 id="monitor-traffic-title">设备流量趋势</h2>
        </div>
        <div className="traffic-trend-controls">
          <span>{config.label}</span>
          <button type="button" onClick={() => setRefreshKey((key) => key + 1)} disabled={!device || loading} aria-label="刷新流量趋势">刷新</button>
        </div>
      </div>
      {!device ? (
        <div className="panel-empty compact"><strong>尚未选择设备</strong></div>
      ) : error && !response ? (
        <div className="traffic-trend-empty" role="status">{error}</div>
      ) : loading && !response ? (
        <div className="traffic-trend-empty" role="status">正在读取真实流量聚合…</div>
      ) : !(response && response.availability.available && response.series.length > 0) ? (
        <div className="traffic-trend-empty" role="status">
          <strong>{response?.availability.reason === 'no_samples' || (response && response.series.length === 0) ? '当前时间范围没有流量样本' : '流量趋势不可用'}</strong>
          {response?.availability.reason && response.availability.reason !== 'no_samples' && <span>{response.availability.reason}</span>}
        </div>
      ) : (
        <>
          {error && <div className="traffic-trend-stale" role="status">更新失败，显示上次取得的流量数据：{error}</div>}
          <div className="traffic-trend-chart" aria-label={`${device.display_name} 发送与接收流量折线图`}>
            <ReactEChartsCore echarts={echarts} option={trafficOption(response)} notMerge lazyUpdate style={{ width: '100%', height: '100%' }} opts={{ renderer: 'canvas' }} />
          </div>
          <footer className="traffic-trend-footer">
            <span>{device.display_name}</span>
            <time dateTime={response.generated_at}>数据更新 {new Intl.DateTimeFormat('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false }).format(new Date(response.generated_at))}</time>
          </footer>
        </>
      )}
    </section>
  );
}
