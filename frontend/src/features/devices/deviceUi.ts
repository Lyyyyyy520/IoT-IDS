import {
  DeviceApiError,
  type ConnectionStatus,
  type DeviceImportance,
  type DeviceProfileSource,
  type OperationMode,
} from '../../api/v3Devices';

export const CONNECTION_LABELS: Record<ConnectionStatus, string> = {
  online: '在线',
  stale: '延迟',
  offline: '离线',
  unknown: '未知',
};

export const MODE_LABELS: Record<OperationMode, string> = {
  active: '运行中',
  maintenance: '维护中',
  disabled: '已停用',
};

export const IMPORTANCE_LABELS: Record<DeviceImportance, string> = {
  low: '低',
  normal: '普通',
  high: '重要',
  critical: '关键',
};

export const SOURCE_LABELS: Record<DeviceProfileSource, string> = {
  unclassified: '未分类',
  physical: '物理设备',
  virtual: '虚拟设备',
  gateway: '网关',
};

const CODE_MESSAGES: Record<string, string> = {
  device_id_conflict: '该 device_id 已存在，请使用另一个稳定 ID。',
  device_identity_conflict: '该 MAC 已绑定其他设备，请核对物理身份。',
  profile_version_conflict: '数据已被其他操作修改，当前内容尚未覆盖服务器数据。',
  device_has_history: '设备已有历史证据，不能彻底删除。',
  confirmation_mismatch: '确认名称与当前设备名称不完全一致。',
  device_lifecycle_conflict: '当前生命周期状态不允许执行此操作。',
  csrf_token_missing: '缺少安全令牌，写操作未执行。',
  csrf_token_invalid: '安全令牌已失效，请刷新页面后重试。',
  invalid_device_request: '输入内容不符合设备档案规则。',
};

const KIND_MESSAGES: Record<DeviceApiError['kind'], string> = {
  bad_request: '输入内容不符合接口要求。',
  unauthorized: '登录状态已失效，请重新登录。',
  forbidden: '当前账号没有执行此操作的权限。',
  not_found: '设备不存在或已被删除。',
  conflict: '操作与当前设备状态冲突。',
  too_large: '提交内容超过后端允许的大小。',
  unavailable: '设备数据库或管理服务尚未准备。',
  network: '无法连接设备管理服务。',
  invalid_response: '后端响应格式不符合设备 API 契约。',
  http: '设备管理请求失败。',
  aborted: '设备请求已取消。',
};

export function deviceErrorMessage(error: unknown): string {
  if (!(error instanceof DeviceApiError)) return '设备操作失败，请稍后重试。';
  const base = error.code ? CODE_MESSAGES[error.code] : null;
  const message = base ?? KIND_MESSAGES[error.kind];
  return error.requestId ? `${message}（请求 ${error.requestId}）` : message;
}

export function formatDeviceTime(value: string | null): string {
  if (!value) return '尚无记录';
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  }).format(new Date(value));
}

export const DEVICE_ID_PATTERN = /^[a-z0-9][a-z0-9_-]{1,63}$/;
export const DEVICE_TYPE_PATTERN = /^[a-z0-9][a-z0-9_-]{0,63}$/;
export const AREA_PATTERN = /^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/;
export const MAC_PATTERN = /^(?:[0-9a-fA-F]{12}|[0-9a-fA-F]{2}(?::[0-9a-fA-F]{2}){5}|[0-9a-fA-F]{2}(?:-[0-9a-fA-F]{2}){5})$/;
