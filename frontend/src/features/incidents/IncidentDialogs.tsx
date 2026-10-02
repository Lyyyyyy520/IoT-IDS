import { useEffect, useRef, useState } from 'react';
import { IncidentApiError, type CreateIncidentInput, type IncidentDetail, type IncidentRole, type TransitionIncidentInput } from '../../api/v3Incidents';
import { v3DevicesApi, type DeviceListItem } from '../../api/v3Devices';
import { PUBLIC_TEXT_FORBIDDEN, ROLE_LABELS, incidentErrorMessage } from './incidentUi';

export type IncidentDialog = null | 'create' | 'ack' | 'recovering' | 'resolve' | 'false-positive';

function DialogShell({ title, children, onClose }: { title: string; children: React.ReactNode; onClose: () => void }) {
  const panel = useRef<HTMLDivElement>(null);
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;
  useEffect(() => {
    const prior = document.activeElement as HTMLElement | null;
    panel.current?.querySelector<HTMLElement>('input, select, textarea, button')?.focus();
    const key = (event: KeyboardEvent) => {
      if (event.key === 'Escape') { onCloseRef.current(); return; }
      if (event.key !== 'Tab' || !panel.current) return;
      const controls = Array.from(panel.current.querySelectorAll<HTMLElement>(
        'button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
      ));
      if (!controls.length) return;
      const first = controls[0];
      const last = controls[controls.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault(); last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault(); first.focus();
      }
    };
    window.addEventListener('keydown', key);
    return () => { window.removeEventListener('keydown', key); prior?.focus(); };
  }, []);
  return <div className="workspace-modal-backdrop" role="presentation"><div className="workspace-modal" role="dialog" aria-modal="true" aria-labelledby="incident-dialog-title" ref={panel}><header><h2 id="incident-dialog-title">{title}</h2><button type="button" onClick={onClose} aria-label="关闭对话框">×</button></header>{children}</div></div>;
}

function publicWarning(...values: string[]) {
  return values.some((value) => PUBLIC_TEXT_FORBIDDEN.test(value))
    ? 'APP 用户可见文案不能包含 IP、MAC、端口、graph ID、GNN、模型版本或规则表达式。'
    : null;
}

