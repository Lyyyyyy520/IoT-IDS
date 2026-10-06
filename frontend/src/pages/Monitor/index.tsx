import { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../../contexts/AuthContext';
import { useMonitorStore } from '../../features/monitor/monitorStore';
import CommunityDeviceDetailDialog from '../../features/devices/CommunityDeviceDetailDialog';
import {
  CapabilityPanel,
  DeviceDetailsPanel,
  DeviceListPanel,
  RecentEventsPanel,
  SystemHealthPanel,
  statusLabel,
  type DeviceFilter,
} from '../../features/monitor/MonitorPanels';
import type { ConnectionStatus } from '../../api/v3Monitor';
import './monitor.css';

const ERROR_TITLES = {
  unauthorized: '登录状态已失效',
  forbidden: '当前账号权限不足',
  unavailable: '监视数据库或服务尚未准备',
  network: '无法连接后端服务',
  invalid_response: '后端响应格式不符合契约',
  http: '监视请求失败',
  aborted: '请求已取消',
};

function formatSyncTime(value: string | null) {
  if (!value) return '尚未同步';
  return new Intl.DateTimeFormat('zh-CN', {
    hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
  }).format(new Date(value));
}

export default function MonitorPage() {
  const monitor = useMonitorStore();
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [selectedIncidentId, setSelectedIncidentId] = useState<string | null>(null);
  const [detailDeviceId, setDetailDeviceId] = useState<string | null>(null);
  const closeDeviceDetail = useCallback(() => setDetailDeviceId(null), []);
  const [search, setSearch] = useState('');
  const [filter, setFilter] = useState<DeviceFilter>('all');
  const [typeFilter, setTypeFilter] = useState('all');
  const [riskFilter, setRiskFilter] = useState('all');
  const [reportRange, setReportRange] = useState('24h');
  const devices = monitor.snapshot?.devices ?? [];
  const deviceTypes = useMemo(() => [...new Set(devices.map((device) => device.device_type))].sort(), [devices]);

  useEffect(() => {
    if (selectedId && devices.some((device) => device.device_id === selectedId)) return;
    setSelectedId(devices[0]?.device_id ?? null);
  }, [devices, selectedId]);

  const visibleDevices = useMemo(() => {
    const query = search.trim().toLocaleLowerCase('zh-CN');
    return devices.filter((device) => {
      if (filter !== 'all' && device.connection_status !== filter) return false;
      if (typeFilter !== 'all' && device.device_type !== typeFilter) return false;
      if (riskFilter === 'undetected' && device.risk_level !== null) return false;
      if (riskFilter === 'normal' && device.risk_level !== 0) return false;
      if (riskFilter === 'alerted' && (device.risk_level === null || device.risk_level === 0)) return false;
      if (!query) return true;
      return [device.display_name, device.device_type, device.area_id, device.ip_address, device.device_id]
        .some((value) => value?.toLocaleLowerCase('zh-CN').includes(query));
    });
  }, [devices, filter, typeFilter, riskFilter, search]);

  const visibleIds = useMemo(() => new Set(visibleDevices.map((device) => device.device_id)), [visibleDevices]);
  const incidentSummaries = useMemo(() => {
    const items = [...(monitor.snapshot?.incidents?.active ?? []), ...(monitor.snapshot?.incidents?.recent ?? [])];
    return items.filter((item, index, all) => all.findIndex((candidate) => candidate.incident_id === item.incident_id) === index);
  }, [monitor.snapshot?.incidents]);
  const selectedIncident = incidentSummaries.find((item) => item.incident_id === selectedIncidentId) ?? null;
  const highlightedDeviceIds = useMemo(
    () => new Set(selectedIncident?.affected_device_ids ?? []),
    [selectedIncident],
  );
  const monitorReportUrl = useMemo(() => {
    const params = new URLSearchParams({ range: reportRange });
    if (search.trim()) params.set('search', search.trim());
    if (filter !== 'all') params.set('connection_status', filter);
    if (typeFilter !== 'all') params.set('device_type', typeFilter);
    if (riskFilter !== 'all') params.set('risk_filter', riskFilter);
    return `/api/v3/monitor/export?${params.toString()}`;
  }, [filter, reportRange, riskFilter, search, typeFilter]);

  const selectedDevice = devices.find((device) => device.device_id === selectedId) ?? null;
  const counts = devices.reduce<Record<ConnectionStatus, number>>(
    (result, device) => {
      result[device.connection_status] += 1;
      return result;
    },
    { online: 0, stale: 0, offline: 0, unknown: 0 },
  );

  const handleLogout = async () => {
    await logout();
    navigate('/login', { replace: true });
  };

  const initialFailure = monitor.phase === 'error' && monitor.snapshot === null;
  return (
    <main className="monitor-workspace">
      <header className="monitor-topbar">
        <div className="monitor-brand">
          <span className="brand-mark" aria-hidden="true">ID</span>
          <div>
            <strong>IoT IDS<span className="monitor-title-space"> </span><br className="monitor-title-break" />实时监视</strong>
            <span>管理员与值守人员工作区</span>
          </div>
        </div>
        <div className="status-counters" aria-label="设备状态统计">
          {(['online', 'stale', 'offline', 'unknown'] as ConnectionStatus[]).map((status) => (
            <div className={`counter status-${status}`} key={status}>
              <span>{statusLabel(status)}</span><strong>{monitor.snapshot ? counts[status] : '—'}</strong>
            </div>
          ))}
        </div>
        <div className="monitor-sync">
          <div className={`live-state live-${monitor.realtime}`}>
            <span aria-hidden="true" />
            {monitor.realtime === 'connected' ? '实时已连接' :
              monitor.realtime === 'connecting' ? '实时连接中' :
                monitor.realtime === 'disconnected' ? '实时已断开' : '实时未启动'}
          </div>
          <span className="sync-time">最后同步 {formatSyncTime(monitor.lastSyncedAt)}</span>
          <button
            type="button"
            className="sync-button"
            onClick={() => void monitor.resync('manual')}
            disabled={monitor.phase === 'loading' || monitor.phase === 'resyncing'}
          >
            {monitor.phase === 'resyncing' ? '重同步中…' : '重新同步'}
          </button>
          <label className="monitor-report-range">
            <span>趋势范围</span>
            <select aria-label="趋势范围" value={reportRange} onChange={(event) => setReportRange(event.target.value)}>
              <option value="15m">近 15 分钟</option>
              <option value="1h">近 1 小时</option>
              <option value="6h">近 6 小时</option>
              <option value="24h">近 24 小时</option>
              <option value="7d">近 7 天</option>
            </select>
          </label>
          <a className="sync-button monitor-report-export-link" href={monitorReportUrl}>
            导出报表 CSV
          </a>
        </div>
        <div className="monitor-account">
          <span>{user?.username}</span>
          <button type="button" onClick={() => void handleLogout()}>退出</button>
        </div>
      </header>

      {(monitor.realtime === 'disconnected' || monitor.stale) && monitor.snapshot && (
        <div className="monitor-warning" role="status">
          实时连接已断开或正在恢复，当前保留最后一次真实快照，数据可能过期。
        </div>
      )}
      {monitor.error && monitor.snapshot && (
        <div className="monitor-warning error-warning" role="alert">
          {ERROR_TITLES[monitor.error.kind]}：{monitor.error.message}
          {monitor.error.requestId ? `（请求 ${monitor.error.requestId}）` : ''}
        </div>
      )}

      {monitor.phase === 'loading' ? (
        <section className="monitor-gate" aria-live="polite">
          <span className="loading-ring" aria-hidden="true" />
          <h1>正在读取真实监视快照</h1>
          <p>成功读取数据库状态后才会建立实时事件连接。</p>
        </section>
      ) : initialFailure ? (
        <section className="monitor-gate error-gate" role="alert">
          <span className="gate-code">{monitor.error?.kind === 'unavailable' ? '503' : monitor.error?.kind === 'forbidden' ? '403' : monitor.error?.kind === 'unauthorized' ? '401' : 'ERR'}</span>
          <h1>{monitor.error ? ERROR_TITLES[monitor.error.kind] : '监视工作区不可用'}</h1>
          <p>{monitor.error?.message}</p>
          {monitor.error?.requestId && <small>请求 ID：{monitor.error.requestId}</small>}
          <button type="button" onClick={() => void monitor.resync('explicit_retry')}>重试真实数据</button>
        </section>
      ) : monitor.snapshot ? (
        <div className="monitor-grid">
          <DeviceListPanel
            devices={visibleDevices}
            selectedId={selectedId}
            onSelect={(deviceId) => { setSelectedId(deviceId); setDetailDeviceId(deviceId); }}
            search={search}
            onSearch={setSearch}
            filter={filter}
            onFilter={setFilter}
            typeFilter={typeFilter}
            onTypeFilter={setTypeFilter}
            riskFilter={riskFilter}
            onRiskFilter={setRiskFilter}
            deviceTypes={deviceTypes}
            onManageDevice={(deviceId) => navigate(`/devices?device_id=${encodeURIComponent(deviceId)}`)}
          />
          <div className="monitor-center-column">
            <CapabilityPanel
              capability={monitor.snapshot.capabilities.graph}
              graph={monitor.snapshot.graph}
              onSelect={(deviceId) => { setSelectedId(deviceId); setDetailDeviceId(deviceId); }}
              visibleIds={visibleIds}
              highlightedDeviceIds={highlightedDeviceIds}
              highlightLabel={selectedIncident?.admin_title ?? null}
              onClearHighlight={() => setSelectedIncidentId(null)}
            />
            <RecentEventsPanel
              events={monitor.recentEvents}
              incident={monitor.snapshot.capabilities.incident}
              incidents={monitor.snapshot.incidents}
              selectedIncidentId={selectedIncidentId}
              onSelectIncident={setSelectedIncidentId}
              onViewIncident={(incidentId) => navigate(`/incidents?incident_id=${encodeURIComponent(incidentId)}`)}
            />
          </div>
          <div className="monitor-right-column">
            <DeviceDetailsPanel device={selectedDevice} />
            <SystemHealthPanel components={monitor.snapshot.system_components} />
          </div>
        </div>
      ) : null}
      <CommunityDeviceDetailDialog
        deviceId={detailDeviceId}
        summary={devices.find((device) => device.device_id === detailDeviceId) ?? null}
        onClose={closeDeviceDetail}
      />
    </main>
  );
}
