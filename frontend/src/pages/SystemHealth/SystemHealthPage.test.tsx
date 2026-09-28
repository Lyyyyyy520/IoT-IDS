import { render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { SystemHealthResponse } from '../../api/v3SystemHealth';
import SystemHealthPage from './index';

const api = vi.hoisted(() => ({ getSystemHealth: vi.fn() }));
vi.mock('../../api/v3SystemHealth', () => api);

const timestamp = '2026-09-24T12:00:00Z';
function health(overrides: Partial<SystemHealthResponse> = {}): SystemHealthResponse {
  const names = ['api', 'database', 'schema', 'integrity_check', 'mqtt', 'traffic', 'event_log', 'incident', 'mobile', 'discovery', 'graph'];
  return {
    observed_at: timestamp,
    components: Object.fromEntries(names.map((name) => [name, {
      status: name === 'graph' || name === 'mqtt' ? 'unavailable' : 'ready',
      updated_at: timestamp,
      reason_code: name === 'graph' ? 'graph_capability_unavailable' : name === 'mqtt' ? 'mqtt_disabled' : null,
      ...(name === 'database' ? { exists: true, readable: true, writable: true } : {}),
      ...(name === 'schema' ? { version: 9, legacy_schema_ready: true, migration_complete: true, migration_checksums_valid: true } : {}),
      ...(name === 'traffic' ? { aggregation_status: 'ready' } : {}),
      ...(name === 'integrity_check' ? { result: 'ok', checked_at: timestamp } : {}),
      ...(name === 'event_log' ? { retained_events: 12, oldest_event_id: 5, latest_event_id: 16 } : {}),
    }])) as SystemHealthResponse['components'],
    maintenance: {
      last_successful_at: null, last_plan_at: null, last_apply_at: null,
      last_plan_reason_code: 'maintenance_plan_read_only', reason_code: 'no_maintenance_run',
    },
    capacity: {
      database_file_bytes: 2048, page_size_bytes: 4096, page_count: 12,
      free_pages: 2, free_bytes: 8192, disk_free_bytes: 1024 * 1024,
    },
    automatic_maintenance: false,
    ...overrides,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  api.getSystemHealth.mockResolvedValue(health());
});

describe('SystemHealthPage', () => {
  it('shows component readiness, update times, capacity, and graph unavailable state', async () => {
    render(<SystemHealthPage />);
    expect(await screen.findByRole('heading', { name: '系统健康' })).toBeInTheDocument();
    expect(await screen.findByText('GNN 与 Graph capability 保持不可用。')).toBeInTheDocument();
    expect(screen.getAllByText('不可用', { selector: '.ant-tag' }).length).toBeGreaterThan(0);
    expect(screen.getByText(/页面总量/)).toBeInTheDocument();
    expect(screen.getByText(/文件存在 · 读取可用 · 写入可用/)).toBeInTheDocument();
    expect(screen.getByText(/迁移完整 · checksum通过/)).toBeInTheDocument();
    expect(screen.getByText(/流量聚合：就绪/)).toBeInTheDocument();
    expect(screen.getByText(/维护计划严格只读/)).toBeInTheDocument();
    expect(screen.getAllByText(/更新时间：/).length).toBeGreaterThan(5);
    expect(api.getSystemHealth).toHaveBeenCalledTimes(1);
  });

  it('shows degraded and unknown values as non-ready states with maintenance guidance', async () => {
    const payload = health();
    payload.components.database.status = 'degraded';
    payload.components.database.reason_code = 'database_disk_full';
    payload.components.traffic.status = 'unknown' as SystemHealthResponse['components'][string]['status'];
    payload.components.traffic.reason_code = 'unrecognized';
    api.getSystemHealth.mockResolvedValue(payload);

    render(<SystemHealthPage />);

    expect(await screen.findByText('磁盘空间不足。先释放或扩展存储，再用备份恢复并重试写入。')).toBeInTheDocument();
    expect(screen.getByText('建议：先解决容量问题，不要在故障状态下执行 VACUUM。')).toBeInTheDocument();
    expect(screen.getByText('组件报告了未识别的 reason code。检查运维文档和后端日志。')).toBeInTheDocument();
  });

  it('does not render a failed health request as a normal status', async () => {
    api.getSystemHealth.mockRejectedValue(new Error('system unavailable'));
    render(<SystemHealthPage />);

    expect(await screen.findByRole('alert')).toHaveTextContent('健康信息不可用');
    expect(screen.getByText('API 服务')).toBeInTheDocument();
    expect(screen.getAllByText('不可用', { selector: '.ant-tag' }).length).toBeGreaterThan(0);
  });
});