function CreateDialog({ pending, onClose, onCreate }: { pending: boolean; onClose: () => void; onCreate: (input: CreateIncidentInput) => Promise<unknown> }) {
  const [devices, setDevices] = useState<DeviceListItem[]>([]);
  const [deviceError, setDeviceError] = useState<string | null>(null);
  const [selected, setSelected] = useState<Array<{ device_id: string; incident_role: IncidentRole; user_visible: boolean }>>([]);
  const [form, setForm] = useState({ incidentType: '', severity: 'medium', adminTitle: '', adminSummary: '', userTitle: '', userSummary: '', publicProgress: '', publish: true });
  const [error, setError] = useState<string | null>(null);
  const userCopyWarning = publicWarning(form.userTitle, form.userSummary, form.publicProgress);
  useEffect(() => {
    const controller = new AbortController();
    v3DevicesApi.listDevices({ limit: 100, offset: 0 }, controller.signal)
      .then((result) => setDevices(result.items))
      .catch(() => setDeviceError('无法读取真实设备列表，当前不能创建事件。'));
    return () => controller.abort();
  }, []);
  const toggle = (deviceId: string) => setSelected((current) => current.some((item) => item.device_id === deviceId) ? current.filter((item) => item.device_id !== deviceId) : [...current, { device_id: deviceId, incident_role: 'affected', user_visible: form.publish }]);
  const patchDevice = (deviceId: string, patch: Partial<{ incident_role: IncidentRole; user_visible: boolean }>) => setSelected((current) => current.map((item) => {
    if (item.device_id !== deviceId) return item;
    const next = { ...item, ...patch };
    if (next.incident_role !== 'affected') next.user_visible = false;
    return next;
  }));
  const submit = async (event: React.FormEvent) => {
    event.preventDefault(); setError(null);
    const warning = publicWarning(form.userTitle, form.userSummary, form.publicProgress);
    if (warning) { setError(warning); return; }
    if (selected.length === 0) { setError('至少关联一台真实设备。'); return; }
    if (form.publish && !selected.some((item) => item.incident_role === 'affected' && item.user_visible)) {
      setError('发布 APP 提醒时，至少一台 affected 设备必须对用户可见。'); return;
    }
    try {
      await onCreate({
        incident_type: form.incidentType.trim(), severity: form.severity as CreateIncidentInput['severity'], source: 'manual',
        admin_title: form.adminTitle.trim(), admin_summary: form.adminSummary.trim(),
        user_title: form.userTitle.trim(), user_summary: form.userSummary.trim(), devices: selected,
        publish_to_mobile: form.publish, ...(form.publicProgress.trim() ? { public_progress: form.publicProgress.trim() } : {}),
      });
      onClose();
    } catch (caught) { setError(caught instanceof IncidentApiError ? incidentErrorMessage(caught) : '创建事件失败。'); }
  };
  return <DialogShell title="创建人工记录事件" onClose={onClose}><form className="workspace-form" onSubmit={submit}>
    <p className="form-boundary-note">来源固定为 manual；本页面不提供 GNN 来源。</p>
    <div className="form-grid"><label><span>事件类型</span><input required pattern="[a-z0-9][a-z0-9_.-]{0,63}" value={form.incidentType} onChange={(e) => setForm({ ...form, incidentType: e.target.value.toLowerCase() })} /></label><label><span>严重度</span><select value={form.severity} onChange={(e) => setForm({ ...form, severity: e.target.value })}><option value="info">提示</option><option value="low">低</option><option value="medium">中</option><option value="high">高</option><option value="critical">严重</option></select></label></div>
    <label><span>管理员标题</span><input required maxLength={160} value={form.adminTitle} onChange={(e) => setForm({ ...form, adminTitle: e.target.value })} /></label>
    <label><span>管理员摘要 · 仅管理端</span><textarea required maxLength={2000} value={form.adminSummary} onChange={(e) => setForm({ ...form, adminSummary: e.target.value })} /></label>
    <label><span>用户标题 · APP 可见</span><input required maxLength={120} value={form.userTitle} onChange={(e) => setForm({ ...form, userTitle: e.target.value })} /></label>
    <label><span>用户摘要 · APP 可见</span><textarea required maxLength={500} value={form.userSummary} onChange={(e) => setForm({ ...form, userSummary: e.target.value })} /></label>
    <label><span>初始处理进度 · APP 可见</span><textarea maxLength={500} value={form.publicProgress} onChange={(e) => setForm({ ...form, publicProgress: e.target.value })} /></label>
    <p className={userCopyWarning ? 'privacy-warning is-invalid' : 'privacy-warning'} role="status" aria-live="polite">
      {userCopyWarning ?? '公开文案请勿填写 IP、MAC、端口、graph ID、GNN、模型版本或内部规则细节。'}
    </p>
    <label className="checkbox-line"><input type="checkbox" checked={form.publish} onChange={(e) => setForm({ ...form, publish: e.target.checked })} /><span>向当前授权范围发布移动提醒</span></label>
    <fieldset className="device-role-fieldset"><legend>关联真实设备与角色</legend>{deviceError ? <div className="workspace-inline-error">{deviceError}</div> : devices.length === 0 ? <div className="workspace-empty">没有可关联的真实设备</div> : devices.map((device) => {
      const item = selected.find((candidate) => candidate.device_id === device.device_id);
      return <div className="device-role-row" key={device.device_id}><label className="checkbox-line"><input type="checkbox" checked={Boolean(item)} onChange={() => toggle(device.device_id)} /><span>{device.display_name}<small>{device.device_id}</small></span></label>{item && <><select aria-label={`${device.display_name} 事件角色`} value={item.incident_role} onChange={(e) => patchDevice(device.device_id, { incident_role: e.target.value as IncidentRole })}>{(['affected', 'suspected_source', 'observer', 'unknown'] as const).map((role) => <option value={role} key={role}>{ROLE_LABELS[role]}</option>)}</select><label className="checkbox-line"><input type="checkbox" checked={item.user_visible} disabled={item.incident_role !== 'affected'} onChange={(e) => patchDevice(device.device_id, { user_visible: e.target.checked })} /><span>APP 可见</span></label></>}</div>;
    })}</fieldset>
    {error && <div className="workspace-inline-error" role="alert">{error}</div>}
    <footer><button type="button" className="ghost-button" onClick={onClose}>取消</button><button type="submit" className="primary-button" disabled={pending || Boolean(deviceError)}>{pending ? '正在创建…' : '创建事件'}</button></footer>
  </form></DialogShell>;
}

