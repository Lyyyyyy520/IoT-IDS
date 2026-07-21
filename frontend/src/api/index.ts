/**
 * API Service Layer — Centralized backend API calls
 * All requests go through Vite proxy (/api → Flask port 5000)
 */

const BASE = '/api';

async function request<T>(url: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${url}`, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  });
  if (!res.ok) throw new Error(`API Error: ${res.status} ${res.statusText}`);
  return res.json();
}

// ---- Types ----
export interface DashboardStats {
  total_scanned: number;
  alerts_today: number;
  total_alerts: number;
  active_threats: number;
  total_assets: number;
  online_assets: number;
  risk_score: number;
  system_status: string;
  traffic_history: { time: string; normal: number; attack: number }[];
  attack_distribution: { type: string; count: number }[];
  recent_alerts: AlertItem[];
}

export interface AlertItem {
  id: number;
  risk_level: string;
  attack_type: string;
  src_ip: string;
  dst_ip: string;
  confidence: number;
  timestamp: string;
  merged_count: number;
  status: string;
  description: string;
}

export interface AlertListResponse {
  total: number;
  page: number;
  page_size: number;
  items: AlertItem[];
}

export interface BlocklistItem {
  id: number;
  ip_address: string;
  reason: string;
  blocked_at: string;
  enabled: number;
  alert_id: number | null;
  attack_type: string | null;
  risk_level: string | null;
  src_ip: string | null;
  dst_ip: string | null;
  alert_status: string | null;
}

export interface BlocklistResponse {
  total: number;
  items: BlocklistItem[];
}

export interface HealthResponse {
  status: string;
  model_loaded: boolean;
  model_name?: string | null;
  model_path?: string | null;
  uptime: number;
}

export interface ModelItem {
  id: string;
  name: string;
  filename: string;
  path: string;
  size_bytes: number;
  updated_at: number;
  active: boolean;
}

export interface ModelListResponse {
  items: ModelItem[];
  active_model_id: string | null;
  model_loaded: boolean;
}

// ---- API Methods ----
export const api = {
  health: () => request<HealthResponse>('/health'),

  getDashboardStats: () => request<DashboardStats>('/dashboard/stats'),

  getAlerts: (params?: Record<string, string>) => {
    const query = params ? '?' + new URLSearchParams(params).toString() : '';
    return request<AlertListResponse>(`/alerts${query}`);
  },

  blockIp: (id: number) => request<{ success: boolean; message: string }>(`/alerts/${id}/block`, { method: 'POST' }),

  unblockIp: (id: number) => request<{ success: boolean; message: string }>(`/alerts/${id}/unblock`, { method: 'POST' }),

  traceAlert: (id: number) => request<{ success: boolean; trace_info: string }>(`/alerts/${id}/trace`, { method: 'POST' }),

  markFalsePositive: (id: number) => request<{ success: boolean; message: string }>(`/alerts/${id}/false-positive`, { method: 'POST' }),

  unmarkFalsePositive: (id: number) => request<{ success: boolean; message: string }>(`/alerts/${id}/unmark-false-positive`, { method: 'POST' }),

  // Blacklist
  getBlocklist: () => request<BlocklistResponse>('/blocklist'),
  deleteBlocklist: (id: number) => request<{ success: boolean; message: string }>(`/blocklist/${id}`, { method: 'DELETE' }),

  // Policies
  getPolicies: (type?: string) => {
    const q = type ? `?type=${type}` : '';
    return request<{ items: any[] }>(`/policies${q}`);
  },
  createPolicy: (data: any) => request<{ success: boolean; id: number }>('/policies', { method: 'POST', body: JSON.stringify(data) }),
  updatePolicy: (id: number, data: any) => request<{ success: boolean }>(`/policies/${id}`, { method: 'PUT', body: JSON.stringify(data) }),
  deletePolicy: (id: number) => request<{ success: boolean }>(`/policies/${id}`, { method: 'DELETE' }),

  // Assets
  getAssets: () => request<{ items: any[] }>('/assets'),
  createAsset: (data: any) => request<{ success: boolean; id: number }>('/assets', { method: 'POST', body: JSON.stringify(data) }),
  updateAsset: (id: number, data: any) => request<{ success: boolean }>(`/assets/${id}`, { method: 'PUT', body: JSON.stringify(data) }),
  deleteAsset: (id: number) => request<{ success: boolean }>(`/assets/${id}`, { method: 'DELETE' }),

  // Logs
  getAuditLogs: () => request<{ items: any[] }>('/logs/audit'),
  getArchivedTrafficLogs: () => request<{ items: any[] }>('/logs/traffic'),

  getConfig: () => request<any>('/config'),

  getModels: () => request<ModelListResponse>('/models'),
  selectModel: (modelId: string) => request<{ success: boolean; message: string; active_model_id: string; model: ModelItem }>(
    '/models/select',
    { method: 'POST', body: JSON.stringify({ model_id: modelId }) },
  ),
  uploadModel: (file: File) => {
    const formData = new FormData();
    formData.append('file', file);
    return fetch(`${BASE}/models/upload`, { method: 'POST', body: formData }).then((r) => r.json());
  },

  getTopology: () => request<any>('/analysis/topology'),

  getHeatmap: () => request<any>('/analysis/heatmap'),

  getMitre: () => request<any>('/analysis/mitre'),

  // Capture control
  getCaptureStatus: () => request<any>('/capture/status'),
  startCapture: (useScapy: boolean) => request<any>('/capture/start', { method: 'POST', body: JSON.stringify({ use_scapy: useScapy }) }),
  stopCapture: () => request<any>('/capture/stop', { method: 'POST' }),

  // Probe
  getProbeStatus: () => request<any>('/probe/status'),
  getProbeList: () => request<any>('/probe/list'),

  // Traffic logs
  getTrafficLogs: (params?: Record<string, string>) => {
    const query = params ? '?' + new URLSearchParams(params).toString() : '';
    return request<any>(`/traffic/logs${query}`);
  },

  uploadPcap: (file: File) => {
    const formData = new FormData();
    formData.append('file', file);
    return fetch(`${BASE}/detect/upload`, { method: 'POST', body: formData }).then((r) => r.json());
  },
};
