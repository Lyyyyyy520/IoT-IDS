import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { IncidentListItem, IncidentReplay } from '../../api/v3Incidents';
import DeviceIncidentReplayPanel from './DeviceIncidentReplayPanel';

const { listIncidents, getIncidentReplay } = vi.hoisted(() => ({
  listIncidents: vi.fn(),
  getIncidentReplay: vi.fn(),
}));

vi.mock('../../api/v3Incidents', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/v3Incidents')>();
  return {
    ...actual,
    v3IncidentsApi: { listIncidents, getIncidentReplay },
  };
});

const incident: IncidentListItem = {
  incident_id: 'inc_camera_01',
  incident_type: 'unexpected_traffic',
  severity: 'high',
  status: 'open',
  source: 'rule',
  admin_title: '摄像头通信异常',
  user_title: '设备需要关注',
  first_seen_at: '2026-10-05T09:00:00Z',
  last_seen_at: '2026-10-05T09:05:00Z',
  updated_at: '2026-10-05T09:05:00Z',
  resolved_at: null,
  incident_version: 1,
  mobile_published: false,
  affected_device_count: 1,
  latest_public_progress: null,
};

const replay: IncidentReplay = {
  incident_id: incident.incident_id,
  generated_at: '2026-10-05T09:10:00Z',
  window: { from: '2026-10-05T08:00:00Z', to: '2026-10-05T10:00:00Z' },
  incident_timeline: [{
    timeline_id: 1,
    action: 'acknowledged',
    actor_user_id: 1,
    actor_username: 'operator-a',
    actor_role: 'admin',
    occurred_at: '2026-10-05T09:06:00Z',
    request_id: 'req-1',
    public_progress: '正在核查设备',
    admin_details: null,
    resulting_status: 'acknowledged',
    incident_version: 2,
  }],
  devices: [
    {
      device_id: 'camera-01', incident_role: 'affected', user_visible: true,
      display_name: '门厅摄像机', device_type: 'camera', area_id: 'hall',
      current_connection_status: 'online', current_last_received_at: '2026-10-05T09:04:00Z',
      observations: [{
        observation_id: 1, device_id: 'camera-01', observed_at: '2026-10-05T09:03:00Z',
        received_at: '2026-10-05T09:03:01Z', temperature_c: 21, humidity_percent: null,
      }],
    },
    {
      device_id: 'sensor-02', incident_role: 'observer', user_visible: false,
      display_name: '走廊传感器', device_type: 'temp_sensor', area_id: 'hall',
      current_connection_status: 'stale', current_last_received_at: '2026-10-05T08:59:00Z',
      observations: [{
        observation_id: 2, device_id: 'sensor-02', observed_at: '2026-10-05T09:02:00Z',
        received_at: '2026-10-05T09:02:01Z', temperature_c: null, humidity_percent: 51,
      }],
    },
  ],
  relationships: [
    {
      source_device_id: 'camera-01', target_device_id: 'sensor-02', bucket_start: '2026-10-05T09:01:00Z',
      direction: 'outbound', protocol: 'mqtt', bytes: 300, packets: 3, flows: 1,
      first_seen: '2026-10-05T09:01:00Z', last_seen: '2026-10-05T09:01:30Z',
    },
    {
      source_device_id: 'unrelated-01', target_device_id: 'sensor-02', bucket_start: '2026-10-05T09:02:00Z',
      direction: 'outbound', protocol: 'coap', bytes: 100, packets: 1, flows: 1,
      first_seen: '2026-10-05T09:02:00Z', last_seen: '2026-10-05T09:02:30Z',
    },
  ],
  connection_status_changes: [
    { event_id: 1, device_id: 'camera-01', occurred_at: '2026-10-05T09:00:00Z', state_version: 4, from_status: 'stale', to_status: 'online', source: 'report' },
    { event_id: 2, device_id: 'sensor-02', occurred_at: '2026-10-05T08:59:00Z', state_version: 2, from_status: 'online', to_status: 'stale', source: 'timeout' },
  ],
  gaps: [
    { device_id: 'camera-01', from: '2026-10-05T08:30:00Z', to: '2026-10-05T08:40:00Z', reason: 'no_retained_observation' },
    { device_id: 'sensor-02', from: '2026-10-05T08:10:00Z', to: '2026-10-05T08:20:00Z', reason: 'no_retained_observation' },
  ],
  truncated: false,
  traffic_available: true,
  capabilities: { connection_status_history: true, camera_media: false, reason: 'camera_media_not_retained' },
};

beforeEach(() => {
  listIncidents.mockReset().mockResolvedValue({ items: [incident], total: 1, limit: 20, offset: 0, event_cursor: 5 });
  getIncidentReplay.mockReset().mockResolvedValue(replay);
});

describe('device contextual incident replay', () => {
  it('loads related incidents, limits replay records to the selected device, and supports a new time range', async () => {
    render(<DeviceIncidentReplayPanel deviceId="camera-01" deviceName="门厅摄像机" />);

    await waitFor(() => expect(listIncidents).toHaveBeenCalledWith(
      { device_id: 'camera-01', limit: 20, offset: 0 }, expect.any(AbortSignal),
    ));
    expect(await screen.findByText(/当前设备视角：门厅摄像机/)).toBeInTheDocument();
    expect(await screen.findByText('摄像头画面未留存')).toBeInTheDocument();
    expect(screen.getByText('温度 21 °C')).toBeInTheDocument();
    expect(screen.queryByText('湿度 51 %RH')).not.toBeInTheDocument();
    expect(screen.getByText(/门厅摄像机 → 走廊传感器 · mqtt/)).toBeInTheDocument();
    expect(screen.queryByText(/unrelated-01 → 走廊传感器/)).not.toBeInTheDocument();
    expect(screen.getByText(/正在核查设备/)).toBeInTheDocument();
    expect(screen.getByText(/当前设备视角中存在 1 段数据缺失区间/)).toBeInTheDocument();
    expect(screen.queryByText(/online → stale/)).not.toBeInTheDocument();

    fireEvent.change(screen.getByLabelText('开始时间'), { target: { value: '2026-10-05T08:30' } });
    fireEvent.change(screen.getByLabelText('结束时间'), { target: { value: '2026-10-05T09:30' } });
    fireEvent.click(screen.getByRole('button', { name: '查询' }));
    await waitFor(() => expect(getIncidentReplay).toHaveBeenLastCalledWith(
      incident.incident_id,
      { from: '2026-10-05T00:30:00.000Z', to: '2026-10-05T01:30:00.000Z' },
      expect.any(AbortSignal),
    ));
  });

  it('gives an explicit empty state when a device has no related incidents', async () => {
    listIncidents.mockResolvedValue({ items: [], total: 0, limit: 20, offset: 0, event_cursor: 5 });

    render(<DeviceIncidentReplayPanel deviceId="camera-01" deviceName="门厅摄像机" />);

    expect(await screen.findByText('没有找到与此设备关联的已记录事件。')).toBeInTheDocument();
    expect(getIncidentReplay).not.toHaveBeenCalled();
  });
});
