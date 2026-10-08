import { useEffect, useRef, useState } from 'react';
import { IncidentApiError, v3IncidentsApi } from '../../api/v3Incidents';
import type { IncidentReplay } from '../../api/v3Incidents';
import { STATUS_LABELS, localTime } from './incidentUi';
import { utc } from './useIncidentWorkspace';

const GAP_LABELS: Record<string, string> = {
  no_observations_in_window: '时段内无设备观测',
  no_retained_observation: '没有留存观测',
  observation_gap_over_stale_threshold: '观测间隔超过数据新鲜阈值',
};

type ReplayEvent =
  | { kind: 'progress'; at: string; label: string; text: string }
  | { kind: 'observation'; at: string; label: string; text: string }
  | { kind: 'status'; at: string; label: string; text: string }
  | { kind: 'network'; at: string; label: string; text: string }
  | { kind: 'gap'; at: string; label: string; text: string };

function replayEvents(replay: IncidentReplay, focusDeviceId?: string): ReplayEvent[] {
  const names = new Map(replay.devices.map((device) => [device.device_id, device.display_name]));
  const label = (deviceId: string) => names.get(deviceId) ?? deviceId;
  return [
    ...replay.incident_timeline.map<ReplayEvent>((item) => ({
      kind: 'progress',
      at: item.occurred_at,
      label: '处置进度',
      text: [
        STATUS_LABELS[item.resulting_status],
        item.public_progress,
        item.admin_details,
      ].filter(Boolean).join(' · '),
    })),
    ...replay.devices
      .filter((device) => !focusDeviceId || device.device_id === focusDeviceId)
      .flatMap((device) => device.observations.flatMap<ReplayEvent>((point) => {
        const values = [
          point.temperature_c === null ? null : `温度 ${point.temperature_c} °C`,
          point.humidity_percent === null ? null : `湿度 ${point.humidity_percent} %RH`,
        ].filter(Boolean);
        return values.length ? [{
          kind: 'observation',
          at: point.received_at,
          label: device.display_name,
          text: values.join(' · '),
        }] : [];
      })),
    ...replay.connection_status_changes.filter((item) => !focusDeviceId || item.device_id === focusDeviceId).map<ReplayEvent>((item) => ({
      kind: 'status',
      at: item.occurred_at,
      label: label(item.device_id),
      text: `连接状态 ${item.from_status} → ${item.to_status}（${item.source === 'timeout' ? '超时判定' : '上报'}）`,
    })),
    ...replay.relationships.filter((item) => !focusDeviceId
      || item.source_device_id === focusDeviceId
      || item.target_device_id === focusDeviceId).map<ReplayEvent>((item) => ({
      kind: 'network',
      at: item.bucket_start,
      label: '已观测通信关系',
      text: `${label(item.source_device_id)} → ${label(item.target_device_id)} · ${item.protocol} · ${item.flows} 条流 · ${item.bytes} 字节`,
    })),
    ...replay.gaps.filter((item) => !focusDeviceId || item.device_id === focusDeviceId).map<ReplayEvent>((item) => ({
      kind: 'gap',
      at: item.from,
      label: '数据缺口',
      text: `${label(item.device_id)} · ${localTime(item.from)} 至 ${localTime(item.to)} · ${GAP_LABELS[item.reason] ?? item.reason}`,
    })),
  ].sort((a, b) => Date.parse(a.at) - Date.parse(b.at));
}

