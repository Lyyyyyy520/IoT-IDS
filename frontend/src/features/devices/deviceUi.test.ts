import { describe, expect, it } from 'vitest';
import { DeviceApiError } from '../../api/v3Devices';
import { deviceErrorMessage } from './deviceUi';

describe('deviceErrorMessage', () => {
  it.each([
    ['device_id_conflict', 'device_id 已存在'],
    ['device_identity_conflict', 'MAC 已绑定其他设备'],
    ['profile_version_conflict', '其他操作修改'],
    ['device_has_history', '已有历史证据'],
    ['confirmation_mismatch', '名称不完全一致'],
  ])('renders stable conflict code %s without exposing raw exceptions', (code, expected) => {
    const message = deviceErrorMessage(new DeviceApiError('conflict', 'internal stack detail', {
      status: 409,
      code,
      requestId: 'req-conflict',
    }));
    expect(message).toContain(expected);
    expect(message).toContain('req-conflict');
    expect(message).not.toContain('internal stack detail');
  });

  it.each([
    ['bad_request', 400, '输入内容不符合接口要求'],
    ['unauthorized', 401, '登录状态已失效'],
    ['forbidden', 403, '没有执行此操作的权限'],
    ['not_found', 404, '设备不存在或已被删除'],
    ['conflict', 409, '当前设备状态冲突'],
    ['too_large', 413, '提交内容超过'],
    ['unavailable', 503, '数据库或管理服务尚未准备'],
    ['network', null, '无法连接设备管理服务'],
    ['invalid_response', null, '响应格式不符合'],
  ] as const)('renders safe %s feedback for status %s', (kind, status, expected) => {
    expect(deviceErrorMessage(new DeviceApiError(kind, 'private failure', {
      status: status ?? undefined,
    }))).toContain(expected);
  });
});
