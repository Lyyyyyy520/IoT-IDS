import { useEffect, useState, type FormEvent } from 'react';
import { Modal } from 'antd';
import {
  DeviceApiError,
  type CreateDeviceInput,
  type DeviceDetail,
  type DeviceImportance,
  type OperationMode,
  type UpdateDeviceInput,
} from '../../api/v3Devices';
import {
  AREA_PATTERN,
  DEVICE_ID_PATTERN,
  DEVICE_TYPE_PATTERN,
  MAC_PATTERN,
  MODE_LABELS,
  deviceErrorMessage,
} from './deviceUi';

export type DeviceDialog = 'create' | 'edit' | 'mode' | 'retire' | 'restore' | null;

interface Props {
  dialog: DeviceDialog;
  device: DeviceDetail | null;
  pending: boolean;
  onClose: () => void;
  onCreate: (input: CreateDeviceInput) => Promise<void>;
  onUpdate: (input: UpdateDeviceInput) => Promise<void>;
  onMode: (mode: OperationMode, expectedVersion: number) => Promise<void>;
  onRetire: (reason: string, expectedVersion: number) => Promise<void>;
  onRestore: (expectedVersion: number) => Promise<void>;
  onReloadLatest: () => Promise<void>;
}

function FormError({ message }: { message: string | null }) {
  return message ? <div className="devices-form-error" role="alert">{message}</div> : null;
}

function DialogActions({ pending, onClose, submitLabel }: {
  pending: boolean;
  onClose: () => void;
  submitLabel: string;
}) {
  return (
    <div className="device-dialog-actions">
      <button type="button" className="devices-button ghost" onClick={onClose} disabled={pending}>取消</button>
      <button type="submit" className="devices-button primary" disabled={pending}>
        {pending ? '提交中…' : submitLabel}
      </button>
    </div>
  );
}

function validateProfile(name: string, type: string, area: string): string | null {
  if (!name.trim() || name.trim().length > 100) return '设备名称长度必须为 1–100 个字符。';
  if (!DEVICE_TYPE_PATTERN.test(type.trim())) return '设备类型必须是小写字母、数字、下划线或连字符组成的标识符。';
  if (area.trim() && !AREA_PATTERN.test(area.trim())) return '区域 ID 只能包含字母、数字、下划线或连字符。';
  return null;
}

function CreateDialog({ open, pending, onClose, onCreate }: {
  open: boolean;
  pending: boolean;
  onClose: () => void;
  onCreate: Props['onCreate'];
}) {
  const [deviceId, setDeviceId] = useState('');
  const [mac, setMac] = useState('');
  const [displayName, setDisplayName] = useState('');
  const [deviceType, setDeviceType] = useState('');
  const [areaId, setAreaId] = useState('');
  const [importance, setImportance] = useState<DeviceImportance>('normal');
  const [profileSource, setProfileSource] = useState<CreateDeviceInput['profile_source']>('physical');
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setDeviceId('');
    setMac('');
    setDisplayName('');
    setDeviceType('');
    setAreaId('');
    setImportance('normal');
    setProfileSource('physical');
    setError(null);
  }, [open]);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const profileError = validateProfile(displayName, deviceType, areaId);
    if (!DEVICE_ID_PATTERN.test(deviceId.trim())) {
      setError('device_id 必须是 2–64 位小写字母、数字、下划线或连字符，并以字母或数字开头。');
      return;
    }
    if (!MAC_PATTERN.test(mac.trim())) {
      setError('MAC 必须是 12 位十六进制，或使用一致的冒号/连字符分隔。');
      return;
    }
    if (profileError) {
      setError(profileError);
      return;
    }
    setError(null);
    try {
      await onCreate({
        device_id: deviceId.trim(),
        mac: mac.trim(),
        display_name: displayName.trim(),
        device_type: deviceType.trim(),
        area_id: areaId.trim() || null,
        importance,
        profile_source: profileSource,
      });
      onClose();
    } catch (caught) {
      setError(deviceErrorMessage(caught));
    }
  };

  return (
    <Modal title="新增稳定设备档案" open={open} onCancel={onClose} footer={null} destroyOnHidden>
      <form className="device-dialog-form" onSubmit={(event) => void submit(event)}>
        <p className="dialog-intro">设备创建后保持 unknown，只有真实心跳才能使其在线。此表单不接受 IP。</p>
        <label>
          <span>device_id</span>
          <input autoFocus value={deviceId} onChange={(event) => setDeviceId(event.target.value)} maxLength={64} required />
          <small>创建后不可修改，用于主题和历史证据关联。</small>
        </label>
        <label>
          <span>MAC</span>
          <input value={mac} onChange={(event) => setMac(event.target.value)} placeholder="AA:BB:CC:DD:EE:01" required />
        </label>
        <label>
          <span>显示名称</span>
          <input value={displayName} onChange={(event) => setDisplayName(event.target.value)} maxLength={100} required />
        </label>
        <label>
          <span>设备类型</span>
          <input value={deviceType} onChange={(event) => setDeviceType(event.target.value)} placeholder="camera" maxLength={64} required />
        </label>
        <label>
          <span>区域 ID（可选）</span>
          <input value={areaId} onChange={(event) => setAreaId(event.target.value)} maxLength={64} />
        </label>
        <label>
          <span>重要性</span>
          <select value={importance} onChange={(event) => setImportance(event.target.value as DeviceImportance)}>
            <option value="low">低</option><option value="normal">普通</option>
            <option value="high">重要</option><option value="critical">关键</option>
          </select>
        </label>
        <label>
          <span>档案来源</span>
          <select value={profileSource} onChange={(event) => setProfileSource(event.target.value as CreateDeviceInput['profile_source'])}>
            <option value="physical">物理设备</option>
            <option value="virtual">虚拟设备</option>
            <option value="gateway">网关</option>
          </select>
        </label>
        <FormError message={error} />
        <DialogActions pending={pending} onClose={onClose} submitLabel="创建设备档案" />
      </form>
    </Modal>
  );
}

