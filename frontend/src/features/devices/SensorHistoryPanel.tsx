import { useEffect, useMemo, useState } from 'react';
import { v3DevicesApi, type SensorReading, type SensorTelemetryResponse } from '../../api/v3Devices';
import type { DeviceDetail } from '../../api/v3Devices';

type Range = '1h' | '6h' | '24h' | '7d';
const RANGE_MS: Record<Range, number> = {
  '1h': 60 * 60 * 1000,
  '6h': 6 * 60 * 60 * 1000,
  '24h': 24 * 60 * 60 * 1000,
  '7d': 7 * 24 * 60 * 60 * 1000,
};

function localTime(value: string | null | undefined): string {
  if (!value) return '暂无';
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
  }).format(parsed);
}

function chartPath(rows: SensorReading[], field: 'temperature_c' | 'humidity_percent') {
  const usable = rows.flatMap((row, index) => {
    const value = row[field];
    return value === null ? [] : [{ index, value }];
  });
  if (!usable.length) return { path: '', points: [] as { row: SensorReading; x: number; y: number; value: number }[], min: 0, max: 0 };
  const min = Math.min(...usable.map((item) => item.value));
  const max = Math.max(...usable.map((item) => item.value));
  const x = (index: number) => 120 + (rows.length <= 1 ? 0 : (index / (rows.length - 1)) * 494);
  const y = (value: number) => 18 + (max === min ? 36 : ((max - value) / (max - min)) * 72);
  return {
    path: usable.map((item, index) => `${index ? 'L' : 'M'} ${x(item.index).toFixed(1)} ${y(item.value).toFixed(1)}`).join(' '),
    points: usable.map((item) => ({ row: rows[item.index], x: x(item.index), y: y(item.value), value: item.value })),
    min,
    max,
  };
}

function Trend({
  rows, field, label, unit, tone,
}: {
  rows: SensorReading[];
  field: 'temperature_c' | 'humidity_percent';
  label: string;
  unit: string;
  tone: 'temperature' | 'humidity';
}) {
  const result = chartPath(rows, field);
  const values = rows.flatMap((row) => row[field] === null ? [] : [row[field] as number]);
  if (!values.length) return <div className="sensor-empty-inline">所选时间范围内没有{label}读数。</div>;
  return (
    <figure className={`sensor-trend sensor-trend-${tone}`}>
      <figcaption><strong>{label}趋势</strong><span>{result.min.toFixed(1)}–{result.max.toFixed(1)} {unit}</span></figcaption>
      <svg viewBox="0 0 640 142" role="img" aria-label={`${label}历史趋势，单位 ${unit}`}>
        {[30, 66, 102].map((y) => <line key={y} x1="120" x2="614" y1={y} y2={y} className="sensor-chart-grid" />)}
        <text x="2" y="33">{result.max.toFixed(1)}</text>
        <text x="2" y="105">{result.min.toFixed(1)}</text>
        <path d={result.path} className="sensor-chart-line" />
        {result.points.map((point) => (
          <circle
            key={point.row.observation_id}
            cx={point.x}
            cy={point.y}
            r="5"
            className="sensor-chart-point"
            tabIndex={0}
            role="img"
            aria-label={`${localTime(point.row.observed_at)} ${label} ${point.value} ${unit}`}
          >
            <title>{`${localTime(point.row.observed_at)} · ${label} ${point.value} ${unit} · 服务器接收 ${localTime(point.row.received_at)}`}</title>
          </circle>
        ))}
      </svg>
      <div className="sensor-chart-axis"><span>{localTime(rows[0]?.observed_at)}</span><span>{localTime(rows[rows.length - 1]?.observed_at)}</span></div>
    </figure>
  );
}

