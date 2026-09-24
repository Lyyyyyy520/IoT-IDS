import { lazy, Suspense, useCallback, useEffect, useRef, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { useAuth } from '../../contexts/AuthContext';
import DeviceDetailPanel from '../../features/devices/DeviceDetailPanel';
import DeviceDialogs, { type DeviceDialog } from '../../features/devices/DeviceDialogs';
import DeviceListPanel from '../../features/devices/DeviceListPanel';
import DiscoveryPanel from '../../features/devices/DiscoveryPanel';
import { deviceErrorMessage } from '../../features/devices/deviceUi';
import { useDiscoveryWorkspace } from '../../features/devices/useDiscoveryWorkspace';
import { useDeviceWorkspace } from '../../features/devices/useDeviceWorkspace';
import { useMonitorStore } from '../../features/monitor/monitorStore';
import type {
  CreateDeviceInput,
  OperationMode,
  UpdateDeviceInput,
} from '../../api/v3Devices';
import './devices.css';

const DeviceTrafficPanel = lazy(() => import('../../features/traffic/DeviceTrafficPanel'));
type DetailTab = 'overview' | 'traffic' | 'manage';

export default function DevicesPage() {
  const { isAdmin, user } = useAuth();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const requestedDeviceId = searchParams.get('device_id');
  const [dialog, setDialog] = useState<DeviceDialog>(null);
  const [detailTab, setDetailTab] = useState<DetailTab>('traffic');
  const [workspaceTab, setWorkspaceTab] = useState<'trusted' | 'discovery'>('trusted');
  const [notice, setNotice] = useState<{ kind: 'success' | 'warning'; message: string } | null>(null);
  const lastDiscoveryEventId = useRef<number | null>(null);

  const updateQuerySelection = useCallback((deviceId: string | null) => {
    setSearchParams((current) => {
      const next = new URLSearchParams(current);
      if (deviceId) next.set('device_id', deviceId);
      else next.delete('device_id');
      return next;
    }, { replace: true });
  }, [setSearchParams]);

  const clearInvalidSelection = useCallback(() => {
    setSearchParams((current) => {
      const next = new URLSearchParams(current);
      next.delete('device_id');
      return next;
    }, { replace: true });
  }, [setSearchParams]);

  const workspace = useDeviceWorkspace({
    initialDeviceId: requestedDeviceId,
    onSelectionChange: updateQuerySelection,
    onInvalidSelection: clearInvalidSelection,
  });
  const discovery = useDiscoveryWorkspace({ active: workspaceTab === 'discovery' });
  const monitor = useMonitorStore({ enabled: workspaceTab === 'discovery' });

  useEffect(() => {
    const event = monitor.recentEvents[0];
    if (workspaceTab !== 'discovery' || event?.event_type !== 'device.discovered'
      || lastDiscoveryEventId.current === event.event_id) return;
    lastDiscoveryEventId.current = event.event_id;
    const timer = window.setTimeout(() => { void discovery.refreshList(); }, 350);
    return () => window.clearTimeout(timer);
  }, [discovery.refreshList, monitor.recentEvents, workspaceTab]);

  useEffect(() => {
    if (workspace.detail?.device_id) setDetailTab('traffic');
  }, [workspace.detail?.device_id]);

  useEffect(() => {
    if (!isAdmin && detailTab === 'manage') setDetailTab('traffic');
  }, [detailTab, isAdmin]);

  const createDevice = async (input: CreateDeviceInput) => {
    workspace.clearFilters();
    const result = await workspace.createDevice(input);
    setNotice({ kind: 'success', message: `已创建 ${result.display_name}；当前真实连接状态为未知。` });
  };

  const updateDevice = async (input: UpdateDeviceInput) => {
    if (!workspace.detail) return;
    const result = await workspace.updateDevice(workspace.detail.device_id, input);
    setNotice({ kind: 'success', message: `已保存 ${result.display_name} 的服务器档案。` });
  };

  const updateMode = async (mode: OperationMode, expectedVersion: number) => {
    if (!workspace.detail) return;
    await workspace.setOperationMode(workspace.detail.device_id, mode, expectedVersion);
    setNotice({ kind: 'success', message: '运行模式已更新；真实连接状态未被修改。' });
  };

  const retireDevice = async (reason: string, expectedVersion: number) => {
    if (!workspace.detail) return;
    const result = await workspace.retireDevice(workspace.detail.device_id, reason, expectedVersion);
    setNotice({
      kind: 'warning',
      message: result.credential_revocation_required
        ? '设备已退役并设为 disabled；MQTT 凭据仍需人工吊销。'
        : '设备已退役；请核对凭据处理状态。',
    });
  };

  const restoreDevice = async (expectedVersion: number) => {
    if (!workspace.detail) return;
    const result = await workspace.restoreDevice(workspace.detail.device_id, expectedVersion);
    setNotice({
      kind: 'warning',
      message: result.credential_reverification_required
        ? `设备已恢复为 active，当前连接仍为 ${result.connection_status}；MQTT 凭据需要重新核验。`
        : `设备已恢复，当前连接状态仍为 ${result.connection_status}。`,
    });
  };

  const deleteDevice = async (confirmation: string) => {
    if (!workspace.detail) return;
    const name = workspace.detail.display_name;
    await workspace.deleteDevice(workspace.detail.device_id, confirmation);
    setNotice({ kind: 'success', message: `误添加设备“${name}”已彻底删除，管理审计仍保留。` });
  };

  const listError = workspace.listError ? deviceErrorMessage(workspace.listError) : null;
  const detailError = workspace.detailError ? deviceErrorMessage(workspace.detailError) : null;

  return (
    <main className="devices-workspace">
      <header className="devices-page-header">
        <div>
          <p className="devices-eyebrow">ADMINISTRATIVE DEVICE CONTROL</p>
          <h1>v3 设备管理工作区</h1>
          <p>稳定身份、真实连接状态、生命周期与历史证据分离管理</p>
        </div>
        <div className="devices-page-actions">
          <span className="workspace-role">{isAdmin ? '管理员 · 可写' : '值守人员 · 只读'}</span>
          <span className="workspace-user">{user?.username}</span>
          {isAdmin && workspaceTab === 'trusted' && (
            <button type="button" className="devices-button primary" onClick={() => setDialog('create')}>
              新增设备档案
            </button>
          )}
        </div>
      </header>

      {notice && (
        <div className={`devices-notice ${notice.kind}`} role="status">
          <span>{notice.message}</span>
          <button type="button" onClick={() => setNotice(null)} aria-label="关闭提示">×</button>
        </div>
      )}
      {workspace.selectionNotice && (
        <div className="devices-notice warning" role="status">{workspace.selectionNotice}</div>
      )}

      <nav className="devices-workspace-tabs" aria-label="设备工作区">
        <button type="button" aria-current={workspaceTab === 'trusted' ? 'page' : undefined}
          className={workspaceTab === 'trusted' ? 'active' : ''} onClick={() => setWorkspaceTab('trusted')}>可信设备</button>
        <button type="button" aria-current={workspaceTab === 'discovery' ? 'page' : undefined}
          className={workspaceTab === 'discovery' ? 'active' : ''} onClick={() => setWorkspaceTab('discovery')}>待确认设备</button>
      </nav>

      {workspaceTab === 'discovery' ? (
        <DiscoveryPanel workspace={discovery} isAdmin={isAdmin} onClaimed={(deviceId, message) => {
          setNotice({ kind: 'warning', message: `已认领并建立 unknown 档案。${message}` });
          setWorkspaceTab('trusted');
          updateQuerySelection(deviceId);
          void workspace.selectDevice(deviceId);
          navigate(`/devices?device_id=${encodeURIComponent(deviceId)}`);
        }} />
      ) : <div className="devices-grid">
        <DeviceListPanel
          filters={workspace.filters}
          onFilters={workspace.patchFilters}
          onClearFilters={workspace.clearFilters}
          items={workspace.items}
          total={workspace.total}
          selectedId={workspace.selectedId}
          onSelect={(deviceId) => {
            setDetailTab('traffic');
            void workspace.selectDevice(deviceId);
          }}
          loading={workspace.listLoading}
          loadingMore={workspace.loadingMore}
          hasMore={workspace.hasMore}
          onLoadMore={() => void workspace.loadMore()}
          onRefresh={() => void workspace.refreshList()}
          lastUpdatedAt={workspace.lastUpdatedAt}
          error={listError}
        />
        <DeviceDetailPanel
          device={workspace.detail}
          loading={workspace.detailLoading}
          error={detailError}
          isAdmin={isAdmin}
          mutationPending={workspace.mutationPending}
          onEdit={() => setDialog('edit')}
          onMode={() => setDialog('mode')}
          onRetire={() => setDialog('retire')}
          onRestore={() => setDialog('restore')}
          onDelete={deleteDevice}
          onHistoryConflict={() => void workspace.refreshDetail()}
          activeTab={detailTab}
          onTabChange={setDetailTab}
          trafficContent={workspace.detail ? (
            <Suspense fallback={<div className="traffic-loading">正在加载流量分析模块…</div>}>
              <DeviceTrafficPanel
                device={workspace.detail}
                isAdmin={isAdmin}
                active={detailTab === 'traffic'}
                onSelectPeer={(deviceId) => {
                  setDetailTab('traffic');
                  void workspace.selectDevice(deviceId);
                }}
              />
            </Suspense>
          ) : null}
        />
      </div>}

      {isAdmin && (
        <DeviceDialogs
          dialog={dialog}
          device={workspace.detail}
          pending={workspace.mutationPending}
          onClose={() => setDialog(null)}
          onCreate={createDevice}
          onUpdate={updateDevice}
          onMode={updateMode}
          onRetire={retireDevice}
          onRestore={restoreDevice}
          onReloadLatest={workspace.refreshDetail}
        />
      )}
    </main>
  );
}