function EditDialog({ open, device, pending, onClose, onUpdate, onReloadLatest }: {
  open: boolean;
  device: DeviceDetail | null;
  pending: boolean;
  onClose: () => void;
  onUpdate: Props['onUpdate'];
  onReloadLatest: Props['onReloadLatest'];
}) {
  const [displayName, setDisplayName] = useState('');
  const [deviceType, setDeviceType] = useState('');
  const [areaId, setAreaId] = useState('');
  const [importance, setImportance] = useState<DeviceImportance>('normal');
  const [error, setError] = useState<string | null>(null);
  const [versionConflict, setVersionConflict] = useState(false);

  useEffect(() => {
    if (!open || !device) return;
    setDisplayName(device.display_name);
    setDeviceType(device.device_type);
    setAreaId(device.area_id ?? '');
    setImportance(device.importance);
    setError(null);
    setVersionConflict(false);
  }, [device?.device_id, open]);

  if (!device) return null;
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const profileError = validateProfile(displayName, deviceType, areaId);
    if (profileError) {
      setError(profileError);
      return;
    }
    setError(null);
    try {
      await onUpdate({
        display_name: displayName.trim(),
        device_type: deviceType.trim(),
        area_id: areaId.trim() || null,
        importance,
        expected_profile_version: device.profile_version,
      });
      onClose();
    } catch (caught) {
      setError(deviceErrorMessage(caught));
      setVersionConflict(caught instanceof DeviceApiError && caught.code === 'profile_version_conflict');
    }
  };

  const reloadLatest = async () => {
    await onReloadLatest();
    onClose();
  };

  return (
    <Modal title="编辑设备档案" open={open} onCancel={onClose} footer={null} destroyOnHidden>
      <form className="device-dialog-form" onSubmit={(event) => void submit(event)}>
        <div className="immutable-summary">不可修改：{device.device_id} · {device.mac_address}</div>
        <label><span>显示名称</span><input autoFocus value={displayName} onChange={(event) => setDisplayName(event.target.value)} maxLength={100} required /></label>
        <label><span>设备类型</span><input value={deviceType} onChange={(event) => setDeviceType(event.target.value)} maxLength={64} required /></label>
        <label><span>区域 ID（可选）</span><input value={areaId} onChange={(event) => setAreaId(event.target.value)} maxLength={64} /></label>
        <label>
          <span>重要性</span>
          <select value={importance} onChange={(event) => setImportance(event.target.value as DeviceImportance)}>
            <option value="low">低</option><option value="normal">普通</option>
            <option value="high">重要</option><option value="critical">关键</option>
          </select>
        </label>
        <small>提交使用当前 profile_version {device.profile_version}；冲突时不会自动覆盖。</small>
        <FormError message={error} />
        {versionConflict ? (
          <div className="version-conflict-actions">
            <button type="button" className="devices-button" onClick={() => void reloadLatest()}>重新加载最新数据</button>
            <button type="button" className="devices-button ghost" onClick={onClose}>取消并保留到关闭前</button>
          </div>
        ) : <DialogActions pending={pending} onClose={onClose} submitLabel="保存档案" />}
      </form>
    </Modal>
  );
}

