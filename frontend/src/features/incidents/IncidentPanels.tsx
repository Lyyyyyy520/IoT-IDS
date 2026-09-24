import { useNavigate } from 'react-router-dom';
import type { IncidentDetail, IncidentListItem, IncidentStatus } from '../../api/v3Incidents';
import type { IncidentFilters } from './useIncidentWorkspace';
import {
  ROLE_LABELS, SEVERITY_LABELS, SOURCE_LABELS, STATUS_LABELS, localTime,
} from './incidentUi';

const STATUSES: Array<'all' | IncidentStatus> = ['all', 'open', 'acknowledged', 'recovering', 'resolved', 'false_positive'];

export function IncidentListPanel({
  filters, onFilters, onClear, items, total, page, pageSize, loading, error,
  selectedId, onSelect, onPage, onRefresh, lastUpdatedAt,
}: {
  filters: IncidentFilters;
  onFilters: (value: IncidentFilters) => void;
  onClear: () => void;
  items: IncidentListItem[];
  total: number;
  page: number;
  pageSize: number;
  loading: boolean;
  error: string | null;
  selectedId: string | null;
  onSelect: (id: string) => void;
  onPage: (page: number) => void;
  onRefresh: () => void;
  lastUpdatedAt: string | null;
}) {
  const patch = (value: Partial<IncidentFilters>) => onFilters({ ...filters, ...value });
  const pageCount = Math.max(1, Math.ceil(total / pageSize));
  return (
    <section className="incident-panel incident-list-panel" aria-labelledby="incident-list-title">
      <header className="incident-panel-heading">
        <div><p>RECORDED INCIDENTS</p><h2 id="incident-list-title">已记录事件</h2></div>
        <button type="button" className="ghost-button" onClick={onRefresh} disabled={loading}>刷新</button>
      </header>
      <div className="incident-filters">
        <label className="filter-wide"><span>搜索</span><input type="search" value={filters.search} onChange={(e) => patch({ search: e.target.value })} placeholder="标题、类型或事件 ID" /></label>
        <label><span>状态</span><select value={filters.status} onChange={(e) => patch({ status: e.target.value as IncidentFilters['status'] })}>{STATUSES.map((value) => <option value={value} key={value}>{value === 'all' ? '全部状态' : STATUS_LABELS[value]}</option>)}</select></label>
        <label><span>严重度</span><select value={filters.severity} onChange={(e) => patch({ severity: e.target.value as IncidentFilters['severity'] })}><option value="all">全部级别</option>{(['info', 'low', 'medium', 'high', 'critical'] as const).map((value) => <option value={value} key={value}>{SEVERITY_LABELS[value]}</option>)}</select></label>
        <label><span>来源</span><select value={filters.source} onChange={(e) => patch({ source: e.target.value as IncidentFilters['source'] })}><option value="all">全部来源</option>{(['manual', 'rule', 'system'] as const).map((value) => <option value={value} key={value}>{SOURCE_LABELS[value]}</option>)}</select></label>
        <label><span>设备 ID</span><input value={filters.deviceId} onChange={(e) => patch({ deviceId: e.target.value })} /></label>
        <label><span>开始时间</span><input type="datetime-local" value={filters.from} onChange={(e) => patch({ from: e.target.value })} /></label>
        <label><span>结束时间</span><input type="datetime-local" value={filters.to} onChange={(e) => patch({ to: e.target.value })} /></label>
        <button type="button" className="ghost-button clear-filter" onClick={onClear}>清空筛选</button>
      </div>
      <div className="list-meta"><span>共 {total} 条</span><span>最后更新 {lastUpdatedAt ? localTime(lastUpdatedAt) : '尚未成功读取'}</span></div>
      {error && <div className="workspace-inline-error" role="alert">{error}</div>}
      <div className="incident-list" aria-busy={loading}>
        {loading && items.length === 0 ? <div className="workspace-empty">正在读取真实事件…</div> : error && items.length === 0 ? (
          <div className="workspace-empty"><strong>事件列表暂不可用</strong><span>修复连接或权限后可重新读取。</span></div>
        ) : items.length === 0 ? (
          <div className="workspace-empty"><strong>当前没有已记录事件</strong><span>这不代表系统安全或没有攻击。</span></div>
        ) : items.map((item) => (
          <button type="button" className={`incident-list-row ${selectedId === item.incident_id ? 'selected' : ''}`} onClick={() => onSelect(item.incident_id)} key={item.incident_id}>
            <span className={`severity-marker severity-${item.severity}`}>{SEVERITY_LABELS[item.severity]}</span>
            <span className="incident-list-copy"><strong>{item.admin_title}</strong><small>{item.incident_type} · {SOURCE_LABELS[item.source]} · v{item.incident_version}</small><span>{item.latest_public_progress ?? '尚无公开处理进度'}</span></span>
            <span className="incident-list-side"><b className={`status-pill status-${item.status}`}>{STATUS_LABELS[item.status]}</b><small>受影响 {item.affected_device_count}</small><time>首次 {localTime(item.first_seen_at)}</time><time>最近 {localTime(item.last_seen_at)}</time></span>
          </button>
        ))}
      </div>
      <footer className="pagination"><button type="button" disabled={page === 0 || loading} onClick={() => onPage(page - 1)}>上一页</button><span>第 {page + 1} / {pageCount} 页</span><button type="button" disabled={page + 1 >= pageCount || loading} onClick={() => onPage(page + 1)}>下一页</button></footer>
    </section>
  );
}