function TransitionDialog({ dialog, detail, pending, onClose, onTransition, onReload }: { dialog: Exclude<IncidentDialog, null | 'create'>; detail: IncidentDetail; pending: boolean; onClose: () => void; onTransition: (action: Exclude<IncidentDialog, null | 'create'>, input: TransitionIncidentInput) => Promise<unknown>; onReload: () => Promise<void> }) {
  const [progress, setProgress] = useState(detail.user_preview.public_progress ?? '');
  const [adminDetails, setAdminDetails] = useState('');
  const [resolution, setResolution] = useState('');
  const [falseReason, setFalseReason] = useState('');
  const [confirmed, setConfirmed] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [conflict, setConflict] = useState(false);
  const progressWarning = publicWarning(progress);
  const submit = async (event: React.FormEvent) => {
    event.preventDefault(); setError(null); setConflict(false);
    const warning = publicWarning(progress); if (warning) { setError(warning); return; }
    if (dialog === 'false-positive' && !confirmed) { setError('请确认误报结案不会删除审计记录。'); return; }
    try {
      await onTransition(dialog, {
        expected_incident_version: detail.incident_version,
        ...(progress.trim() ? { public_progress: progress.trim() } : {}),
        ...(adminDetails.trim() ? { admin_details: adminDetails.trim() } : {}),
        ...(resolution.trim() ? { resolution_summary: resolution.trim() } : {}),
        ...(falseReason.trim() ? { false_positive_reason: falseReason.trim() } : {}),
      });
      onClose();
    } catch (caught) {
      if (caught instanceof IncidentApiError) {
        setConflict(caught.code === 'incident_version_conflict'); setError(incidentErrorMessage(caught));
      } else setError('事件处置失败。');
    }
  };
  const title = { ack: '确认开始处理', recovering: '进入恢复阶段', resolve: '标记事件已解决', 'false-positive': '作为误报结案' }[dialog];
  return <DialogShell title={title} onClose={onClose}><form className="workspace-form" onSubmit={submit}>
    <p>当前版本：v{detail.incident_version}。服务器将执行乐观并发检查。</p>
    <label><span>公开处理进度 · APP 用户可见{dialog === 'recovering' || dialog === 'resolve' || dialog === 'false-positive' ? ' · 必填' : ''}</span><textarea required={dialog !== 'ack'} maxLength={500} value={progress} onChange={(e) => setProgress(e.target.value)} /></label>
    <p className={progressWarning ? 'privacy-warning is-invalid' : 'privacy-warning'} role="status" aria-live="polite">
      {progressWarning ?? '进度文案不会向普通用户展示内部证据；请勿包含 IP、MAC、端口、graph ID 或模型细节。'}
    </p>
    <label><span>内部处置详情 · 仅管理端可见</span><textarea maxLength={2000} value={adminDetails} onChange={(e) => setAdminDetails(e.target.value)} /></label>
    {dialog === 'resolve' && <label><span>解决摘要 · 仅管理端</span><textarea required maxLength={1000} value={resolution} onChange={(e) => setResolution(e.target.value)} /></label>}
    {dialog === 'false-positive' && <><label><span>误报原因 · 仅管理端</span><textarea required maxLength={1000} value={falseReason} onChange={(e) => setFalseReason(e.target.value)} /></label><label className="checkbox-line danger-confirm"><input type="checkbox" checked={confirmed} onChange={(e) => setConfirmed(e.target.checked)} /><span>我确认将事件作为误报终态结案；历史和审计记录不会删除。</span></label></>}
    {error && <div className="workspace-inline-error" role="alert">{error}</div>}
    {conflict && <button type="button" className="ghost-button" onClick={() => void onReload()}>加载服务器最新版本（保留当前草稿）</button>}
    <footer><button type="button" className="ghost-button" onClick={onClose}>取消</button><button type="submit" className={dialog === 'false-positive' ? 'danger-button' : 'primary-button'} disabled={pending || (dialog === 'false-positive' && !confirmed)}>{pending ? '正在提交…' : title}</button></footer>
  </form></DialogShell>;
}

export default function IncidentDialogs({ dialog, detail, pending, onClose, onCreate, onTransition, onReload }: { dialog: IncidentDialog; detail: IncidentDetail | null; pending: boolean; onClose: () => void; onCreate: (input: CreateIncidentInput) => Promise<unknown>; onTransition: (action: Exclude<IncidentDialog, null | 'create'>, input: TransitionIncidentInput) => Promise<unknown>; onReload: () => Promise<void> }) {
  if (!dialog) return null;
  if (dialog === 'create') return <CreateDialog pending={pending} onClose={onClose} onCreate={onCreate} />;
  if (!detail) return null;
  return <TransitionDialog dialog={dialog} detail={detail} pending={pending} onClose={onClose} onTransition={onTransition} onReload={onReload} />;
}
