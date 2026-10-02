import type {
  HelpRequestDetail,
  IncidentDetail,
  IncidentListItem,
  IncidentListResponse,
} from '../api/v3Incidents';

export const incidentListItem: IncidentListItem = {
  incident_id: 'inc_0123456789abcdef',
  incident_type: 'unexpected_traffic',
  severity: 'high',
  status: 'open',
  source: 'manual',
  admin_title: '门厅摄像机通信异常',
  user_title: '门厅设备需要关注',
  first_seen_at: '2026-09-23T02:00:00Z',
  last_seen_at: '2026-09-23T02:01:00Z',
  updated_at: '2026-09-23T02:01:00Z',
  resolved_at: null,
  incident_version: 1,
  mobile_published: true,
  affected_device_count: 1,
  latest_public_progress: '管理员正在核查设备状态。',
};

export const incidentDetail: IncidentDetail = {
  ...incidentListItem,
  admin_summary: '快速规则记录到需要人工复核的通信变化。',
  user_summary: '管理员正在检查设备服务，请留意后续进度。',
  created_at: '2026-09-23T02:01:00Z',
  created_by: 1,
  resolution_summary: null,
  false_positive_reason: null,
  devices: [
    { device_id: 'camera-01', incident_role: 'affected', user_visible: true, display_name: '门厅摄像机', device_type: 'camera', area_id: 'area-a' },
    { device_id: 'lock-01', incident_role: 'suspected_source', user_visible: false, display_name: '门锁控制器', device_type: 'lock', area_id: 'area-a' },
  ],
  timeline: [{
    timeline_id: 1, action: 'opened', actor_user_id: 1, actor_username: 'admin-a', actor_role: 'admin',
    occurred_at: '2026-09-23T02:01:00Z', request_id: 'req-open',
    public_progress: '管理员正在核查设备状态。', admin_details: '仅管理端证据摘要',
    resulting_status: 'open', incident_version: 1,
  }],
  user_preview: {
    user_title: '门厅设备需要关注',
    user_summary: '管理员正在检查设备服务，请留意后续进度。',
    public_progress: '管理员正在核查设备状态。',
  },
};

export function incidentListResponse(overrides: Partial<IncidentListResponse> = {}): IncidentListResponse {
  return { items: [incidentListItem], total: 1, limit: 25, offset: 0, event_cursor: 17, ...overrides };
}

export const helpDetail: HelpRequestDetail = {
  help_request_id: 'help_0123456789abcdef', user_id: 8, incident_id: incidentListItem.incident_id,
  device_id: 'camera-01', category: 'device_issue', user_message: '<script>我家的设备无法使用</script>',
  status: 'open', public_response: null, internal_note: null, assigned_to: null,
  created_at: '2026-09-23T03:00:00Z', updated_at: '2026-09-23T03:00:00Z', closed_at: null,
  request_version: 1,
  timeline: [{
    timeline_id: 1, help_request_id: 'help_0123456789abcdef', action: 'created', actor_user_id: 8,
    actor_username: 'resident-a', actor_role: 'user', occurred_at: '2026-09-23T03:00:00Z',
    request_id: 'req-help', public_response: null, internal_note: null, resulting_status: 'open', request_version: 1,
  }],
};
