import type { DeviceDetail, DeviceListItem, DeviceListResponse } from '../api/v3Devices';

export const deviceDetail: DeviceDetail = {
  device_id: 'camera-01',
  mac_address: 'AA:BB:CC:DD:EE:01',
  display_name: '东门摄像头',
  device_type: 'camera',
  area_id: 'east-gate',
  importance: 'high',
  profile_source: 'physical',
  profile_version: 3,
  operation_mode: 'active',
  retired_at: null,
  retirement_reason: null,
  created_at: '2026-09-20T01:00:00Z',
  updated_at: '2026-09-20T02:00:00Z',
  connection_status: 'online',
  ip_address: '192.168.4.21',
  state_version: 5,
  observed_at: '2026-09-20T02:00:00Z',
  received_at: '2026-09-20T02:00:00Z',
  sources: ['mqtt'],
  lifecycle_status: 'active',
  references: {
    state_observations: 4,
    mqtt_boot_sessions: 1,
    mqtt_cursor: 1,
    non_management_events: 2,
    management_audits: 3,
    current_state_placeholder: 1,
    future_references: {},
  },
  can_delete: false,
  delete_blocking_reasons: ['state_observations', 'mqtt_boot_sessions'],
};

export function listItem(overrides: Partial<DeviceListItem> = {}): DeviceListItem {
  return {
    device_id: deviceDetail.device_id,
    display_name: deviceDetail.display_name,
    device_type: deviceDetail.device_type,
    area_id: deviceDetail.area_id,
    importance: deviceDetail.importance,
    profile_source: deviceDetail.profile_source,
    profile_version: deviceDetail.profile_version,
    operation_mode: deviceDetail.operation_mode,
    retired_at: deviceDetail.retired_at,
    retirement_reason: deviceDetail.retirement_reason,
    connection_status: deviceDetail.connection_status,
    ip_address: deviceDetail.ip_address,
    state_version: deviceDetail.state_version,
    last_received_at: deviceDetail.received_at,
    lifecycle_status: deviceDetail.lifecycle_status,
    ...overrides,
  };
}

export function listResponse(items = [listItem()], overrides: Partial<DeviceListResponse> = {}): DeviceListResponse {
  return {
    items,
    total: items.length,
    limit: 24,
    offset: 0,
    ...overrides,
  };
}

export function detailEnvelope(detail: DeviceDetail = deviceDetail) {
  return { device: detail };
}