export default function SensorHistoryPanel({ device }: { device: DeviceDetail }) {
  const [range, setRange] = useState<Range>('6h');
  const [data, setData] = useState<SensorTelemetryResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [reload, setReload] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    const end = new Date();
    setLoading(true);
    setError(null);
    setData(null);
    void v3DevicesApi.getSensorTelemetry(device.device_id, {
      from: new Date(end.getTime() - RANGE_MS[range]),
      to: end,
    }, controller.signal).then((result) => {
      if (!controller.signal.aborted && result.device_id === device.device_id) setData(result);
    }).catch((caught: unknown) => {
      if (!controller.signal.aborted) setError(caught instanceof Error ? caught.message : '传感器数据读取失败');
    }).finally(() => {
      if (!controller.signal.aborted) setLoading(false);
    });
    return () => controller.abort();
  }, [device.device_id, device.received_at, range, reload]);

  const chartRows = useMemo(() => {
    const rows = data?.series ?? [];
    if (rows.length <= 240) return rows;
    const step = Math.ceil(rows.length / 240);
    return rows.filter((_, index) => index % step === 0 || index === rows.length - 1);
  }, [data?.series]);

  const latest = data?.latest;
  const isSensor = device.device_type.toLowerCase() === 'sensor';
  if (!isSensor) return null;

  return (
    <section className="sensor-history-panel" aria-labelledby="sensor-history-title">
      <header className="sensor-history-heading">
        <div><h3 id="sensor-history-title">温湿度传感器</h3><span>来自已保存的设备 MQTT 上报；当前固件未标注 DHT 读取是否成功</span></div>
        <div className="sensor-history-controls">
          <label>趋势范围
            <select aria-label="传感器趋势范围" value={range} onChange={(event) => setRange(event.target.value as Range)}>
              <option value="1h">最近 1 小时</option><option value="6h">最近 6 小时</option>
              <option value="24h">最近 24 小时</option><option value="7d">最近 7 天</option>
            </select>
          </label>
          <button type="button" onClick={() => setReload((value) => value + 1)} disabled={loading}>刷新</button>
        </div>
      </header>
      {device.connection_status === 'offline' && <p className="sensor-state-note error" role="status">设备离线。以下若有数值，为最后一次已接收读数，不代表当前值。</p>}
      {device.connection_status === 'stale' && <p className="sensor-state-note warning" role="status">设备上报已过期；最新读数可能延迟。</p>}
      {device.connection_status === 'unknown' && <p className="sensor-state-note warning" role="status">尚无已确认的设备连接状态。</p>}
      {loading && !data && <div className="sensor-empty-inline" role="status">正在读取真实传感器观测…</div>}
      {error && <div className="sensor-error" role="alert">传感器数据读取失败：{error}</div>}
      {!loading && !error && data && !data.availability.available && (
        <div className="sensor-empty-inline" role="status">
          {data.availability.reason === 'no_sensor_samples'
            ? '所选时间范围内没有已保存的温湿度观测。'
            : '当前设备资料类型不是传感器。'}
        </div>
      )}
      {latest && (
        <>
          <div className="sensor-current-values">
            <div><span>最近温度</span><strong>{latest.temperature_c === null ? '暂无数据' : `${latest.temperature_c.toFixed(1)} °C`}</strong><small>最近更新：{latest.temperature_c === null ? '暂无记录' : localTime(latest.observed_at)}</small></div>
            <div><span>最近湿度</span><strong>{latest.humidity_percent === null ? '暂无数据' : `${latest.humidity_percent.toFixed(1)} %RH`}</strong><small>最近更新：{latest.humidity_percent === null ? '暂无记录' : localTime(latest.observed_at)}</small></div>
            <div className="sensor-reading-time"><span>设备观测时间</span><strong>{localTime(latest.observed_at)}</strong><small>服务器接收 {localTime(latest.received_at)}</small></div>
          </div>
          {data?.truncated && <p className="sensor-state-note warning">历史结果达到接口上限，图表只显示返回的最新观测区间。</p>}
          <div className="sensor-trend-grid">
            <Trend rows={chartRows} field="temperature_c" label="温度" unit="°C" tone="temperature" />
            <Trend rows={chartRows} field="humidity_percent" label="湿度" unit="%" tone="humidity" />
          </div>
        </>
      )}
    </section>
  );
}
