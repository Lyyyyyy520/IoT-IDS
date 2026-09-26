import { useEffect, useState, type ReactNode } from 'react';
import { DeviceApiError, type DeviceDetail } from '../../api/v3Devices';
import {
  CONNECTION_LABELS,
  IMPORTANCE_LABELS,
  MODE_LABELS,
  SOURCE_LABELS,
  deviceErrorMessage,
  formatDeviceTime,
} from './deviceUi';

const REFERENCE_LABELS: Record<string, string> = {
  state_observations: '状态观测',
  mqtt_boot_sessions: 'MQTT 启动会话',
  mqtt_cursor: 'MQTT 重放游标',
  non_management_events: '历史实时事件',
  management_audits: '管理审计',
  current_state_placeholder: '当前状态占位',
};

function blockingReason(reason: string): string {
  if (reason.startsWith('future_reference:')) {
    return `未来证据表：${reason.slice('future_reference:'.length)}`;
  }
  if (reason === 'foreign_key_reference') return '存在未分类的数据库外键引用';
  return REFERENCE_LABELS[reason] ?? reason;
}

interface Props {
  device: DeviceDetail | null;
  loading: boolean;
  error: string | null;
  isAdmin: boolean;
  mutationPending: boolean;
  onEdit: () => void;
  onMode: () => void;
  onRetire: () => void;
  onRestore: () => void;
  onDelete: (confirmation: string) => Promise<void>;
  onHistoryConflict: () => void;
  activeTab: 'overview' | 'traffic' | 'manage';
  onTabChange: (tab: 'overview' | 'traffic' | 'manage') => void;
  trafficContent: ReactNode;
}

