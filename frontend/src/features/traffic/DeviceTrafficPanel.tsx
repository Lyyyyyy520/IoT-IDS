import { lazy, Suspense, useMemo, useState } from 'react';
import type { DeviceDetail } from '../../api/v3Devices';
import type { PeerItem } from '../../api/v3Traffic';
import { useDeviceTraffic, TRAFFIC_RANGES, type TrafficRangeKey } from './useDeviceTraffic';
import {
  RESOLUTION_SECONDS,
  buildTrendData,
  formatCount,
  formatLocalTime,
  formatRate,
  freshnessKind,
  protocolBreakdown,
  trafficErrorMessage,
  type ProtocolMetric,
  type TrafficMetric,
} from './trafficUi';

const TrafficTrendChart = lazy(() => import('./TrafficTrendChart'));

interface Props {
  device: DeviceDetail;
  isAdmin: boolean;
  active: boolean;
  onSelectPeer: (deviceId: string) => void;
}

function errorWithRequest(error: unknown): string {
  const base = trafficErrorMessage(error);
  if (error && typeof error === 'object' && 'requestId' in error) {
    const requestId = (error as { requestId?: string | null }).requestId;
    if (requestId) return `${base} 请求 ID：${requestId}`;
  }
  return base;
}

function gapCount(points: { bucket_start: string }[], seconds: number): number {
  let gaps = 0;
  for (let index = 1; index < points.length; index += 1) {
    const previous = Date.parse(points[index - 1].bucket_start);
    const current = Date.parse(points[index].bucket_start);
    if (current - previous > seconds * 1_500) gaps += 1;
  }
  return gaps;
}

function peerLabel(peer: PeerItem): string {
  return peer.peer_device_id ?? '外部通信对象';
}

