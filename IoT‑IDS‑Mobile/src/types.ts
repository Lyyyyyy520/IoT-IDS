/**
 * 与后端 D:\IoT‑IDS\backend 返回结构一一对应的类型定义
 */

export interface User {
  id: number;
  username: string;
  role: string;
}

export interface AlertItem {
  id: number;
  risk_level: string;
  attack_type: string;
  src_ip: string;
  dst_ip: string;
  src_port?: number;
  dst_port?: number;
  protocol?: string;
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

export interface TrafficPoint {
  time: string;
  normal: number;
  attack: number;
}

export interface AttackDistributionItem {
  type: string;
  count: number;
}

export interface DashboardStats {
  total_scanned: number;
  alerts_today: number;
  total_alerts: number;
  active_threats: number;
  total_assets: number;
  online_assets: number;
  risk_score: number;
  system_status: string;
  traffic_history: TrafficPoint[];
  attack_distribution: AttackDistributionItem[];
  recent_alerts: AlertItem[];
}

export interface HealthResponse {
  status: string;
  model_loaded: boolean;
  uptime: number;
  timestamp?: string;
}

export interface Asset {
  id: number;
  name: string;
  ip_address: string;
  mac_address?: string;
  device_type: string;
  status: string;
  risk_level: string;
  last_seen?: string;
  created_at?: string;
}

export interface TrafficLog {
  id: number;
  timestamp: string;
  src_ip: string;
  dst_ip: string;
  src_port?: number;
  dst_port?: number;
  protocol?: string;
  length?: number;
  flags?: string;
  source?: string;
  onnx_label?: string;
}

export interface CaptureStatus {
  running: boolean;
  mode?: string;
  packet_count?: number;
  attack_count?: number;
  [key: string]: any;
}

export interface ProbeStatus {
  total_probes: number;
  online_probes: number;
  offline_probes: number;
  alerts_from_probes: number;
}

export interface AuditLog {
  id: number;
  user_id?: number;
  username: string;
  action: string;
  detail?: string;
  ip_address?: string;
  created_at: string;
}

export interface BlocklistItem {
  id: number;
  ip_address: string;
  reason?: string;
  blocked_at?: string;
  enabled: number;
  attack_type?: string | null;
  risk_level?: string | null;
}

export interface ApiResult {
  success: boolean;
  message?: string;
  [key: string]: any;
}
