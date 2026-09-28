export type SystemHealthStatus = 'ready' | 'warming_up' | 'degraded' | 'unavailable';

export interface SystemHealthComponent {
  status: SystemHealthStatus;
  updated_at: string;
  reason_code: string | null;
  version?: number;
  legacy_schema_ready?: boolean;
  result?: 'ok' | 'failed' | 'unavailable';
  checked_at?: string | null;
  retained_events?: number;
  oldest_event_id?: number | null;
  latest_event_id?: number | null;
  latest_cursor?: number;
  exists?: boolean | null;
  readable?: boolean;
  writable?: boolean | null;
  migration_complete?: boolean;
  migration_checksums_valid?: boolean | null;
  aggregation_status?: SystemHealthStatus;
}

export interface SystemHealthResponse {
  observed_at: string;
  components: Record<string, SystemHealthComponent>;
  maintenance: {
    last_successful_at: string | null;
    last_plan_at: string | null;
    last_apply_at: string | null;
    last_plan_reason_code: string | null;
    reason_code: string | null;
  };
  capacity: {
    database_file_bytes: number | null;
    page_size_bytes: number | null;
    page_count: number | null;
    free_pages: number | null;
    free_bytes: number | null;
    disk_free_bytes: number | null;
  };
  automatic_maintenance: false;
}

export async function getSystemHealth(signal?: AbortSignal): Promise<SystemHealthResponse> {
  const response = await fetch('/api/v3/system/health', {
    method: 'GET',
    credentials: 'include',
    headers: { Accept: 'application/json' },
    signal,
  });
  if (!response.ok) {
    throw new Error('系统健康检查失败（HTTP ' + response.status + '）');
  }
  return response.json() as Promise<SystemHealthResponse>;
}
