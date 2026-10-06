import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  IncidentApiError,
  type HelpRequestDetail,
  type HelpRequestItem,
  type HelpStatus,
  type SupportContact,
  type UpdateHelpRequestInput,
  type UpdateSupportContactInput,
} from '../../api/v3Incidents';
import type { HelpFilters } from './useIncidentWorkspace';
import {
  HELP_CATEGORY_LABELS, HELP_STATUS_LABELS, incidentErrorMessage, localTime,
} from './incidentUi';

function legalHelpStatuses(status: HelpStatus): Array<Exclude<HelpStatus, 'open'>> {
  if (status === 'open') return ['in_progress', 'closed'];
  if (status === 'in_progress') return ['waiting_for_user', 'closed'];
  if (status === 'waiting_for_user') return ['in_progress', 'closed'];
  return [];
}

export function HelpRequestsPanel({
  filters, onFilters, onClear, items, total, page, pageSize, loading, error,
  selectedId, detail, detailLoading, detailError, pending,
  onSelect, onPage, onRefresh, onUpdate, onReload,
}: {
  filters: HelpFilters;
  onFilters: (value: HelpFilters) => void;
  onClear: () => void;
  items: HelpRequestItem[];
  total: number;
  page: number;
  pageSize: number;
  loading: boolean;
  error: string | null;
  selectedId: string | null;
  detail: HelpRequestDetail | null;
  detailLoading: boolean;
  detailError: string | null;
  pending: boolean;
  onSelect: (id: string) => void;
  onPage: (page: number) => void;
  onRefresh: () => void;
  onUpdate: (input: UpdateHelpRequestInput) => Promise<unknown>;
  onReload: () => Promise<void>;
}) {
  const navigate = useNavigate();
  const patch = (value: Partial<HelpFilters>) => onFilters({ ...filters, ...value });
  const [draftOwner, setDraftOwner] = useState<string | null>(null);
  const [status, setStatus] = useState<Exclude<HelpStatus, 'open'>>('in_progress');
  const [publicResponse, setPublicResponse] = useState('');
  const [internalNote, setInternalNote] = useState('');
  const [assignedTo, setAssignedTo] = useState('');
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [conflict, setConflict] = useState(false);
  useEffect(() => {
    if (!detail || detail.help_request_id === draftOwner) return;
    setDraftOwner(detail.help_request_id);
    setStatus(legalHelpStatuses(detail.status)[0] ?? 'closed');
    setPublicResponse(detail.public_response ?? '');
    setInternalNote(detail.internal_note ?? '');
    setAssignedTo(detail.assigned_to ? String(detail.assigned_to) : '');
    setSubmitError(null); setConflict(false);
  }, [detail, draftOwner]);
  const submit = async (event: React.FormEvent) => {
    event.preventDefault(); if (!detail) return; setSubmitError(null); setConflict(false);
    try {
      await onUpdate({
        expected_request_version: detail.request_version, status,
        ...(publicResponse.trim() ? { public_response: publicResponse.trim() } : {}),
        ...(internalNote.trim() ? { internal_note: internalNote.trim() } : {}),
        ...(assignedTo ? { assigned_to: Number(assignedTo) } : {}),
      });
    } catch (caught) {
      if (caught instanceof IncidentApiError) {
        setConflict(caught.code === 'help_request_version_conflict');
        setSubmitError(incidentErrorMessage(caught));
      } else setSubmitError('求助处理失败。');
    }
  };
  const pages = Math.max(1, Math.ceil(total / pageSize));
  return <div className="support-grid">
    <section className="incident-panel support-list-panel">
      <header className="incident-panel-heading"><div><p>MOBILE HELP REQUESTS</p><h2>用户求助</h2></div><button type="button" className="ghost-button" onClick={onRefresh}>刷新</button></header>
      <div className="incident-filters help-filters">
        <label><span>状态</span><select value={filters.status} onChange={(e) => patch({ status: e.target.value as HelpFilters['status'] })}><option value="all">全部</option>{(['open', 'in_progress', 'waiting_for_user', 'closed'] as const).map((value) => <option value={value} key={value}>{HELP_STATUS_LABELS[value]}</option>)}</select></label>
        <label><span>分类</span><select value={filters.category} onChange={(e) => patch({ category: e.target.value as HelpFilters['category'] })}><option value="all">全部</option>{(['device_issue', 'security_question', 'service_problem', 'other'] as const).map((value) => <option value={value} key={value}>{HELP_CATEGORY_LABELS[value]}</option>)}</select></label>
        <label><span>用户 ID</span><input inputMode="numeric" value={filters.userId} onChange={(e) => patch({ userId: e.target.value.replace(/\D/g, '') })} /></label>
        <label><span>设备 ID</span><input value={filters.deviceId} onChange={(e) => patch({ deviceId: e.target.value })} /></label>
        <label><span>事件 ID</span><input value={filters.incidentId} onChange={(e) => patch({ incidentId: e.target.value })} /></label>
        <label><span>开始时间</span><input type="datetime-local" value={filters.from} onChange={(e) => patch({ from: e.target.value })} /></label>
        <label><span>结束时间</span><input type="datetime-local" value={filters.to} onChange={(e) => patch({ to: e.target.value })} /></label>
        <button type="button" className="ghost-button" onClick={onClear}>清空</button>
      </div>
      {error && <div className="workspace-inline-error" role="alert">{error}</div>}
      <div className="help-list" aria-busy={loading}>{loading && items.length === 0 ? <div className="workspace-empty">正在读取用户求助…</div> : error && items.length === 0 ? <div className="workspace-empty"><strong>求助列表暂不可用</strong><span>修复连接或权限后可重新读取。</span></div> : items.length === 0 ? <div className="workspace-empty"><strong>当前没有用户求助</strong><span>页面不会生成示例请求。</span></div> : items.map((item) => <button type="button" className={`help-row ${selectedId === item.help_request_id ? 'selected' : ''}`} onClick={() => onSelect(item.help_request_id)} key={item.help_request_id}><div><strong>{HELP_CATEGORY_LABELS[item.category]}</strong><span>{item.help_request_id}</span><p>{item.user_message}</p></div><div><b>{HELP_STATUS_LABELS[item.status]}</b><small>用户 {item.user_id} · v{item.request_version}</small><time>{localTime(item.updated_at)}</time></div></button>)}</div>
      <footer className="pagination"><button type="button" disabled={page === 0 || loading} onClick={() => onPage(page - 1)}>上一页</button><span>第 {page + 1} / {pages} 页</span><button type="button" disabled={page + 1 >= pages || loading} onClick={() => onPage(page + 1)}>下一页</button></footer>
    </section>
    <section className="incident-panel help-detail-panel">
      {detailLoading && !detail ? <div className="workspace-empty">正在读取求助详情…</div> : detailError && !detail ? <div className="workspace-inline-error">{detailError}</div> : !detail ? <div className="workspace-empty"><strong>选择一条用户求助</strong><span>用户正文始终按纯文本显示。</span></div> : <>
        <header className="incident-detail-header"><div><p>{detail.help_request_id}</p><h2>{HELP_CATEGORY_LABELS[detail.category]}</h2><span>用户 {detail.user_id} · 请求版本 v{detail.request_version}</span></div><b className={`status-${detail.status}`}>{HELP_STATUS_LABELS[detail.status]}</b></header>
        {detailError && <div className="workspace-inline-error">{detailError}</div>}
        <section className="plain-message"><h3>用户正文</h3><p>{detail.user_message}</p></section>
        <dl className="incident-facts"><div><dt>关联设备</dt><dd>{detail.device_id ? <button type="button" className="text-link" onClick={() => navigate(`/devices?device_id=${encodeURIComponent(detail.device_id!)}`)}>{detail.device_id}</button> : '无'}</dd></div><div><dt>关联事件</dt><dd>{detail.incident_id ? <button type="button" className="text-link" onClick={() => navigate(`/incidents?incident_id=${encodeURIComponent(detail.incident_id!)}`)}>{detail.incident_id}</button> : '无'}</dd></div><div><dt>创建时间</dt><dd>{localTime(detail.created_at)}</dd></div><div><dt>更新时间</dt><dd>{localTime(detail.updated_at)}</dd></div></dl>
        <div className="response-boundaries"><section><span className="boundary-label public">APP 用户可见</span><p>{detail.public_response ?? '尚未回复用户'}</p></section><section><span className="boundary-label admin">仅 Web 管理端</span><p>{detail.internal_note ?? '尚无内部备注'}</p></section></div>
        {legalHelpStatuses(detail.status).length === 0 ? <div className="terminal-note">该求助已关闭，不提供重新打开操作。</div> : <form className="workspace-form compact-form" onSubmit={submit}><label><span>处理状态</span><select value={status} onChange={(e) => setStatus(e.target.value as Exclude<HelpStatus, 'open'>)}>{legalHelpStatuses(detail.status).map((value) => <option value={value} key={value}>{HELP_STATUS_LABELS[value]}</option>)}</select></label><label><span>公开回复 · APP 用户可见</span><textarea maxLength={1000} value={publicResponse} onChange={(e) => setPublicResponse(e.target.value)} /></label><label><span>内部备注 · 仅 Web 可见</span><textarea maxLength={2000} value={internalNote} onChange={(e) => setInternalNote(e.target.value)} /></label><label><span>分配给用户 ID</span><input inputMode="numeric" value={assignedTo} onChange={(e) => setAssignedTo(e.target.value.replace(/\D/g, ''))} /></label>{submitError && <div className="workspace-inline-error">{submitError}</div>}{conflict && <button type="button" className="ghost-button" onClick={() => void onReload()}>加载最新版本（保留草稿）</button>}<button type="submit" className="primary-button" disabled={pending}>{pending ? '提交中…' : '更新处理进度'}</button></form>}
        <section className="timeline-section"><h3>求助时间线</h3>{detail.timeline.map((entry) => <article className="timeline-entry" key={entry.timeline_id}><span className="timeline-dot" /><div><header><strong>{entry.action}</strong><time>{localTime(entry.occurred_at)}</time></header><p>{entry.actor_username} · {entry.actor_role} · {HELP_STATUS_LABELS[entry.resulting_status]} · v{entry.request_version}</p>{entry.public_response && <div className="timeline-public"><b>APP 用户可见：</b>{entry.public_response}</div>}{entry.internal_note && <div className="timeline-admin"><b>仅 Web 管理端：</b>{entry.internal_note}</div>}</div></article>)}</section>
      </>}
    </section>
  </div>;
}

