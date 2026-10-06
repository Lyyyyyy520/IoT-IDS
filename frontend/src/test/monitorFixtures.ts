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
      risk_level: null,
      risk_name: null,
      risk_confidence: null,
      risk_detected_at: null,
      sources: ['mqtt'],
    },
  ],
  system_components: [],
  capabilities: {
    graph: { available: true, reason: null },
    incident: { available: false, reason: 'incident_store_not_migrated' },
  },
  graph: {
    window: { from: '2026-09-19T02:00:00Z', to: '2026-09-20T02:00:00Z', range: '24h' },
    nodes: [
      { id: 'camera-01', label: '东门摄像头', type: 'camera', status: 'online', risk_level: null, risk_name: null, ip: '192.168.4.21' },
      { id: 'light-01', label: '路灯', type: 'light', status: 'online', risk_level: 0, risk_name: '正常(绿)', ip: '192.168.4.12' },
    ],
    edges: [
      { source: 'camera-01', target: 'light-01', bytes: 120, packets: 3, protocols: ['coap'] },
    ],
    availability: { source: 'v3_device_traffic_peer_minutes', reason: null },
  },
};