export default function DeviceTrafficPanel({ device, isAdmin, active, onSelectPeer }: Props) {
  const traffic = useDeviceTraffic({ deviceId: device.device_id, active });
  const [trendMetric, setTrendMetric] = useState<TrafficMetric>('bytes');
  const [protocolMetric, setProtocolMetric] = useState<ProtocolMetric>('bytes');
  const realtime = traffic.realtime?.realtime ?? null;
  const latestSample = traffic.realtime?.freshness.latest_sample_at
    ?? traffic.history?.freshness.latest_sample_at
    ?? null;
  const freshness = freshnessKind(latestSample, new Date());
  const historyPoints = buildTrendData(traffic.history, trendMetric);
  const protocolRows = useMemo(
    () => protocolBreakdown(traffic.history?.protocols ?? [], protocolMetric),
    [protocolMetric, traffic.history?.protocols],
  );
  const gaps = traffic.history
    ? gapCount(traffic.history.series, RESOLUTION_SECONDS[traffic.history.resolution])
    : 0;
  const dataQuality = traffic.history?.data_quality;
  const staleBecauseRefreshFailed = Boolean(traffic.realtimeError || traffic.historyError);

  const metricValue = (kind: 'tx' | 'rx' | 'packets' | 'flows') => {
    if (!realtime?.available) return '—';
    if (kind === 'tx') return formatRate(realtime.tx_bytes_per_second, 'bytes');
    if (kind === 'rx') return formatRate(realtime.rx_bytes_per_second, 'bytes');
    if (kind === 'packets') {
      return formatRate(
        realtime.tx_packets_per_second + realtime.rx_packets_per_second,
        'packets',
      );
    }
    return formatRate(
      realtime.tx_flows_per_second + realtime.rx_flows_per_second,
      'flows',
    );
  };

  return (
    <div className="traffic-workspace" aria-label={`${device.display_name} 流量分析`}>
      {device.lifecycle_status === 'retired' && (
        <div className="traffic-history-banner" role="status">
          该设备已退役；以下为按稳定 device_id 保留的历史流量，不代表设备当前在线。
        </div>
      )}
      {(staleBecauseRefreshFailed || freshness === 'stale') && latestSample && (
        <div className="traffic-stale-banner" role="status">
          数据可能已过期；最后真实样本：{formatLocalTime(latestSample)}。
          {staleBecauseRefreshFailed ? '刷新失败，页面保留最后一次真实结果。' : ''}
        </div>
      )}
      {traffic.realtimeError && (
        <div className="traffic-inline-error" role="alert">{errorWithRequest(traffic.realtimeError)}</div>
      )}

      <section className="traffic-metric-section" aria-labelledby="realtime-metrics-title">
        <div className="traffic-section-heading">
          <div>
            <h3 id="realtime-metrics-title">短期实时窗口</h3>
            <span>
              {realtime?.available
                ? `真实窗口 ${realtime.window_seconds} 秒`
                : realtime?.readiness === 'warming_up'
                  ? '正在积累实时窗口'
                  : realtime?.reason ?? '实时窗口尚不可用'}
            </span>
          </div>
          <button type="button" className="devices-button ghost" onClick={traffic.refreshAll}>
            手动刷新
          </button>
        </div>
        <div className="traffic-metric-grid">
          <div><span>当前上传速率</span><strong>{metricValue('tx')}</strong></div>
          <div><span>当前下载速率</span><strong>{metricValue('rx')}</strong></div>
          <div><span>当前包速率</span><strong>{metricValue('packets')}</strong></div>
          <div><span>当前流速率</span><strong>{metricValue('flows')}</strong></div>
          <div><span>最新样本</span><strong>{formatLocalTime(latestSample)}</strong></div>
          <div>
            <span>数据新鲜度</span>
            <strong className={`freshness-${freshness}`}>
              {freshness === 'fresh' ? '新鲜' : freshness === 'stale' ? '已过期' : '未知'}
            </strong>
          </div>
        </div>
        {!realtime?.available && (
          <div className="traffic-empty-inline">
            {traffic.realtimeLoading && !traffic.realtime
              ? '正在读取短期实时窗口…'
              : traffic.realtime?.availability.reason === 'no_samples'
              ? '尚未收到流量样本。'
              : realtime?.readiness === 'warming_up'
                ? '正在积累实时窗口；未知值不会显示为 0 B/s。'
                : `实时窗口不可用：${realtime?.reason ?? '服务尚未提供原因'}`}
          </div>
        )}
      </section>

      <section className="traffic-analysis-section" aria-labelledby="history-title">
        <div className="traffic-section-heading traffic-filter-heading">
          <div><h3 id="history-title">历史趋势</h3><span>仅绘制后端返回的时间桶，缺失时间不会补零。</span></div>
          <div className="traffic-controls">
            <label>时间范围
              <select
                aria-label="流量时间范围"
                value={traffic.rangeKey}
                onChange={(event) => traffic.setRange(event.target.value as TrafficRangeKey)}
              >
                {TRAFFIC_RANGES.map((range) => (
                  <option key={range.key} value={range.key}>{range.label}</option>
                ))}
              </select>
            </label>
            <div className="segmented-control" role="group" aria-label="趋势指标">
              <button type="button" aria-pressed={trendMetric === 'bytes'} onClick={() => setTrendMetric('bytes')}>字节</button>
              <button type="button" aria-pressed={trendMetric === 'packets'} onClick={() => setTrendMetric('packets')}>包数</button>
            </div>
          </div>
        </div>
        {traffic.historyError && (
          <div className="traffic-inline-error" role="alert">{errorWithRequest(traffic.historyError)}</div>
        )}
        {traffic.historyLoading && !traffic.history && <div className="traffic-loading">正在读取历史分钟聚合…</div>}
        {traffic.history && !traffic.history.availability.available ? (
          <div className="traffic-empty-state">
            <strong>{traffic.history.availability.reason === 'no_samples' ? '尚未收到流量样本' : '历史聚合不可用'}</strong>
            <span>{traffic.history.availability.reason === 'no_samples'
              ? '当前窗口没有可绘制的真实聚合数据。'
              : `服务原因：${traffic.history.availability.reason ?? '未提供'}`}</span>
          </div>
        ) : traffic.history && historyPoints.length ? (
          <>
            <div className="traffic-chart-shell" data-testid="traffic-chart-data" data-points={historyPoints.length}>
              <Suspense fallback={<div className="traffic-loading">正在加载图表模块…</div>}>
                <TrafficTrendChart response={traffic.history} metric={trendMetric} />
              </Suspense>
            </div>
            <div className="traffic-table-wrap">
              <table className="traffic-data-table">
                <caption>服务器返回的时间桶可读摘要</caption>
                <thead><tr><th>本地时间</th><th>TX</th><th>RX</th></tr></thead>
                <tbody>
                  {historyPoints.slice(-12).map((point) => (
                    <tr key={point.bucket_start}>
                      <td title={point.bucket_start}>{formatLocalTime(point.bucket_start)}</td>
                      <td>{formatRate(point.tx, trendMetric)}</td>
                      <td>{formatRate(point.rx, trendMetric)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        ) : null}
      </section>

      <section className="traffic-analysis-section" aria-labelledby="protocol-title">
        <div className="traffic-section-heading">
          <div><h3 id="protocol-title">协议构成</h3><span>占比仅基于当前查询窗口真实聚合。</span></div>
          <div className="segmented-control" role="group" aria-label="协议统计指标">
            {(['bytes', 'packets', 'flows'] as ProtocolMetric[]).map((metric) => (
              <button
                key={metric}
                type="button"
                aria-pressed={protocolMetric === metric}
                onClick={() => setProtocolMetric(metric)}
              >{metric === 'bytes' ? '字节' : metric === 'packets' ? '包数' : '流数'}</button>
            ))}
          </div>
        </div>
        {protocolRows.length && protocolRows.some((row) => row.value > 0) ? (
          <div className="protocol-list">
            {protocolRows.map((row) => (
              <div className="protocol-row" key={`${row.protocol}-${row.evidence}`}>
                <div>
                  <strong>{row.protocol}</strong>
                  <span>{row.evidence === 'inferred_application_protocol' ? '推断协议 · inferred' : '已确认网络协议'}</span>
                </div>
                <div className="protocol-value">
                  <strong>{formatCount(row.value, protocolMetric)}</strong>
                  <span>{row.percentage === null ? '占比未知' : `${row.percentage.toFixed(1)}%`}</span>
                </div>
                <div className="protocol-bar" aria-hidden="true"><span style={{ width: `${row.percentage ?? 0}%` }} /></div>
              </div>
            ))}
          </div>
        ) : <div className="traffic-empty-inline">当前窗口没有协议聚合，不显示空的 100% 图形。</div>}
      </section>

      {(dataQuality?.has_unassigned_or_missing_data || gaps > 0 || realtime?.readiness === 'warming_up') && (
        <section className="traffic-quality-note" aria-labelledby="quality-title">
          <h3 id="quality-title">数据质量提示</h3>
          <p>统计可能不完整；该提示不是安全告警，也不代表 GNN 结论。</p>
          <ul>
            {dataQuality && dataQuality.unassigned_samples_in_window > 0 && (
              <li>窗口内有 {dataQuality.unassigned_samples_in_window} 个未归属样本，可能来自 IP 冲突或身份不确定。</li>
            )}
            {dataQuality?.note && <li>后端数据说明：{dataQuality.note}</li>}
            {gaps > 0 && <li>后端返回的时间桶中检测到 {gaps} 处缺口，前端未补零。</li>}
            {realtime?.readiness === 'warming_up' && <li>后端实时窗口可能刚重启，正在重新积累。</li>}
          </ul>
        </section>
      )}

      <section className="traffic-analysis-section" aria-labelledby="peers-title">
        <div className="traffic-section-heading traffic-filter-heading">
          <div><h3 id="peers-title">通信对象</h3><span>按 bytes/packets 排序的聚合对象，不包含报文载荷。</span></div>
          <div className="traffic-controls peer-controls">
            <label>方向
              <select aria-label="通信方向" value={traffic.peerDirection} onChange={(event) => traffic.setPeerDirection(event.target.value as 'all' | 'tx' | 'rx')}>
                <option value="all">全部</option><option value="tx">TX</option><option value="rx">RX</option>
              </select>
            </label>
            <label>协议
              <select aria-label="通信协议" value={traffic.peerProtocol} onChange={(event) => traffic.setPeerProtocol(event.target.value)}>
                <option value="">全部</option>
                {(traffic.history?.protocols ?? []).map((protocol) => (
                  <option key={protocol.protocol} value={protocol.protocol}>{protocol.protocol}</option>
                ))}
              </select>
            </label>
            <label>排序
              <select aria-label="通信对象排序" value={traffic.peerSort} onChange={(event) => traffic.setPeerSort(event.target.value as 'bytes' | 'packets')}>
                <option value="bytes">字节</option><option value="packets">包数</option>
              </select>
            </label>
          </div>
        </div>
        {traffic.peersError && <div className="traffic-inline-error" role="alert">{errorWithRequest(traffic.peersError)}</div>}
        {traffic.peersLoading && !traffic.peers && <div className="traffic-loading">正在读取通信对象…</div>}
        {traffic.peers && !traffic.peers.availability.available ? (
          <div className="traffic-empty-inline">
            {traffic.peers.availability.reason === 'no_samples'
              ? '当前窗口没有通信对象样本。'
              : `通信对象聚合不可用：${traffic.peers.availability.reason ?? '未提供原因'}`}
          </div>
        ) : traffic.peers ? (
          <>
            <div className="peer-list">
              {traffic.peers.peers.map((peer, index) => (
                <article className="peer-row" key={`${peer.peer_device_id ?? peer.peer_ip ?? 'hidden'}-${peer.direction}-${peer.protocol}-${index}`}>
                  <div className="peer-identity">
                    {peer.peer_device_id ? (
                      <button type="button" onClick={() => onSelectPeer(peer.peer_device_id as string)}>{peerLabel(peer)}</button>
                    ) : <strong>外部通信对象</strong>}
                    {isAdmin && peer.peer_ip_visible && peer.peer_ip && <span>{peer.peer_ip}</span>}
                  </div>
                  <span>{peer.direction.toUpperCase()}</span><span>{peer.protocol}</span>
                  <span>{formatCount(peer.bytes, 'bytes')}</span><span>{formatCount(peer.packets, 'packets')}</span><span>{formatCount(peer.flows, 'flows')}</span>
                  <small title={peer.first_seen}>{formatLocalTime(peer.first_seen)} → {formatLocalTime(peer.last_seen)}</small>
                </article>
              ))}
            </div>
            <div className="peer-pagination">
              <span>{traffic.peers.pagination.total} 个聚合对象</span>
              <button type="button" className="devices-button ghost" disabled={traffic.peerOffset === 0} onClick={() => traffic.setPeerOffset(Math.max(0, traffic.peerOffset - 25))}>上一页</button>
              <button type="button" className="devices-button ghost" disabled={!traffic.peers.pagination.has_more} onClick={() => traffic.setPeerOffset(traffic.peerOffset + 25)}>下一页</button>
            </div>
          </>
        ) : null}
      </section>
    </div>
  );
}

export { gapCount };
