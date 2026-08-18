/**
 * HTTP 客户端 — 封装 fetch 并手动管理 Flask 会话 Cookie
 *
 * 原网页后端 (D:\IoT‑IDS\backend) 使用 Flask Session（签名 Cookie）做认证，
 * 移动端没有浏览器自动 Cookie 存储，因此这里：
 *   1. 登录后从响应头 Set-Cookie 中抓取 session=xxx
 *   2. 持久化到 AsyncStorage
 *   3. 后续每个请求都手动带上 Cookie: session=xxx
 */
import AsyncStorage from '@react-native-async-storage/async-storage';
import { DEFAULT_API_BASE } from '../config';

const COOKIE_KEY = 'iot_ids_session_cookie';
const URL_KEY = 'iot_ids_api_base';

let sessionCookie: string | null = null;
let baseUrl: string = DEFAULT_API_BASE;

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
    this.name = 'ApiError';
  }
}

/** 初始化：从本地存储恢复 Cookie 与后端地址（App 启动时调用一次） */
export async function initClient(): Promise<void> {
  try {
    const [cookie, url] = await Promise.all([
      AsyncStorage.getItem(COOKIE_KEY),
      AsyncStorage.getItem(URL_KEY),
    ]);
    if (cookie) sessionCookie = cookie;
    if (url) baseUrl = url;
  } catch {
    // ignore
  }
}

export function getBaseUrl(): string {
  return baseUrl;
}

export function getDefaultBaseUrl(): string {
  return DEFAULT_API_BASE;
}

/** 设置并持久化后端地址 */
export async function setBaseUrl(url: string): Promise<void> {
  const cleaned = url.trim().replace(/\/+$/, '');
  baseUrl = cleaned;
  try {
    await AsyncStorage.setItem(URL_KEY, cleaned);
  } catch {
    // ignore
  }
}

/** 清除会话（登出时调用） */
export function clearSession(): void {
  sessionCookie = null;
  AsyncStorage.removeItem(COOKIE_KEY).catch(() => {});
}

function extractSessionCookie(res: Response): string | null {
  try {
    const raw = res.headers.get('set-cookie');
    if (!raw) return null;
    const m = raw.match(/session=([^;,\s]+)/i);
    return m ? `session=${m[1]}` : null;
  } catch {
    return null;
  }
}

async function persistSession(): Promise<void> {
  try {
    if (sessionCookie) await AsyncStorage.setItem(COOKIE_KEY, sessionCookie);
    else await AsyncStorage.removeItem(COOKIE_KEY);
  } catch {
    // ignore
  }
}

interface RequestOptions {
  method?: string;
  body?: any;
  formData?: FormData;
}

export async function request<T = any>(
  path: string,
  options: RequestOptions = {},
): Promise<T> {
  const method = options.method ?? 'GET';
  const headers: Record<string, string> = {};
  let body: any = undefined;

  if (options.formData) {
    body = options.formData;
    // 使用 FormData 时不能手动设置 Content-Type，让 fetch 自动带上 boundary
  } else if (options.body !== undefined) {
    headers['Content-Type'] = 'application/json';
    body = JSON.stringify(options.body);
  }

  if (sessionCookie) headers['Cookie'] = sessionCookie;

  let res: Response;
  try {
    res = await fetch(baseUrl + path, { method, headers, body });
  } catch {
    throw new ApiError(0, '无法连接后端服务，请确认后端已启动且地址正确');
  }

  // 后端若刷新了会话 Cookie（如登录成功），则更新并持久化
  const newCookie = extractSessionCookie(res);
  if (newCookie && newCookie !== sessionCookie) {
    sessionCookie = newCookie;
    await persistSession();
  }

  const text = await res.text();
  let data: any = null;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = null;
    }
  }

  if (!res.ok) {
    const msg =
      data?.message || data?.error || `请求失败 (${res.status})`;
    throw new ApiError(res.status, msg);
  }

  return data as T;
}

/** 拼接查询字符串 */
export function qs(params?: Record<string, string | number | boolean | undefined | null>): string {
  if (!params) return '';
  const entries = Object.entries(params).filter(
    ([, v]) => v !== '' && v !== undefined && v !== null,
  );
  if (entries.length === 0) return '';
  const s = entries
    .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(String(v))}`)
    .join('&');
  return `?${s}`;
}
