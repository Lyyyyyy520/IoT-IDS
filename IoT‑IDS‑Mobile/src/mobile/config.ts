import AsyncStorage from '@react-native-async-storage/async-storage';

const SERVER_KEY = 'iot_ids_mobile_server_v1';
const LEGACY_KEYS = [
  'iot_ids_session_cookie', 'iot_ids_username', 'iot_ids_password',
  'iot_ids_access_token', 'iot_ids_refresh_token', 'iot_ids_token',
];

export type ServerConfig = { baseUrl: string; insecureLan: boolean };
export class ServerConfigError extends Error { constructor(message: string) { super(message); this.name = 'ServerConfigError'; } }

export function validateServerUrl(input: string, insecureLan: boolean): string {
  let url: URL;
  try { url = new URL(input.trim()); } catch { throw new ServerConfigError('请输入完整的服务器地址'); }
  const host = url.hostname.replace(/^\[|\]$/g, '').toLowerCase();
  const localHttp = host === 'localhost' || host === '::1' || host.endsWith('.local') ||
    /^127\./.test(host) || /^10\./.test(host) || /^192\.168\./.test(host) ||
    (() => { const match = /^172\.(\d+)\./.exec(host); return !!match && Number(match[1]) >= 16 && Number(match[1]) <= 31; })();
  if (url.protocol !== 'https:' && !(insecureLan && __DEV__ && url.protocol === 'http:' && localHttp)) {
    throw new ServerConfigError('服务器地址必须使用 HTTPS；仅隔离局域网开发模式可显式启用 HTTP');
  }
  if (!url.hostname || url.username || url.password || url.search || url.hash ||
      (url.pathname !== '/' && url.pathname !== '')) {
    throw new ServerConfigError('地址只能包含协议、主机和可选端口，不得包含路径或凭据');
  }
  return url.origin;
}

export async function clearLegacyAuthentication(): Promise<void> {
  await AsyncStorage.multiRemove(LEGACY_KEYS);
}

export async function readServerConfig(): Promise<ServerConfig | null> {
  const raw = await AsyncStorage.getItem(SERVER_KEY);
  if (!raw) return null;
  try {
    const value: unknown = JSON.parse(raw);
    if (!value || typeof value !== 'object') return null;
    const candidate = value as Record<string, unknown>;
    if (typeof candidate.baseUrl !== 'string' || typeof candidate.insecureLan !== 'boolean') return null;
    return { baseUrl: validateServerUrl(candidate.baseUrl, candidate.insecureLan), insecureLan: candidate.insecureLan };
  } catch { return null; }
}

export async function saveServerConfig(baseUrl: string, insecureLan: boolean): Promise<ServerConfig> {
  const config = { baseUrl: validateServerUrl(baseUrl, insecureLan), insecureLan };
  await AsyncStorage.setItem(SERVER_KEY, JSON.stringify(config));
  return config;
}
