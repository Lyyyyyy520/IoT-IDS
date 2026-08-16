/**
 * API 方法集合 — 与原网页前端 frontend/src/api/index.ts 保持一致的后端调用
 */
import { request, qs } from './client';
import type {
  DashboardStats,
  AlertListResponse,
  AlertItem,
  HealthResponse,
  Asset,
  TrafficLog,
  CaptureStatus,
  ProbeStatus,
  AuditLog,
  BlocklistItem,
  ApiResult,
  User,
} from '../types';

export const api = {
  // ---- 系统 ----
  health: () => request<HealthResponse>('/health'),

  // ---- 认证 ----
  login: (username: string, password: string) =>
    request<{ success: boolean; message: string; user: User }>('/auth/login', {
      method: 'POST',
      body: { username, password },
    }),
  logout: () => request<{ success: boolean }>('/auth/logout', { method: 'POST' }),
  me: () => request<{ authenticated: boolean; user?: User }>('/auth/me'),

  // ---- 仪表盘 ----
  getDashboardStats: (params?: Record<string, string>) =>
    request<DashboardStats>(`/dashboard/stats${qs(params)}`),

  // ---- 告警 ----
  getAlerts: (params?: Record<string, string | number | boolean>) =>
    request<AlertListResponse>(`/alerts${qs(params)}`),
  getNewAlerts: (sinceId: number) =>
    request<{ items: AlertItem[]; max_id: number }>(`/alerts/new?since_id=${sinceId}`),
  blockIp: (id: number) =>
    request<ApiResult>(`/alerts/${id}/block`, { method: 'POST' }),
  unblockIp: (id: number) =>
    request<ApiResult>(`/alerts/${id}/unblock`, { method: 'POST' }),
  traceAlert: (id: number) =>
    request<{ success: boolean; trace_info: string }>(`/alerts/${id}/trace`, {
      method: 'POST',
    }),
  markFalsePositive: (id: number) =>
    request<ApiResult>(`/alerts/${id}/false-positive`, { method: 'POST' }),
  unmarkFalsePositive: (id: number) =>
    request<ApiResult>(`/alerts/${id}/unmark-false-positive`, { method: 'POST' }),

  // ---- 资产/设备 ----
  getAssets: () => request<{ items: Asset[] }>('/assets'),

  // ---- 流量 ----
  getTrafficLogs: (params?: Record<string, string>) =>
    request<{ items: TrafficLog[] }>(`/traffic/logs${qs(params)}`),

  // ---- 抓包控制 ----
  getCaptureStatus: () => request<CaptureStatus>('/capture/status'),
  startCapture: (useScapy = false, attackRatio?: number) =>
    request<CaptureStatus>('/capture/start', {
      method: 'POST',
      body: { use_scapy: useScapy, attack_ratio: attackRatio ?? 0.25 },
    }),
  stopCapture: () => request<CaptureStatus>('/capture/stop', { method: 'POST' }),

  // ---- 探针 ----
  getProbeStatus: () => request<ProbeStatus>('/probe/status'),
  getProbeList: () => request<{ probes: Asset[] }>('/probe/list'),

  // ---- 黑名单 ----
  getBlocklist: () => request<{ total: number; items: BlocklistItem[] }>('/blocklist'),
  deleteBlocklist: (id: number) =>
    request<ApiResult>(`/blocklist/${id}`, { method: 'DELETE' }),

  // ---- 日志 ----
  getAuditLogs: () => request<{ items: AuditLog[] }>('/logs/audit'),

  // ---- 分析 ----
  getHeatmap: () => request<any>('/analysis/heatmap'),
  getTopology: () => request<any>('/analysis/topology'),
  getMitre: () => request<any>('/analysis/mitre'),

  // ---- 配置 ----
  getConfig: () => request<any>('/config'),
  updateConfig: (data: Record<string, any>) =>
    request<ApiResult>('/config', { method: 'PUT', body: data }),
};
