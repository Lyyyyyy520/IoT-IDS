import React, { useMemo, useState } from 'react';
import { Text, TouchableOpacity, View, useWindowDimensions } from 'react-native';
import type { MobileDeviceHistory } from './api';
import { palette, ui } from './ui';

type Metric = { key: 'temp' | 'humidity'; title: string; unit: string; color: string };
const metrics: Metric[] = [
  { key: 'temp', title: '温度', unit: '°C', color: palette.green },
  { key: 'humidity', title: '湿度', unit: '%RH', color: palette.amber },
];
const timeLabel = (value: string) => new Date(value).toLocaleString([], {
  month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit',
});
const valueLabel = (value: number) => Number.isInteger(value) ? String(value) : value.toFixed(1);

function MetricChart({ metric, history }: { metric: Metric; history: MobileDeviceHistory }) {
  const { width: screenWidth } = useWindowDimensions();
  const [selected, setSelected] = useState<number | null>(null);
  const points = useMemo(() => history.series.flatMap(item => {
    const value = item.telemetry[metric.key];
    return typeof value === 'number' && Number.isFinite(value)
      ? [{ at: item.observed_at, receivedAt: item.received_at, value }] : [];
  }), [history.series, metric.key]);
  const plotWidth = Math.max(220, screenWidth - 108);
  const plotHeight = 142;
  const padLeft = 46, padRight = 12, padTop = 12, padBottom = 26;
  const min = points.length ? Math.min(...points.map(point => point.value)) : 0;
  const max = points.length ? Math.max(...points.map(point => point.value)) : 1;
  const spread = max - min || Math.max(Math.abs(max) * 0.1, 1);
  const xAt = (index: number) => padLeft + (points.length <= 1 ? 0.5 : index / (points.length - 1)) * (plotWidth - padLeft - padRight);
  const yAt = (value: number) => padTop + (1 - ((value - min) / spread)) * (plotHeight - padTop - padBottom);
  let gapCount = 0;
  const segments = points.slice(1).flatMap((point, offset) => {
    const previous = points[offset];
    if ((Date.parse(point.receivedAt) - Date.parse(previous.receivedAt)) / 1000 > history.bucket_seconds * 2) {
      gapCount += 1;
      return [];
    }
    const x1 = xAt(offset), y1 = yAt(previous.value), x2 = xAt(offset + 1), y2 = yAt(point.value);
    const dx = x2 - x1, dy = y2 - y1, length = Math.sqrt(dx * dx + dy * dy);
    const angle = Math.atan2(dy, dx);
    return [<View key={`${previous.at}:${point.at}`} accessible={false} style={{ position: 'absolute',
      left: (x1 + x2 - length) / 2, top: (y1 + y2) / 2 - 1, width: length, height: 2,
      backgroundColor: metric.color, transform: [{ rotateZ: `${angle}rad` }] }} />];
  });
  const selectedPoint = selected === null ? null : points[selected] ?? null;

  return <View style={ui.card}>
    <Text style={ui.cardTitle}>{metric.title}趋势（{metric.unit}）</Text>
    {points.length === 0 ? <Text style={ui.muted}>所选范围没有可用的{metric.title}历史数据。</Text> : <>
      <View accessibilityLabel={`${metric.title}历史折线图，单位${metric.unit}`} style={{ height: plotHeight, width: plotWidth,
        alignSelf: 'center', borderLeftWidth: 1, borderBottomWidth: 1, borderColor: palette.border }}>
        <Text style={{ position: 'absolute', left: 0, top: 2, color: palette.muted, fontSize: 14 }}>{valueLabel(max)}</Text>
        <Text style={{ position: 'absolute', left: 0, bottom: padBottom - 4, color: palette.muted, fontSize: 14 }}>{valueLabel(min)}</Text>
        {segments}
        {points.map((point, index) => <TouchableOpacity key={`${point.at}:${index}`} accessibilityRole="button"
          accessibilityLabel={`${metric.title} ${valueLabel(point.value)} ${metric.unit}，观测时间 ${timeLabel(point.at)}，收到时间 ${timeLabel(point.receivedAt)}`}
          accessibilityState={{ selected: selected === index }} onPress={() => setSelected(index)}
          style={{ position: 'absolute', left: xAt(index) - 8, top: yAt(point.value) - 8, width: 16, height: 16,
            borderRadius: 8, borderWidth: 2, borderColor: metric.color, backgroundColor: palette.card }} />)}
      </View>
      <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignSelf: 'center', width: plotWidth }}>
        <Text style={ui.muted}>{timeLabel(points[0].at)}</Text>
        <Text style={ui.muted}>{timeLabel(points[points.length - 1].at)}</Text>
      </View>
      {selectedPoint && <Text accessibilityLiveRegion="polite" style={ui.body}>
        {metric.title} {valueLabel(selectedPoint.value)} {metric.unit} · 观测 {timeLabel(selectedPoint.at)} · 接收 {timeLabel(selectedPoint.receivedAt)}
      </Text>}
      <Text style={ui.muted}>{points.length} 个真实采样点{history.truncated ? '（已按时间采样压缩显示）' : ''}。点按数据点查看读数和时间。</Text>
      {gapCount > 0 && <Text style={ui.warning}>折线断开处有 {gapCount} 段没有留存采样数据。</Text>}
    </>}
  </View>;
}

export default function MobileSensorCharts({ history }: { history: MobileDeviceHistory }) {
  return <>
    {metrics.map(metric => <MetricChart key={metric.key} metric={metric} history={history} />)}
  </>;
}
