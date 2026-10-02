import type {
  ProtocolAggregate,
  TrafficPoint,
  TrafficResolution,
  TrafficResponse,
} from '../../api/v3Traffic';

export type TrafficMetric = 'bytes' | 'packets';
export type ProtocolMetric = 'bytes' | 'packets' | 'flows';

export const RESOLUTION_SECONDS: Record<Exclude<TrafficResolution, 'auto'>, number> = {
  minute: 60,
  '5minute': 300,
  hour: 3600,
};

export function formatRate(value: number, unit: 'bytes' | 'packets' | 'flows'): string {
  if (!Number.isFinite(value) || value < 0) return '—';
  if (unit !== 'bytes') {
    const label = unit === 'packets' ? '包/秒' : '流/秒';
    return `${value < 10 ? value.toFixed(2) : value.toFixed(1)} ${label}`;
  }
  const units = ['B/s', 'KiB/s', 'MiB/s', 'GiB/s'];
  let current = value;
  let index = 0;
  while (current >= 1024 && index < units.length - 1) {
    current /= 1024;
    index += 1;
  }
  return `${current < 10 ? current.toFixed(2) : current.toFixed(1)} ${units[index]}`;
}

export function formatCount(value: number, unit: ProtocolMetric): string {
  return `${new Intl.NumberFormat('zh-CN').format(value)} ${
    unit === 'bytes' ? 'B' : unit === 'packets' ? '包' : '流'
  }`;
}

export function formatLocalTime(value: string | null, includeDate = true): string {
  if (!value) return '未知';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '时间无效';
  return new Intl.DateTimeFormat('zh-CN', {
    ...(includeDate ? { month: '2-digit', day: '2-digit' } : {}),
    hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
  }).format(date);
}

export function bucketRate(
  point: TrafficPoint,
  resolution: TrafficResponse['resolution'],
  metric: TrafficMetric,
) {
  const seconds = RESOLUTION_SECONDS[resolution];
  return {
    bucket_start: point.bucket_start,
    tx: metric === 'bytes' ? point.tx_bytes / seconds : point.tx_packets / seconds,
    rx: metric === 'bytes' ? point.rx_bytes / seconds : point.rx_packets / seconds,
  };
}

export function buildTrendData(
  response: TrafficResponse | null,
  metric: TrafficMetric,
) {
  if (!response) return [];
  // Deliberately map only server-provided buckets. Missing intervals remain
  // gaps rather than invented zero traffic.
  return response.series.map((point) => bucketRate(point, response.resolution, metric));
}

export function buildTrendSeries(
  response: TrafficResponse,
  metric: TrafficMetric,
  direction: 'tx' | 'rx',
): Array<[string, number | null]> {
  const points = buildTrendData(response, metric);
  const bucketMilliseconds = RESOLUTION_SECONDS[response.resolution] * 1_000;
  const series: Array<[string, number | null]> = [];
  points.forEach((point, index) => {
    const previous = points[index - 1];
    if (previous) {
      const previousTime = Date.parse(previous.bucket_start);
      const currentTime = Date.parse(point.bucket_start);
      if (currentTime - previousTime > bucketMilliseconds * 1.5) {
        series.push([new Date(previousTime + bucketMilliseconds).toISOString(), null]);
      }
    }
    series.push([point.bucket_start, point[direction]]);
  });
  return series;
}

export function protocolValue(protocol: ProtocolAggregate, metric: ProtocolMetric): number {
  if (metric === 'bytes') return protocol.tx_bytes + protocol.rx_bytes;
  if (metric === 'packets') return protocol.tx_packets + protocol.rx_packets;
  return protocol.tx_flows + protocol.rx_flows;
}

export function protocolBreakdown(protocols: ProtocolAggregate[], metric: ProtocolMetric) {
  const values = protocols.map((protocol) => ({
    ...protocol,
    value: protocolValue(protocol, metric),
  }));
  const total = values.reduce((sum, item) => sum + item.value, 0);
  return values.map((item) => ({
    ...item,
    percentage: total > 0 ? item.value / total * 100 : null,
  }));
}

export type FreshnessKind = 'unknown' | 'fresh' | 'stale';
export function freshnessKind(
  latestSampleAt: string | null,
  now: Date,
  staleAfterMs = 15_000,
): FreshnessKind {
  if (!latestSampleAt) return 'unknown';
  const sampleTime = Date.parse(latestSampleAt);
  if (!Number.isFinite(sampleTime)) return 'unknown';
  return now.getTime() - sampleTime > staleAfterMs ? 'stale' : 'fresh';
}

export function trafficErrorMessage(error: unknown): string {
  if (!error || typeof error !== 'object' || !('kind' in error)) return '流量请求失败，请稍后重试。';
  const kind = (error as { kind: string }).kind;
  if (kind === 'unauthorized') return '登录已失效，请重新登录。';
  if (kind === 'forbidden') return '当前账号没有设备流量查看权限。';
  if (kind === 'not_found') return '设备不存在或已被删除。';
  if (kind === 'unavailable') return '数据库或流量聚合服务尚未准备。';
  if (kind === 'network') return '实时连接失败，保留最后一次真实数据。';
  if (kind === 'invalid_response') return '流量服务响应格式异常，数据未进入图表。';
  if (kind === 'bad_request') return '流量查询范围或筛选参数无效。';
  return '流量请求失败，请稍后重试。';
}