function transitionActions(status: IncidentStatus): Array<'ack' | 'recovering' | 'resolve' | 'false-positive'> {
  if (status === 'open') return ['ack', 'false-positive'];
  if (status === 'acknowledged') return ['recovering', 'resolve', 'false-positive'];
  if (status === 'recovering') return ['resolve', 'false-positive'];
  return [];
}

const ACTION_LABELS = { ack: '确认处理', recovering: '进入恢复中', resolve: '标记已解决', 'false-positive': '作为误报结案' } as const;

export function IncidentDetailPanel({
  detail, loading, error, pending, onAction,
}: {
  detail: IncidentDetail | null;
  loading: boolean;
  error: string | null;
  pending: boolean;
  onAction: (action: 'ack' | 'recovering' | 'resolve' | 'false-positive') => void;
}) {
  const navigate = useNavigate();
  if (loading && !detail) return <section className="incident-panel incident-detail-panel"><div className="workspace-empty">正在读取事件详情…</div></section>;
  if (error && !detail) return <section className="incident-panel incident-detail-panel"><div className="workspace-inline-error" role="alert">{error}</div></section>;
  if (!detail) return <section className="incident-panel incident-detail-panel"><div className="workspace-empty"><strong>选择事件查看详情</strong><span>详情、时间线和处置操作均来自后端。</span></div></section>;
  const grouped = (role: keyof typeof ROLE_LABELS) => detail.devices.filter((item) => item.incident_role === role);
  return (
    <section className="incident-panel incident-detail-panel" aria-labelledby="incident-detail-title">
      <header className="incident-detail-header">
        <div><p>{detail.incident_id}</p><h2 id="incident-detail-title">{detail.admin_title}</h2><span>{detail.admin_summary}</span></div>
        <div className="detail-badges"><b className={`severity-${detail.severity}`}>{SEVERITY_LABELS[detail.severity]}</b><b className={`status-${detail.status}`}>{STATUS_LABELS[detail.status]}</b></div>
      </header>
      {error && <div className="workspace-inline-error" role="alert">{error}</div>}
      <dl className="incident-facts">
        <div><dt>事件类型</dt><dd>{detail.incident_type}</dd></div><div><dt>来源</dt><dd>{SOURCE_LABELS[detail.source]}</dd></div>
        <div><dt>首次发现</dt><dd>{localTime(detail.first_seen_at)}</dd></div><div><dt>最近发现</dt><dd>{localTime(detail.last_seen_at)}</dd></div>
        <div><dt>事件版本</dt><dd>{detail.incident_version}</dd></div><div><dt>移动提醒</dt><dd>{detail.mobile_published ? '已发布到授权范围' : '未向 APP 发布'}</dd></div>
      </dl>
      <div className="incident-actions" aria-label="合法事件处置操作">
        {transitionActions(detail.status).map((action) => <button type="button" className={action === 'false-positive' ? 'danger-outline' : 'primary-button'} onClick={() => onAction(action)} disabled={pending} key={action}>{ACTION_LABELS[action]}</button>)}
        {transitionActions(detail.status).length === 0 && <span>该事件已处于终态，本版本不提供重新打开操作。</span>}
      </div>
      <div className="detail-sections">
        <section><h3>设备角色</h3>{detail.devices.length === 0 && <p className="workspace-empty">当前没有可读取的关联设备记录；未生成替代信息。</p>}{(['affected', 'suspected_source', 'observer', 'unknown'] as const).map((role) => grouped(role).length > 0 && <div className="role-group" key={role}><h4>{ROLE_LABELS[role]}</h4>{grouped(role).map((device) => <article key={`${role}-${device.device_id}`}><div><strong>{device.display_name}</strong><span>{device.device_id} · {device.device_type} · {device.area_id ?? '未分区'}</span></div><div><span>{device.user_visible ? 'APP 用户可见' : '仅管理端可见'}</span>{role === 'affected' && <button type="button" onClick={() => navigate(`/devices?device_id=${encodeURIComponent(device.device_id)}`)}>查看设备</button>}</div></article>)}</div>)}</section>
        <section className="user-preview"><h3>APP 用户可见预览</h3><p className="boundary-label public">公开字段 · 不含疑似来源和技术证据</p><strong>{detail.user_preview.user_title}</strong><p>{detail.user_preview.user_summary}</p><p><b>处理进度：</b>{detail.user_preview.public_progress ?? '管理员尚未提供公开进度'}</p></section>
        <section><h3>结案信息</h3><p><b>解决摘要：</b>{detail.resolution_summary ?? '尚无'}</p><p><b>误报原因：</b>{detail.false_positive_reason ?? '尚无'}</p></section>
        <section className="timeline-section"><h3>处置时间线</h3>{detail.timeline.map((entry) => <article className="timeline-entry" key={entry.timeline_id}><span className="timeline-dot" aria-hidden="true" /><div><header><strong>{entry.action}</strong><time>{localTime(entry.occurred_at)}</time></header><p>{entry.actor_username} · {entry.actor_role} · {STATUS_LABELS[entry.resulting_status]} · v{entry.incident_version}</p>{entry.public_progress && <div className="timeline-public"><b>APP 用户可见：</b>{entry.public_progress}</div>}{entry.admin_details && <div className="timeline-admin"><b>仅管理端：</b>{entry.admin_details}</div>}</div></article>)}</section>
      </div>
    </section>
  );
}