export function IncidentReplayPanel({
  incidentId,
  focusDeviceId,
  focusDeviceName,
}: {
  incidentId: string;
  focusDeviceId?: string;
  focusDeviceName?: string;
}) {
  const [replay, setReplay] = useState<IncidentReplay | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [from, setFrom] = useState('');
  const [to, setTo] = useState('');
  const requestController = useRef<AbortController | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    requestController.current?.abort();
    requestController.current = controller;
    setBusy(true);
    setError(null);
    setReplay(null);
    setFrom('');
    setTo('');
    v3IncidentsApi.getIncidentReplay(incidentId, undefined, controller.signal)
      .then((value) => { if (!controller.signal.aborted) setReplay(value); })
      .catch((cause: unknown) => {
        if (!controller.signal.aborted) setError(cause instanceof IncidentApiError ? cause.message : '事件回放读取失败');
      })
      .finally(() => { if (!controller.signal.aborted) setBusy(false); });
    return () => {
      controller.abort();
      if (requestController.current === controller) requestController.current = null;
    };
  }, [incidentId]);

  const submit = () => {
    const range: { from?: string; to?: string } = {};
    if (from) range.from = utc(from);
    if (to) range.to = utc(to);
    requestController.current?.abort();
    const controller = new AbortController();
    requestController.current = controller;
    setBusy(true);
    setError(null);
    v3IncidentsApi.getIncidentReplay(incidentId, range, controller.signal)
      .then((value) => { if (!controller.signal.aborted) setReplay(value); })
      .catch((cause: unknown) => {
        if (!controller.signal.aborted) setError(cause instanceof IncidentApiError ? cause.message : '事件回放读取失败');
      })
      .finally(() => { if (!controller.signal.aborted) setBusy(false); });
  };

  const events = replay ? replayEvents(replay, focusDeviceId) : [];
  const visibleGaps = replay?.gaps.filter((item) => !focusDeviceId || item.device_id === focusDeviceId) ?? [];
  const visibleDevices = replay?.devices.filter((device) => !focusDeviceId || device.device_id === focusDeviceId) ?? [];

  return (
    <section className="incident-replay" aria-labelledby="replay-title">
      <div className="replay-heading">
        <h3 id="replay-title">事件回放时间轴</h3>
        <p>{focusDeviceId
          ? `当前设备视角：${focusDeviceName ?? focusDeviceId}。显示该设备的传感器读数、连接状态、数据缺口及相关通信关系，并保留事件处置进度。`
          : '按统一时间标尺汇总事件发生前后的处置进度、设备传感器读数、连接状态与通信关系变化，并标注数据缺失区间。'}</p>
      </div>
      <div className="replay-controls">
        <label><span>开始时间</span><input type="datetime-local" value={from} onChange={(e) => setFrom(e.target.value)} /></label>
        <label><span>结束时间</span><input type="datetime-local" value={to} onChange={(e) => setTo(e.target.value)} /></label>
        <button type="button" className="primary-button" onClick={submit} disabled={busy}>查询</button>
      </div>
      {busy && <p className="replay-hint">正在读取事件时间轴…</p>}
      {error && <div className="workspace-inline-error" role="alert">{error}</div>}
      {replay && (
        <>
          <p className="replay-hint">时间范围：{localTime(replay.window.from)} 至 {localTime(replay.window.to)}</p>
          <div className="replay-capabilities">
            <span>{replay.traffic_available ? '通信关系历史可用' : '通信关系历史不可用'}</span>
            <span>{replay.capabilities.connection_status_history ? '连接状态历史可用' : '连接状态历史未配置'}</span>
            <span>{replay.capabilities.camera_media ? '含摄像头画面' : '摄像头画面未留存'}</span>
          </div>
          {visibleDevices.length > 0 && (
            <div className="replay-current-state">
              {visibleDevices.map((device) => (
                <p key={device.device_id}>
                  <strong>{device.display_name}</strong>（{device.device_id} · {device.device_type}）当前连接状态 {device.current_connection_status}
                  {device.current_last_received_at ? ` · 最近上报 ${localTime(device.current_last_received_at)}` : ''}
                </p>
              ))}
            </div>
          )}
          {replay.truncated && <div className="replay-gap-note">事件较多，时间轴结果已截断。</div>}
          {visibleGaps.length > 0 && <div className="replay-gap-note">{focusDeviceId ? '当前设备视角中' : '时间轴中'}存在 {visibleGaps.length} 段数据缺失区间，已在下方标注。</div>}
          {events.length === 0 ? (
            <p className="workspace-empty">所选时段没有可回放的留存记录。</p>
          ) : (
            <ol className="replay-timeline">
              {events.map((item, index) => (
                <li key={`${item.at}:${index}`} className={`replay-event${item.kind === 'network' ? ' replay-network' : ''}${item.kind === 'gap' ? ' replay-gap' : ''}`}>
                  <span className="replay-marker" aria-hidden="true" />
                  <div>
                    <header><strong>{item.label}</strong><time>{localTime(item.at)}</time></header>
                    <p>{item.text}</p>
                  </div>
                </li>
              ))}
            </ol>
          )}
        </>
      )}
    </section>
  );
}