export default function DeviceDetailPanel({
  device,
  loading,
  error,
  isAdmin,
  mutationPending,
  onEdit,
  onMode,
  onRetire,
  onRestore,
  onDelete,
  onHistoryConflict,
  activeTab,
  onTabChange,
  trafficContent,
}: Props) {
  const [deleteConfirmation, setDeleteConfirmation] = useState('');
  const [deleteError, setDeleteError] = useState<string | null>(null);

  useEffect(() => {
    setDeleteConfirmation('');
    setDeleteError(null);
  }, [device?.device_id, device?.profile_version]);

  if (loading && !device) {
    return <section className="devices-panel device-detail-panel devices-empty">正在读取设备详情…</section>;
  }
  if (error && !device) {
    return <section className="devices-panel device-detail-panel devices-empty error" role="alert">{error}</section>;
  }
  if (!device) {
    return (
      <section className="devices-panel device-detail-panel devices-empty">
        <strong>请选择设备</strong>
        <span>从左侧真实设备档案中选择一项查看详情。</span>
      </section>
    );
  }

  const deleteAllowed = device.can_delete
    && deleteConfirmation === device.display_name
    && !mutationPending;
  const references = Object.entries(device.references)
    .filter(([key]) => key !== 'future_references') as [keyof Omit<DeviceDetail['references'], 'future_references'>, number][];

  const handleDelete = async () => {
    if (!deleteAllowed) return;
    setDeleteError(null);
    try {
      await onDelete(deleteConfirmation);
    } catch (caught) {
      setDeleteError(deviceErrorMessage(caught));
      if (caught instanceof DeviceApiError && caught.code === 'device_has_history') {
        onHistoryConflict();
      }
    }
  };

  return (
    <section className="devices-panel device-detail-panel" aria-labelledby="device-detail-title">
      <div className="devices-panel-heading detail-heading">
        <div>
          <p className="devices-eyebrow">STABLE DEVICE PROFILE</p>
          <h2 id="device-detail-title">{device.display_name}</h2>
          <span className="detail-device-id">{device.device_id}</span>
        </div>
        <div className="detail-status-stack">
          <span className={`status-pill status-${device.connection_status}`}>
            连接：{CONNECTION_LABELS[device.connection_status]}
          </span>
          <span className={`mode-pill mode-${device.operation_mode}`}>
            模式：{MODE_LABELS[device.operation_mode]}
          </span>
          {device.lifecycle_status === 'retired' && <span className="retired-pill">已退役</span>}
        </div>
      </div>

      {loading && <div className="detail-refreshing" role="status">正在刷新服务器详情…</div>}
      {error && <div className="devices-inline-error" role="alert">{error}</div>}
      {device.credential_revocation_required && (
        <div className="credential-warning" role="status">
          设备已退役，但 MQTT 凭据尚未自动吊销；需要管理员在 Broker 侧人工处理。
        </div>
      )}
      {device.credential_reverification_required && (
        <div className="credential-warning" role="status">
          设备已恢复，但不会自动在线；重新接入前需核验 MQTT 凭据。
        </div>
      )}

      <div className="device-detail-tabs" role="tablist" aria-label="设备详情视图">
        <button type="button" role="tab" aria-selected={activeTab === 'overview'} onClick={() => onTabChange('overview')}>概览</button>
        <button type="button" role="tab" aria-selected={activeTab === 'traffic'} onClick={() => onTabChange('traffic')}>流量</button>
        {isAdmin && (
          <button type="button" role="tab" aria-selected={activeTab === 'manage'} onClick={() => onTabChange('manage')}>管理</button>
        )}
      </div>

      {activeTab === 'traffic' ? (
        <div className="device-detail-scroll" role="tabpanel" aria-label="设备流量">{trafficContent}</div>
      ) : (
      <div className="device-detail-scroll" role="tabpanel" aria-label={activeTab === 'manage' ? '设备管理' : '设备概览'}>
        <div className="detail-section">
          <div className="detail-section-title">
            <h3>档案与身份</h3>
            {isAdmin && activeTab === 'manage' ? (
              <div className="detail-actions" aria-label="管理员设备操作">
                <button type="button" className="devices-button" onClick={onEdit} disabled={mutationPending}>编辑档案</button>
                <button type="button" className="devices-button" onClick={onMode} disabled={mutationPending || device.lifecycle_status === 'retired'}>修改运行模式</button>
                {device.lifecycle_status === 'retired' ? (
                  <button type="button" className="devices-button warning" onClick={onRestore} disabled={mutationPending}>恢复设备</button>
                ) : (
                  <button type="button" className="devices-button warning" onClick={onRetire} disabled={mutationPending}>退役设备</button>
                )}
              </div>
            ) : !isAdmin ? (
              <span className="read-only-badge">值守人员只读</span>
            ) : null}
          </div>
          <dl className="device-detail-grid">
            <div><dt>显示名称</dt><dd>{device.display_name}</dd></div>
            <div><dt>设备类型</dt><dd>{device.device_type}</dd></div>
            <div><dt>稳定 device_id</dt><dd>{device.device_id}</dd></div>
            <div><dt>绑定 MAC</dt><dd>{device.mac_address}</dd></div>
            <div><dt>区域</dt><dd>{device.area_id ?? '未分区'}</dd></div>
            <div><dt>重要性</dt><dd>{IMPORTANCE_LABELS[device.importance]}</dd></div>
            <div><dt>档案来源</dt><dd>{SOURCE_LABELS[device.profile_source]}</dd></div>
            <div><dt>档案版本</dt><dd>{device.profile_version}</dd></div>
            <div><dt>创建时间</dt><dd>{formatDeviceTime(device.created_at)}</dd></div>
            <div><dt>档案更新时间</dt><dd>{formatDeviceTime(device.updated_at)}</dd></div>
          </dl>
        </div>

        <div className="detail-section">
          <div className="detail-section-title"><h3>实时状态</h3></div>
          <p className="mode-separation-note">
            连接状态来自后端真实心跳；运行模式只影响运行和告警语义，maintenance/disabled 不等于离线。
          </p>
          <dl className="device-detail-grid">
            <div><dt>连接状态</dt><dd className={`status-${device.connection_status}`}>{CONNECTION_LABELS[device.connection_status]}</dd></div>
            <div><dt>运行模式</dt><dd>{MODE_LABELS[device.operation_mode]}</dd></div>
            <div><dt>当前 IP</dt><dd>{device.ip_address ?? '尚无 IP'}</dd></div>
            <div><dt>状态版本</dt><dd>{device.state_version}</dd></div>
            <div><dt>设备观测时间</dt><dd>{formatDeviceTime(device.observed_at)}</dd></div>
            <div><dt>后端接收时间</dt><dd>{formatDeviceTime(device.received_at)}</dd></div>
            <div className="detail-wide"><dt>观测来源</dt><dd>{device.sources.length ? device.sources.join('、') : '尚无来源'}</dd></div>
          </dl>
        </div>

        <div className="detail-section">
          <div className="detail-section-title"><h3>生命周期与历史引用</h3></div>
          <dl className="device-detail-grid">
            <div><dt>生命周期</dt><dd>{device.lifecycle_status === 'retired' ? '已退役' : '使用中'}</dd></div>
            <div><dt>退役时间</dt><dd>{formatDeviceTime(device.retired_at)}</dd></div>
            <div className="detail-wide"><dt>退役原因</dt><dd>{device.retirement_reason ?? '无'}</dd></div>
          </dl>
          <div className="reference-grid">
            {references.map(([key, count]) => (
              <div key={key}><span>{REFERENCE_LABELS[key]}</span><strong>{count}</strong></div>
            ))}
            {Object.entries(device.references.future_references).map(([table, count]) => (
              <div key={table}><span>未来引用：{table}</span><strong>{count}</strong></div>
            ))}
          </div>
        </div>

        {isAdmin && activeTab === 'manage' && (
          <div className="danger-zone" aria-labelledby="danger-zone-title">
            <h3 id="danger-zone-title">危险操作：彻底删除误添加设备</h3>
            <p>该操作不可恢复。只有没有历史证据的误添加设备才能删除，管理审计会继续保留。</p>
            {!device.can_delete && (
              <div className="delete-blockers">
                <strong>当前不可删除：</strong>
                <ul>
                  {device.delete_blocking_reasons.map((reason) => <li key={reason}>{blockingReason(reason)}</li>)}
                </ul>
              </div>
            )}
            <label>
              <span>手工输入完整设备名称“{device.display_name}”确认</span>
              <input
                value={deleteConfirmation}
                onChange={(event) => setDeleteConfirmation(event.target.value)}
                autoComplete="off"
                disabled={!device.can_delete || mutationPending}
              />
            </label>
            {deleteError && <div className="devices-inline-error" role="alert">{deleteError}</div>}
            <button
              type="button"
              className="devices-button danger"
              disabled={!deleteAllowed}
              onClick={() => void handleDelete()}
            >
              {mutationPending ? '正在提交…' : '不可恢复地删除设备'}
            </button>
          </div>
        )}
      </div>
      )}
    </section>
  );
}
