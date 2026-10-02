import type { MonitorSnapshot } from '../api/v3Monitor';

export const validSnapshot: MonitorSnapshot = {
  api_version: 'v3',
  schema_version: 4,
  generated_at: '2026-09-20T02:00:00Z',
  event_cursor: 5,
  devices: [
    {
      device_id: 'camera-01',
      display_name: '东门摄像头',
      device_type: 'camera',
      area_id: 'east-gate',
      operation_mode: 'active',
      connection_status: 'online',
      ip_address: '192.168.4.21',
      state_version: 5,
      observed_at: '2026-09-20T02:00:00Z',
      received_at: '2026-09-20T02:00:00Z',
      sources: ['mqtt'],
    },
  ],
  system_components: [],
  capabilities: {
    graph: { available: false, reason: 'graph_snapshots_not_implemented' },
    incident: { available: false, reason: 'incident_store_not_implemented' },
  },
};
