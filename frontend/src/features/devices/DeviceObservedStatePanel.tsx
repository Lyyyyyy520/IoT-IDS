import { useEffect, useMemo, useState } from 'react';
import { v3DevicesApi, type DeviceDetail, type DeviceStateHistoryResponse, type DeviceTelemetryValue } from '../../api/v3Devices';

const HISTORY_WINDOW_MS = 7 * 24 * 60 * 60 * 1000;

const FIELD_LABELS: Record<string, string> = {
  state: '状态',
  door_state: '门状态',
  contact: '门磁状态',
  contact_state: '门磁状态',
  lock_state: '门禁状态',
  open: '开启状态',
  closed: '关闭状态',
  locked: '锁定状态',
  on: '开关状态',
  alarm: '报警状态',
  smoke: '烟雾读数',
  smoke_level: '烟雾读数',
  smoke_ppm: '烟雾浓度',
  concentration: '设备浓度读数',
  ppm: '设备浓度读数（ppm）',
  temp: '温度',
  temperature: '温度',
  temperature_c: '温度',
  humidity: '湿度',
  humidity_percent: '湿度',
  level: '设备读数（level）',
  power: '设备读数（power）',
  angle: '角度',
};

function localTime(value: string | null | undefined): string {
  if (!value) return '暂无记录';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
  }).format(parsed);
}

function fieldLabel(key: string): string {
  return FIELD_LABELS[key.toLowerCase()] ?? `设备字段：${key}`;
}

function valueLabel(key: string, value: DeviceTelemetryValue): string {
  if (typeof value === 'boolean') {
    if (key.toLowerCase() === 'alarm') return value ? '报警' : '未报警';
    if (key.toLowerCase() === 'locked') return value ? '已锁定' : '已解锁';
    if (key.toLowerCase() === 'open') return value ? '开启' : '关闭';
    if (key.toLowerCase() === 'on') return value ? '开启' : '关闭';
    return String(value);
  }
  if (value === null) return 'null';
  if (typeof value === 'string') {
    const enumLabels: Record<string, string> = {
      locked: '已锁定', unlocked: '已解锁', open: '已打开', closed: '已关闭',
      recording: '采集中', idle: '空闲', alarm: '报警', triggered: '已触发',
      normal: '正常', clear: '未报警',
    };
    return enumLabels[value.toLowerCase()] ?? value;
  }
  const normalized = key.toLowerCase();
  const unit = normalized === 'temp' || normalized === 'temperature' || normalized === 'temperature_c'
    ? ' °C'
    : normalized === 'humidity' || normalized === 'humidity_percent'
      ? ' %RH'
      : normalized === 'angle' ? '°' : '';
  return `${value}${unit}`;
}

function observedChanges(data: DeviceStateHistoryResponse | null) {
  const rows = data?.series ?? [];
  const changes: { observationId: number; observedAt: string; receivedAt: string; fields: { key: string; before: DeviceTelemetryValue | undefined; after: DeviceTelemetryValue }[] }[] = [];
  for (let index = 1; index < rows.length; index += 1) {
    const previous = rows[index - 1];
    const current = rows[index];
    const keys = new Set([...Object.keys(previous.telemetry), ...Object.keys(current.telemetry)]);
    const fields = [...keys]
      .filter((key) => key.toLowerCase() !== 'device_type')
      .flatMap((key) => previous.telemetry[key] === current.telemetry[key]
        ? []
        : [{ key, before: previous.telemetry[key], after: current.telemetry[key] }]);
    if (fields.length) changes.push({
      observationId: current.observation_id,
      observedAt: current.observed_at,
      receivedAt: current.received_at,
      fields,
    });
  }
  return changes.reverse();
}

export default function DeviceObservedStatePanel({ device }: { device: DeviceDetail }) {
  const [data, setData] = useState<DeviceStateHistoryResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [reload, setReload] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    const end = new Date();
    setLoading(true);
    setError(null);
    setData(null);
    void v3DevicesApi.getDeviceStateHistory(device.device_id, {
      from: new Date(end.getTime() - HISTORY_WINDOW_MS),
      to: end,
    }, controller.signal).then((result) => {
      if (!controller.signal.aborted && result.device_id === device.device_id) setData(result);
    }).catch((caught: unknown) => {
      if (!controller.signal.aborted) setError(caught instanceof Error ? caught.message : '设备遥测读取失败');
    }).finally(() => {
      if (!controller.signal.aborted) setLoading(false);
    });
    return () => controller.abort();
  }, [device.device_id, device.received_at, reload]);

  const latest = data?.latest;
  const fields = useMemo(() => Object.entries(latest?.telemetry ?? {})
    .filter(([key]) => key.toLowerCase() !== 'device_type'), [latest?.telemetry]);
  const changes = useMemo(() => observedChanges(data), [data]);

  return (
    <section className="observed-state-panel" aria-labelledby="observed-state-title">
      <header className="observed-state-heading">
        <div>
          <h3 id="observed-state-title">设备上报信息与状态变化</h3>
          <span>读取数据库留存的设备遥测；状态变化按相邻观测值计算</span>
        </div>
        <button type="button" onClick={() => setReload((current) => current + 1)} disabled={loading}>
          {loading ? '读取中…' : '刷新'}
        </button>
      </header>
      {loading && !data && <div className="observed-state-empty" role="status">正在读取最近 7 天的设备观测…</div>}
      {error && <div className="observed-state-empty observed-state-error" role="alert">设备状态历史读取失败：{error}</div>}
      {!loading && !error && data && !data.availability.available && (
        <div className="observed-state-empty" role="status">
          {data.availability.reason === 'no_state_observations'
            ? '最近 7 天没有已保存的设备状态观测。'
            : '观测记录中没有可展示的设备遥测字段。'}
        </div>
      )}
      {latest && fields.length > 0 && (
        <>
          <div className="observed-telemetry-grid">
            {fields.map(([key, value]) => (
              <div key={key}>
                <span>{fieldLabel(key)}</span>
                <strong>{valueLabel(key, value)}</strong>
              </div>
            ))}
          </div>
          <p className="observed-state-time">
            最近观测：{localTime(latest.observed_at)} · 服务器接收：{localTime(latest.received_at)}
          </p>
        </>
      )}
      {data?.truncated && <p className="observed-state-note">历史记录达到接口上限，仅显示窗口内返回的最近 1000 条。</p>}
      {data?.availability.available && (
        <div className="observed-change-section">
          <h4>最近 7 天的观测变化</h4>
          {changes.length === 0 ? (
            <p className="observed-state-empty">现有相邻观测中没有可确认的字段变化；首条记录没有前值可比较。</p>
          ) : (
            <ol className="observed-change-list">
              {changes.slice(0, 20).map((change) => (
                <li key={change.observationId}>
                  <time dateTime={change.observedAt}>{localTime(change.observedAt)}</time>
                  <ul>
                    {change.fields.map((field) => (
                      <li key={field.key}>
                        <strong>{fieldLabel(field.key)}</strong>
                        <span>{field.before === undefined ? '未提供' : valueLabel(field.key, field.before)} → {valueLabel(field.key, field.after)}</span>
                      </li>
                    ))}
                  </ul>
                  <small>服务器接收：{localTime(change.receivedAt)}</small>
                </li>
              ))}
            </ol>
          )}
          <p className="observed-state-note">变化记录仅根据所选范围内相邻的真实遥测计算；未观测区间不会补值或推断。</p>
        </div>
      )}
    </section>
  );
}
