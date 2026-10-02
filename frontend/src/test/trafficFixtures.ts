import type { PeersResponse, TrafficResponse } from '../api/v3Traffic';

export function trafficResponse(overrides: Partial<TrafficResponse> = {}): TrafficResponse {
  return {
    api_version: 'v3',
    traffic_schema_version: 1,
    generated_at: '2026-09-21T10:00:00Z',
    device_id: 'camera-01',
    window: { from: '2026-09-21T09:00:00Z', to: '2026-09-21T10:00:00Z' },
    resolution: 'minute',
    protocol_filter: null,
    availability: {
      available: true, reason: null, latest_sample_at: '2026-09-21T09:59:55Z',
    },
    freshness: {
      historical_source: 'sqlite_minute_aggregates',
      latest_sample_at: '2026-09-21T09:59:55Z',
    },
    realtime: {
      available: true,
      readiness: 'ready',
      reason: null,
      window_seconds: 60,
      as_of: '2026-09-21T10:00:00Z',
      tx_bytes_per_second: 128,
      rx_bytes_per_second: 64,
      tx_packets_per_second: 2,
      rx_packets_per_second: 1,
      tx_flows_per_second: 0.5,
      rx_flows_per_second: 0.25,
    },
    summary: {
      tx_bytes: 6000, rx_bytes: 3000,
      tx_packets: 60, rx_packets: 30,
      tx_flows: 6, rx_flows: 3,
    },
    series: [
      {
        bucket_start: '2026-09-21T09:58:00Z',
        tx_bytes: 1200, rx_bytes: 600,
        tx_packets: 12, rx_packets: 6,
        tx_flows: 2, rx_flows: 1,
      },
      {
        bucket_start: '2026-09-21T09:59:00Z',
        tx_bytes: 1800, rx_bytes: 900,
        tx_packets: 18, rx_packets: 9,
        tx_flows: 3, rx_flows: 1,
      },
    ],
    protocols: [{
      protocol: 'TCP', evidence: 'network_protocol',
      tx_bytes: 5000, rx_bytes: 2500,
      tx_packets: 50, rx_packets: 25,
      tx_flows: 5, rx_flows: 2,
    }, {
      protocol: 'UNKNOWN', evidence: 'network_protocol',
      tx_bytes: 1000, rx_bytes: 500,
      tx_packets: 10, rx_packets: 5,
      tx_flows: 1, rx_flows: 1,
    }],
    data_quality: {
      unassigned_samples_in_window: 0,
      has_unassigned_or_missing_data: false,
      note: null,
    },
    ...overrides,
  };
}

export function emptyTrafficResponse(overrides: Partial<TrafficResponse> = {}): TrafficResponse {
  return trafficResponse({
    availability: { available: false, reason: 'no_samples', latest_sample_at: null },
    freshness: { historical_source: 'sqlite_minute_aggregates', latest_sample_at: null },
    realtime: {
      available: false,
      readiness: 'warming_up',
      reason: 'realtime_window_empty_after_restart_or_no_recent_samples',
      window_seconds: 120,
      as_of: '2026-09-21T10:00:00Z',
    },
    summary: null,
    series: [],
    protocols: [],
    ...overrides,
  });
}

export function peersResponse(overrides: Partial<PeersResponse> = {}): PeersResponse {
  return {
    api_version: 'v3',
    traffic_schema_version: 1,
    generated_at: '2026-09-21T10:00:00Z',
    device_id: 'camera-01',
    window: { from: '2026-09-21T09:00:00Z', to: '2026-09-21T10:00:00Z' },
    filters: { direction: null, protocol: null },
    sort: 'bytes',
    availability: { available: true, reason: null },
    peers: [{
      peer_device_id: 'gateway-01',
      peer_ip: '192.168.1.1',
      peer_ip_visible: true,
      direction: 'tx',
      protocol: 'TCP',
      bytes: 5000,
      packets: 50,
      flows: 5,
      first_seen: '2026-09-21T09:10:00Z',
      last_seen: '2026-09-21T09:59:00Z',
    }, {
      peer_device_id: null,
      peer_ip: '203.0.113.10',
      peer_ip_visible: true,
      direction: 'rx',
      protocol: 'UDP',
      bytes: 1000,
      packets: 10,
      flows: 1,
      first_seen: '2026-09-21T09:20:00Z',
      last_seen: '2026-09-21T09:40:00Z',
    }],
    pagination: { limit: 50, offset: 0, total: 2, has_more: false },
    ...overrides,
  };
}
