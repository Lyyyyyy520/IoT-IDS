import { lazy, Suspense, useCallback, useEffect, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useAuth } from '../../contexts/AuthContext';
import { IncidentDetailPanel, IncidentListPanel } from '../../features/incidents/IncidentPanels';
import { incidentErrorMessage } from '../../features/incidents/incidentUi';
import { useIncidentWorkspace } from '../../features/incidents/useIncidentWorkspace';
import type { IncidentDialog } from '../../features/incidents/IncidentDialogs';
import type { CreateIncidentInput, TransitionIncidentInput } from '../../api/v3Incidents';
import './incidents.css';

const IncidentDialogs = lazy(() => import('../../features/incidents/IncidentDialogs'));
const HelpRequestsPanel = lazy(() => import('../../features/incidents/SupportPanels').then((module) => ({ default: module.HelpRequestsPanel })));
const SupportContactPanel = lazy(() => import('../../features/incidents/SupportPanels').then((module) => ({ default: module.SupportContactPanel })));
type WorkspaceTab = 'incidents' | 'help' | 'contact';

export default function IncidentsPage() {
  const { isAdmin, user } = useAuth();
  const [params, setParams] = useSearchParams();
  const requestedId = params.get('incident_id');
  const [tab, setTab] = useState<WorkspaceTab>('incidents');
  const [dialog, setDialog] = useState<IncidentDialog>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const userChangedIncidentQuery = useRef(false);
  const updateSelection = useCallback((id: string | null) => {
    setParams((current) => {
      const next = new URLSearchParams(current);
      if (id) next.set('incident_id', id); else next.delete('incident_id');
      return next;
    }, { replace: true });
  }, [setParams]);
  const workspace = useIncidentWorkspace({
    initialIncidentId: requestedId,
    onSelectionChange: updateSelection,
    helpActive: tab === 'help',
  });
  useEffect(() => {
    if (tab !== 'incidents' || workspace.incidentLoading) return;
    if (!userChangedIncidentQuery.current && requestedId && workspace.selectedIncidentId === requestedId) return;
    const selectionIsVisible = workspace.incidents.some((item) => item.incident_id === workspace.selectedIncidentId);
    if (selectionIsVisible) return;
    const firstIncident = workspace.incidents[0];
    if (firstIncident) void workspace.selectIncident(firstIncident.incident_id);
    else if (workspace.selectedIncidentId) void workspace.selectIncident(null);
  }, [tab, workspace.incidentLoading, workspace.incidents, workspace.selectIncident, workspace.selectedIncidentId]);
  const create = async (input: CreateIncidentInput) => {
    const result = await workspace.createIncident(input);
    setNotice(`已创建人工事件 ${result.incident_id}，状态为待处理。`);
  };
  const transition = async (action: Exclude<IncidentDialog, null | 'create'>, input: TransitionIncidentInput) => {
    const result = await workspace.transitionIncident(action, input);
    setNotice(`事件已更新为“${result.status}”，服务器版本为 v${result.incident_version}。`);
  };
  const incidentListError = workspace.incidentError ? incidentErrorMessage(workspace.incidentError) : null;
  const incidentDetailError = workspace.incidentDetailError ? incidentErrorMessage(workspace.incidentDetailError) : null;
  const helpError = workspace.helpError ? incidentErrorMessage(workspace.helpError) : null;
  const helpDetailError = workspace.helpDetailError ? incidentErrorMessage(workspace.helpDetailError) : null;
  const contactError = workspace.contactError ? incidentErrorMessage(workspace.contactError) : null;
  const selectedIncidentDetail = workspace.incidentDetail?.incident_id === workspace.selectedIncidentId
    ? workspace.incidentDetail
    : null;
  return <main className="incident-workspace">
    <header className="incident-page-header"><div><p>RECORDED SECURITY WORKFLOW</p><h1>事件与处置</h1></div><div className="incident-header-actions"><span>{isAdmin ? '管理员 · 完整处置' : '值守人员 · 事件处置'}</span><small>{user?.username}</small>{isAdmin && <button type="button" className="primary-button" onClick={() => setDialog('create')}>创建人工事件</button>}</div></header>
    <nav className="workspace-tabs" aria-label="事件工作区"><button type="button" className={tab === 'incidents' ? 'active' : ''} onClick={() => { if (tab !== 'incidents') void workspace.selectHelp(null); setTab('incidents'); }}>事件处置</button><button type="button" className={tab === 'help' ? 'active' : ''} onClick={() => { if (tab !== 'help') void workspace.selectIncident(null); setTab('help'); }}>用户求助</button><button type="button" className={tab === 'contact' ? 'active' : ''} onClick={() => { void workspace.selectHelp(null); void workspace.selectIncident(null); setTab('contact'); }}>APP 联系方式</button></nav>
    {notice && <div className="workspace-notice" role="status"><span>{notice}</span><button type="button" aria-label="关闭提示" onClick={() => setNotice(null)}>×</button></div>}
    {tab === 'incidents' && <>
      {(workspace.stale || workspace.realtime === 'disconnected') && <div className="workspace-stale" role="status">实时事件连接已断开或正在重同步；页面保留最后一次真实数据，当前内容可能过期。</div>}
      <div className="realtime-strip"><span className={`realtime-dot ${workspace.realtime}`} />实时状态：{workspace.realtime === 'connected' ? '已连接' : workspace.realtime === 'connecting' ? '连接中' : workspace.realtime === 'disconnected' ? '已断开' : '未启动'}</div>
      <div className="incident-main-grid">
        <IncidentListPanel filters={workspace.incidentFilters} onFilters={(filters) => { userChangedIncidentQuery.current = true; workspace.setIncidentFilters(filters); }} onClear={() => { userChangedIncidentQuery.current = true; workspace.clearIncidentFilters(); }} items={workspace.incidents} total={workspace.incidentTotal} page={workspace.incidentPage} pageSize={workspace.pageSize} loading={workspace.incidentLoading} error={incidentListError} selectedId={workspace.selectedIncidentId} onSelect={(id) => void workspace.selectIncident(id)} onPage={(page) => { userChangedIncidentQuery.current = true; workspace.setIncidentPage(page); }} onRefresh={() => void workspace.refreshIncidents()} lastUpdatedAt={workspace.incidentUpdatedAt} />
        <IncidentDetailPanel detail={selectedIncidentDetail} loading={workspace.incidentDetailLoading} error={incidentDetailError} pending={workspace.mutationPending} onAction={setDialog} />
      </div>
    </>}
    {tab === 'help' && <Suspense fallback={<div className="workspace-empty">正在加载用户求助工作区…</div>}><HelpRequestsPanel filters={workspace.helpFilters} onFilters={workspace.setHelpFilters} onClear={workspace.clearHelpFilters} items={workspace.helpItems} total={workspace.helpTotal} page={workspace.helpPage} pageSize={workspace.pageSize} loading={workspace.helpLoading} error={helpError} selectedId={workspace.selectedHelpId} detail={workspace.helpDetail} detailLoading={workspace.helpDetailLoading} detailError={helpDetailError} pending={workspace.mutationPending} onSelect={(id) => void workspace.selectHelp(id)} onPage={workspace.setHelpPage} onRefresh={() => void workspace.refreshHelp()} onUpdate={workspace.updateHelp} onReload={async () => { if (workspace.selectedHelpId) await workspace.selectHelp(workspace.selectedHelpId); }} /></Suspense>}
    {tab === 'contact' && <Suspense fallback={<div className="workspace-empty">正在加载公开联系人设置…</div>}><SupportContactPanel contact={workspace.contact} loading={workspace.contactLoading} error={contactError} isAdmin={isAdmin} pending={workspace.mutationPending} onLoad={workspace.loadContact} onUpdate={workspace.updateContact} /></Suspense>}
    <Suspense fallback={null}><IncidentDialogs dialog={dialog} detail={workspace.incidentDetail} pending={workspace.mutationPending} onClose={() => setDialog(null)} onCreate={create} onTransition={transition} onReload={async () => { if (workspace.selectedIncidentId) await workspace.selectIncident(workspace.selectedIncidentId); }} /></Suspense>
  </main>;
}
