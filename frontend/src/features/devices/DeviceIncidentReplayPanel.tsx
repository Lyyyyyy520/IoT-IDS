import { useEffect, useState } from 'react';
import { IncidentApiError, v3IncidentsApi } from '../../api/v3Incidents';
import type { IncidentListItem } from '../../api/v3Incidents';
import { IncidentReplayPanel } from '../incidents/IncidentReplayPanel';
import { STATUS_LABELS } from '../incidents/incidentUi';

const INCIDENT_PAGE_SIZE = 20;

export default function DeviceIncidentReplayPanel({
  deviceId,
  deviceName,
}: {
  deviceId: string;
  deviceName: string;
}) {
  const [items, setItems] = useState<IncidentListItem[]>([]);
  const [total, setTotal] = useState(0);
  const [selectedId, setSelectedId] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    setBusy(true);
    setError(null);
    setItems([]);
    setTotal(0);
    setSelectedId('');
    v3IncidentsApi.listIncidents({ device_id: deviceId, limit: INCIDENT_PAGE_SIZE, offset: 0 }, controller.signal)
      .then((response) => {
        if (controller.signal.aborted) return;
        setItems(response.items);
        setTotal(response.total);
        setSelectedId(response.items[0]?.incident_id ?? '');
      })
      .catch((cause: unknown) => {
        if (controller.signal.aborted) return;
        setError(cause instanceof IncidentApiError ? cause.message : '相关事件读取失败');
      })
      .finally(() => {
        if (!controller.signal.aborted) setBusy(false);
      });
    return () => controller.abort();
  }, [deviceId]);

  return (
    <section className="detail-section device-incident-replay" aria-labelledby="device-replay-title">
      <div className="detail-section-title"><h3 id="device-replay-title">相关事件与回放</h3></div>
      <p className="device-replay-context">事件按当前设备筛选；回放只展示该设备相关的留存记录。摄像头画面未留存时会明确标示。</p>
      {busy && <p className="device-replay-hint" role="status">正在读取该设备的相关事件…</p>}
      {error && <div className="devices-inline-error" role="alert">{error}</div>}
      {!busy && !error && items.length === 0 && (
        <p className="device-replay-empty">没有找到与此设备关联的已记录事件。</p>
      )}
      {items.length > 0 && (
        <>
          <label className="device-replay-select">
            <span>选择事件</span>
            <select value={selectedId} onChange={(event) => setSelectedId(event.target.value)}>
              {items.map((item) => (
                <option value={item.incident_id} key={item.incident_id}>
                  {item.admin_title} · {STATUS_LABELS[item.status]}
                </option>
              ))}
            </select>
          </label>
          {total > items.length && <p className="device-replay-hint">显示最近 {items.length} 条，共 {total} 条相关事件。</p>}
          {selectedId && <IncidentReplayPanel
            incidentId={selectedId}
            focusDeviceId={deviceId}
            focusDeviceName={deviceName}
          />}
        </>
      )}
    </section>
  );
}
