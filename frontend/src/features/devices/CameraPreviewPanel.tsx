import { useEffect, useMemo, useState } from 'react';
import type { DeviceDetail } from '../../api/v3Devices';

type CameraSource =
  | { kind: 'unconfigured' }
  | { kind: 'invalid' }
  | { kind: 'credentials' }
  | { kind: 'ready'; address: string };

const SECRET_QUERY_KEY = /(^|[-_])(authorization|auth|access[_-]?token|refresh[_-]?token|token|api[_-]?key|key|secret|password|credential|signature|sig|jwt|security[_-]?token|client[_-]?secret)([-_]|$)/i;

function configuredImageSource(device: DeviceDetail): CameraSource {
  const template = import.meta.env.VITE_CAMERA_STREAM_URL_TEMPLATE?.trim();
  if (!template) return { kind: 'unconfigured' };
  const value = template
    .replace(/\{device_id\}/g, encodeURIComponent(device.device_id))
    .replace(/\{ip_address\}/g, encodeURIComponent(device.ip_address ?? ''));
  try {
    const parsed = new URL(value);
    if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') return { kind: 'invalid' };
    let containsSecretParameter = false;
    parsed.searchParams.forEach((_value, key) => {
      if (SECRET_QUERY_KEY.test(key)) containsSecretParameter = true;
    });
    if (parsed.username || parsed.password || containsSecretParameter) {
      return { kind: 'credentials' };
    }
    return { kind: 'ready', address: parsed.toString() };
  } catch {
    return { kind: 'invalid' };
  }
}

function localTime(value: string | null): string {
  if (!value) return '暂无';
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
  }).format(parsed);
}

export default function CameraPreviewPanel({ device }: { device: DeviceDetail }) {
  const source = useMemo(() => configuredImageSource(device), [device.device_id, device.ip_address]);
  const address = source.kind === 'ready' ? source.address : null;
  const [loadError, setLoadError] = useState(false);
  const [loadedAt, setLoadedAt] = useState<string | null>(null);
  const [expanded, setExpanded] = useState(false);
  useEffect(() => {
    setLoadError(false);
    setLoadedAt(null);
    setExpanded(false);
  }, [device.device_id, address]);
  if (device.device_type.toLowerCase() !== 'camera') return null;
  const connectionKind = device.connection_status === 'offline'
    ? 'offline'
    : loadError ? 'failed' : address && loadedAt ? 'connected' : address ? 'connecting' : 'unavailable';
  const connectionLabel = connectionKind === 'offline' ? '设备离线'
    : connectionKind === 'failed' ? '画面连接失败'
      : connectionKind === 'connected' ? '画面已接入'
        : connectionKind === 'connecting' ? '正在连接画面' : '画面源未接入';

  return (
    <section className="camera-preview-panel" aria-labelledby="camera-preview-title">
      <header className="sensor-history-heading">
        <div><h3 id="camera-preview-title">摄像头画面</h3><span>连接状态基于浏览器实际加载结果</span></div>
        <span className={`camera-connection camera-${connectionKind}`}>
          {connectionLabel}
        </span>
      </header>
      {device.connection_status === 'offline' ? (
        <div className="camera-empty" role="status"><strong>设备离线</strong><span>当前无法确认摄像头可连接；未显示缓存画面。</span></div>
      ) : !address ? (
        <div className="camera-empty" role="status">
          <strong>{source.kind === 'credentials' ? '拒绝在浏览器加载含认证信息的画面地址' : source.kind === 'invalid' ? '摄像头画面地址格式无效' : '没有可用的视频流或最近画面'}</strong>
          <span>{source.kind === 'credentials'
            ? 'VITE_ 配置会进入浏览器可见资源，不能存放账号、密码、令牌或签名 URL。需要鉴权时应通过受 session 权限保护的同源后端代理，并将摄像头凭据留在服务端。'
            : '当前摄像头固件仅上报 MQTT 心跳，本项目没有摄像头流地址或已留存快照。请配置真实的非敏感只读画面地址后再接入。'}</span>
          <span>设备最近上报：{localTime(device.received_at)}</span>
        </div>
      ) : loadError ? (
        <div className="camera-empty error" role="alert">
          <strong>摄像头画面连接失败</strong><span>确认设备在线、流服务可达，并检查浏览器跨域或混合内容限制。</span>
        </div>
      ) : (
        <button
          type="button"
          className={`camera-image-frame${expanded ? ' is-expanded' : ''}`}
          aria-label={expanded ? '收起摄像头画面' : '放大摄像头画面'}
          aria-expanded={expanded}
          aria-busy={!loadedAt}
          disabled={!loadedAt}
          onClick={() => setExpanded((current) => !current)}
        >
          <img
            src={address}
            alt={`${device.display_name} 的实际摄像头画面`}
            referrerPolicy="no-referrer"
            onLoad={() => { setLoadedAt(new Date().toISOString()); setLoadError(false); }}
            onError={() => setLoadError(true)}
          />
          {!loadedAt && <span className="camera-image-loading" role="status">正在连接摄像头画面…</span>}
        </button>
      )}
      {device.connection_status === 'stale' && <p className="sensor-state-note warning">设备心跳已过期，画面连接状态不能代替设备在线确认。</p>}
      {address && loadedAt && !loadError && <p className="camera-frame-time">浏览器首次收到画面：{localTime(loadedAt)} · 设备最近上报：{localTime(device.received_at)}</p>}
    </section>
  );
}
