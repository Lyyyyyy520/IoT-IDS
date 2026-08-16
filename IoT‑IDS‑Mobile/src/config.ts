/**
 * API Base URL 配置
 *
 * 移动端需要连接到运行在原网页后端 (D:\IoT‑IDS\backend) 的 Flask 服务。
 * 后端默认监听 http://localhost:5000，API 前缀为 /api。
 *
 * 在 Expo Go 开发模式下，会自动从 Expo 的 hostUri 推导出开发机局域网 IP，
 * 因此真机调试时无需手动配置即可连上同一台电脑上的后端。
 *
 * 你可以在 APP 内的「设置」页覆盖这个地址，或修改 app.json 的 extra.apiBaseUrl。
 */
import Constants from 'expo-constants';

function deriveDevHost(): string | null {
  const hostUri =
    Constants.expoConfig?.hostUri ??
    (Constants as any).expoGoConfig?.debuggerHost ??
    null;

  if (hostUri && typeof hostUri === 'string') {
    // hostUri 形如 "192.168.1.5:8081"
    const ip = hostUri.split(':')[0];
    if (ip && ip.includes('.')) return ip;
  }
  return null;
}

/**
 * 默认后端 API 地址。
 * 优先从 app.json 的 extra.apiBaseUrl 读取（非空时生效），
 * 其次自动推导开发机 IP，最后回退到 localhost。
 */
export const DEFAULT_API_BASE = (() => {
  const explicit = Constants.expoConfig?.extra?.apiBaseUrl as string | undefined;
  if (explicit && explicit.trim().length > 0) {
    return explicit.trim().replace(/\/+$/, '');
  }
  const host = deriveDevHost();
  if (host) return `http://${host}:5000/api`;
  return 'http://localhost:5000/api';
})();
