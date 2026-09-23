/** Archived Web Cookie client. Ordinary users must use src/mobile/api.ts. */
import { DEFAULT_API_BASE } from '../config';

let baseUrl = DEFAULT_API_BASE;
export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); this.name = 'ApiError'; }
}
export async function initClient(): Promise<void> { /* no legacy session restoration */ }
export function getBaseUrl(): string { return baseUrl; }
export function getDefaultBaseUrl(): string { return DEFAULT_API_BASE; }
export async function setBaseUrl(url: string): Promise<void> { baseUrl = url.trim(); }
export function clearSession(): void { /* no legacy session exists */ }
export async function request<T = unknown>(
  _path: string, _options: { method?: string; body?: unknown; formData?: FormData } = {},
): Promise<T> {
  throw new ApiError(410, '旧版管理端移动接口已停用，请使用安全配对');
}
export function qs(params?: Record<string, string | number | boolean | undefined | null>): string {
  if (!params) return '';
  const entries = Object.entries(params).filter(([, value]) => value !== '' && value !== undefined && value !== null);
  return entries.length ? '?' + entries.map(([key, value]) => `${encodeURIComponent(key)}=${encodeURIComponent(String(value))}`).join('&') : '';
}
