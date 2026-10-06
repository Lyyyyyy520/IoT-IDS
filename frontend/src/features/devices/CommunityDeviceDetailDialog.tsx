import { useEffect, useRef, useState } from 'react';
import { DeviceApiError, v3DevicesApi, type DeviceDetail } from '../../api/v3Devices';
import type { MonitorDevice } from '../../api/v3Monitor';
import { statusLabel } from '../monitor/MonitorPanels';
import CameraPreviewPanel from './CameraPreviewPanel';
import DeviceObservedStatePanel from './DeviceObservedStatePanel';
import SensorHistoryPanel from './SensorHistoryPanel';
import './community-device-detail.css';

function localTime(value: string | null | undefined): string {
  if (!value) return '暂无记录';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
  }).format(parsed);
}

function deviceError(error: unknown): string {
  if (!(error instanceof DeviceApiError)) return error instanceof Error ? error.message : '设备详情读取失败';
  if (error.kind === 'unauthorized') return '登录状态已失效，请重新登录后重试。';
  if (error.kind === 'forbidden') return '当前账号没有读取该设备详情的权限。';
  if (error.kind === 'unavailable') return '设备数据库或详情接口暂不可用。';
  if (error.kind === 'not_found') return '设备档案不存在或已不可用。';
  return error.message;
}

function modeLabel(mode: DeviceDetail['operation_mode']): string {
  if (mode === 'maintenance') return '维护中';
  if (mode === 'disabled') return '已停用';
  return '运行中';
}

export default function CommunityDeviceDetailDialog({
  deviceId,
  summary,
  onClose,
}: {
  deviceId: string | null;
  summary: MonitorDevice | null;
  onClose: () => void;
}) {
  const [detail, setDetail] = useState<DeviceDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const closeButton = useRef<HTMLButtonElement>(null);
  const previousFocus = useRef<HTMLElement | null>(null);
  const matchedDetail = detail?.device_id === deviceId ? detail : null;
  const matchedSummary = summary?.device_id === deviceId ? summary : null;

  useEffect(() => {
    if (!deviceId) return undefined;
    const controller = new AbortController();
    setDetail(null);
    setError(null);
    setLoading(true);
    void v3DevicesApi.getDevice(deviceId, controller.signal)
      .then((result) => {
        if (!controller.signal.aborted && result.device_id === deviceId) setDetail(result);
      })
      .catch((caught: unknown) => {
        if (!controller.signal.aborted) setError(deviceError(caught));
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [deviceId]);

  useEffect(() => {
    if (!deviceId) return undefined;
    const previousOverflow = document.body.style.overflow;
    previousFocus.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    document.body.style.overflow = 'hidden';
    closeButton.current?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        onClose();
        return;
      }
      if (event.key !== 'Tab') return;
      const dialog = document.querySelector<HTMLElement>('.community-device-dialog');
      const focusable = dialog?.querySelectorAll<HTMLElement>(
        'button:not(:disabled), a[href], input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex="-1"])',
      );
      if (!focusable?.length) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => {
      document.body.style.overflow = previousOverflow;
      window.removeEventListener('keydown', onKeyDown);
      previousFocus.current?.focus();
    };
  }, [deviceId, onClose]);

  if (!deviceId) return null;
  const displayName = matchedDetail?.display_name ?? matchedSummary?.display_name ?? deviceId;
  const type = matchedDetail?.device_type ?? matchedSummary?.device_type ?? null;
  const connection = matchedDetail?.connection_status ?? matchedSummary?.connection_status ?? null;

  return (
    <div
      className="community-device-overlay"
      role="presentation"
      onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}
    >
      <section className="community-device-dialog" role="dialog" aria-modal="true" aria-labelledby="community-device-title">
        <header className="community-device-dialog-heading">
          <div>
            <p className="eyebrow">COMMUNITY DEVICE DETAIL</p>
            <h2 id="community-device-title">{displayName}</h2>
            <span className="community-device-id">{deviceId}</span>
          </div>
          <button ref={closeButton} type="button" className="community-device-close" onClick={onClose} aria-label="关闭设备详情">×</button>
        </header>

        {loading && <div className="community-device-message" role="status">正在读取设备档案…</div>}
        {error && <div className="community-device-message error" role="alert">{error}</div>}

        {matchedDetail ? (
          <>
            <div className="community-device-status-strip">
              <span className={`status-pill status-${matchedDetail.connection_status}`}>
                {statusLabel(matchedDetail.connection_status)}
              </span>
              <span className={`mode-pill mode-${matchedDetail.operation_mode}`}>{modeLabel(matchedDetail.operation_mode)}</span>
              {matchedDetail.lifecycle_status === 'retired' && <span className="retired-pill">已退役</span>}
            </div>
            {matchedDetail.connection_status === 'offline' && <p className="community-device-state-note error" role="status">设备当前离线。下面的数据均为最近一次保留的设备上报，不代表实时状态。</p>}
            {matchedDetail.connection_status === 'stale' && <p className="community-device-state-note warning" role="status">设备上报已过期；最近读数与状态可能延迟。</p>}
            {matchedDetail.connection_status === 'unknown' && <p className="community-device-state-note warning" role="status">设备连接状态尚未确认。</p>}

            <dl className="community-device-profile-grid">
              <div><dt>设备类型</dt><dd>{matchedDetail.device_type}</dd></div>
              <div><dt>所属区域</dt><dd>{matchedDetail.area_id ?? '数据源未提供'}</dd></div>
              <div><dt>最近上报</dt><dd>{localTime(matchedDetail.received_at)}</dd></div>
              <div><dt>设备观测时间</dt><dd>{localTime(matchedDetail.observed_at)}</dd></div>
              <div><dt>当前 IP</dt><dd>{matchedDetail.ip_address ?? '数据源未提供'}</dd></div>
              <div><dt>MAC 地址</dt><dd>{matchedDetail.mac_address || '数据源未提供'}</dd></div>
              <div><dt>固件版本</dt><dd>{matchedDetail.firmware_version ?? '设备未上报'}</dd></div>
              <div><dt>运行时长</dt><dd>{matchedDetail.uptime_ms === null ? '设备未上报' : `${Math.floor(matchedDetail.uptime_ms / 1000)} 秒`}</dd></div>
              <div><dt>档案来源</dt><dd>{matchedDetail.profile_source}</dd></div>
              <div><dt>状态版本</dt><dd>{matchedDetail.state_version}</dd></div>
              <div className="community-device-profile-wide"><dt>观测来源</dt><dd>{matchedDetail.sources.length ? matchedDetail.sources.join('、') : '尚无来源记录'}</dd></div>
              <div className="community-device-profile-wide"><dt>档案更新时间</dt><dd>{localTime(matchedDetail.updated_at)}</dd></div>
            </dl>

            {type?.toLowerCase() === 'temp_sensor' ? (
              <SensorHistoryPanel key={matchedDetail.device_id} device={matchedDetail} />
            ) : (
              <DeviceObservedStatePanel key={matchedDetail.device_id} device={matchedDetail} />
            )}
            {type?.toLowerCase() === 'camera' && <CameraPreviewPanel key={`camera-${matchedDetail.device_id}`} device={matchedDetail} />}
          </>
        ) : !loading && !error ? (
          <div className="community-device-message" role="status">
            {matchedSummary
              ? `已选择 ${matchedSummary.display_name}，正在加载此设备的完整档案。`
              : '正在等待与所选设备对应的档案信息。'}
          </div>
        ) : null}
      </section>
    </div>
  );
}