export function SupportContactPanel({ contact, loading, error, isAdmin, pending, onLoad, onUpdate }: { contact: SupportContact | null; loading: boolean; error: string | null; isAdmin: boolean; pending: boolean; onLoad: () => void; onUpdate: (input: UpdateSupportContactInput) => Promise<unknown> }) {
  const [draft, setDraft] = useState({ display_name: '', phone: '', email: '', working_hours: '', public_note: '', enabled: true });
  const [draftVersion, setDraftVersion] = useState<number | null>(null);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [conflict, setConflict] = useState(false);
  useEffect(() => { onLoad(); }, [onLoad]);
  useEffect(() => {
    if (!contact?.available || draftVersion === contact.config_version) return;
    setDraft({ display_name: contact.display_name, phone: contact.phone ?? '', email: contact.email ?? '', working_hours: contact.working_hours ?? '', public_note: contact.public_note ?? '', enabled: contact.enabled });
    setDraftVersion(contact.config_version);
  }, [contact, draftVersion]);
  const submit = async (event: React.FormEvent) => {
    event.preventDefault(); setSubmitError(null); setConflict(false);
    try {
      await onUpdate({ display_name: draft.display_name.trim(), phone: draft.phone.trim() || null, email: draft.email.trim() || null, working_hours: draft.working_hours.trim() || null, public_note: draft.public_note.trim() || null, enabled: draft.enabled, expected_config_version: contact?.available ? contact.config_version : 0 });
    } catch (caught) {
      if (caught instanceof IncidentApiError) { setConflict(caught.code === 'support_config_version_conflict'); setSubmitError(incidentErrorMessage(caught)); }
      else setSubmitError('联系方式更新失败。');
    }
  };
  return <section className="incident-panel contact-panel"><header className="incident-panel-heading"><div><p>PUBLIC APP CONTACT</p><h2>APP 联系方式</h2></div><span>{isAdmin ? '管理员 · 可编辑' : '值守人员 · 只读'}</span></header>
    {loading && !contact ? <div className="workspace-empty">正在读取联系方式…</div> : error && !contact ? <div className="workspace-inline-error">{error}</div> : !contact?.available ? <div className="workspace-empty"><strong>尚未配置公开联系方式</strong><span>系统不会生成默认电话或邮箱。</span>{isAdmin && <button type="button" className="primary-button" onClick={() => setDraftVersion(0)}>开始配置</button>}</div> : null}
    {error && contact && <div className="workspace-inline-error">{error}</div>}
    {(!contact?.available && draftVersion !== 0) ? null : isAdmin ? <form className="workspace-form contact-form" onSubmit={submit}><label><span>显示名称</span><input required maxLength={120} value={draft.display_name} onChange={(e) => setDraft({ ...draft, display_name: e.target.value })} /></label><div className="form-grid"><label><span>电话</span><input pattern="[0-9+() .-]{5,32}" value={draft.phone} onChange={(e) => setDraft({ ...draft, phone: e.target.value })} /></label><label><span>邮箱</span><input type="email" maxLength={254} value={draft.email} onChange={(e) => setDraft({ ...draft, email: e.target.value })} /></label></div><label><span>工作时间</span><input maxLength={200} value={draft.working_hours} onChange={(e) => setDraft({ ...draft, working_hours: e.target.value })} /></label><label><span>公开说明 · APP 用户可见</span><textarea maxLength={500} value={draft.public_note} onChange={(e) => setDraft({ ...draft, public_note: e.target.value })} /></label><label className="checkbox-line"><input type="checkbox" checked={draft.enabled} onChange={(e) => setDraft({ ...draft, enabled: e.target.checked })} /><span>在 APP 中公开；禁用后 APP 将看不到这些联系方式</span></label><p>配置版本：{contact?.available ? contact.config_version : 0}</p>{submitError && <div className="workspace-inline-error">{submitError}</div>}{conflict && <button type="button" className="ghost-button" onClick={onLoad}>加载服务器最新配置</button>}<button type="submit" className="primary-button" disabled={pending}>{pending ? '保存中…' : '保存公开联系方式'}</button></form> : contact?.available ? <div className="contact-readonly"><dl className="incident-facts"><div><dt>显示名称</dt><dd>{contact.display_name}</dd></div><div><dt>状态</dt><dd>{contact.enabled ? 'APP 可见' : '已禁用，APP 不可见'}</dd></div><div><dt>电话</dt><dd>{contact.phone ?? '未配置'}</dd></div><div><dt>邮箱</dt><dd>{contact.email ?? '未配置'}</dd></div><div><dt>工作时间</dt><dd>{contact.working_hours ?? '未配置'}</dd></div><div><dt>配置版本</dt><dd>{contact.config_version}</dd></div></dl><section className="plain-message"><h3>公开说明</h3><p>{contact.public_note ?? '未配置'}</p></section></div> : null}
  </section>;
}