function ModeDialog({ open, device, pending, onClose, onMode }: {
  open: boolean;
  device: DeviceDetail | null;
  pending: boolean;
  onClose: () => void;
  onMode: Props['onMode'];
}) {
  const [mode, setMode] = useState<OperationMode>('active');
  const [error, setError] = useState<string | null>(null);
  useEffect(() => { if (open && device) { setMode(device.operation_mode); setError(null); } }, [device, open]);
  if (!device) return null;
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (mode === device.operation_mode) { setError('请选择不同的运行模式。'); return; }
    try { await onMode(mode, device.profile_version); onClose(); }
    catch (caught) { setError(deviceErrorMessage(caught)); }
  };
  return (
    <Modal title="修改运行模式" open={open} onCancel={onClose} footer={null} destroyOnHidden>
      <form className="device-dialog-form" onSubmit={(event) => void submit(event)}>
        <label><span>运行模式</span><select autoFocus value={mode} onChange={(event) => setMode(event.target.value as OperationMode)}>
          <option value="active">active · 正常参与告警</option>
          <option value="maintenance">maintenance · 维护语义</option>
          <option value="disabled">disabled · 停用运行语义</option>
        </select></label>
        <div className="mode-impact">
          将模式改为“{MODE_LABELS[mode]}”不会改变真实连接状态，也不会把设备伪装成离线。
        </div>
        <FormError message={error} />
        <DialogActions pending={pending} onClose={onClose} submitLabel="确认修改模式" />
      </form>
    </Modal>
  );
}

function RetireDialog({ open, device, pending, onClose, onRetire }: {
  open: boolean;
  device: DeviceDetail | null;
  pending: boolean;
  onClose: () => void;
  onRetire: Props['onRetire'];
}) {
  const [reason, setReason] = useState('');
  const [confirmed, setConfirmed] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => { if (open) { setReason(''); setConfirmed(false); setError(null); } }, [open]);
  if (!device) return null;
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!reason.trim() || reason.trim().length > 500) { setError('请输入 1–500 个字符的退役原因。'); return; }
    if (!confirmed) { setError('请完成第二次确认。'); return; }
    try { await onRetire(reason.trim(), device.profile_version); onClose(); }
    catch (caught) { setError(deviceErrorMessage(caught)); }
  };
  return (
    <Modal title="退役设备" open={open} onCancel={onClose} footer={null} destroyOnHidden>
      <form className="device-dialog-form" onSubmit={(event) => void submit(event)}>
        <div className="lifecycle-explanation">
          退役会把运行模式设为 disabled，但保留档案、观测和历史证据。系统目前不会自动吊销 MQTT 凭据。
        </div>
        <label><span>退役原因</span><textarea autoFocus value={reason} onChange={(event) => setReason(event.target.value)} maxLength={500} required /></label>
        <label className="confirm-check"><input type="checkbox" checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} /><span>我确认退役该设备并保留全部历史记录</span></label>
        <FormError message={error} />
        <DialogActions pending={pending} onClose={onClose} submitLabel="确认退役" />
      </form>
    </Modal>
  );
}

function RestoreDialog({ open, device, pending, onClose, onRestore }: {
  open: boolean;
  device: DeviceDetail | null;
  pending: boolean;
  onClose: () => void;
  onRestore: Props['onRestore'];
}) {
  const [error, setError] = useState<string | null>(null);
  useEffect(() => { if (open) setError(null); }, [open]);
  if (!device) return null;
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    try { await onRestore(device.profile_version); onClose(); }
    catch (caught) { setError(deviceErrorMessage(caught)); }
  };
  return (
    <Modal title="恢复退役设备" open={open} onCancel={onClose} footer={null} destroyOnHidden>
      <form className="device-dialog-form" onSubmit={(event) => void submit(event)}>
        <div className="lifecycle-explanation">
          恢复会清除退役状态并设为 active，但不会把设备自动设置为 online；重新接入前需要核验 MQTT 凭据。
        </div>
        <FormError message={error} />
        <DialogActions pending={pending} onClose={onClose} submitLabel="确认恢复" />
      </form>
    </Modal>
  );
}

export default function DeviceDialogs(props: Props) {
  return (
    <>
      <CreateDialog open={props.dialog === 'create'} pending={props.pending} onClose={props.onClose} onCreate={props.onCreate} />
      <EditDialog open={props.dialog === 'edit'} device={props.device} pending={props.pending} onClose={props.onClose} onUpdate={props.onUpdate} onReloadLatest={props.onReloadLatest} />
      <ModeDialog open={props.dialog === 'mode'} device={props.device} pending={props.pending} onClose={props.onClose} onMode={props.onMode} />
      <RetireDialog open={props.dialog === 'retire'} device={props.device} pending={props.pending} onClose={props.onClose} onRetire={props.onRetire} />
      <RestoreDialog open={props.dialog === 'restore'} device={props.device} pending={props.pending} onClose={props.onClose} onRestore={props.onRestore} />
    </>
  );
}
