import ReactEChartsCore from 'echarts-for-react/lib/core';
import * as echarts from 'echarts/core';
import { LineChart } from 'echarts/charts';
import { GridComponent, LegendComponent, TooltipComponent } from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';
import type { TrafficResponse } from '../../api/v3Traffic';
import {
  buildRealtimeTrendSeries,
  buildTrendSeries,
  formatLocalTime,
  formatRate,
  type TrafficRateSample,
  type TrafficMetric,
} from './trafficUi';

echarts.use([LineChart, GridComponent, LegendComponent, TooltipComponent, CanvasRenderer]);

interface Props {
  metric: TrafficMetric;
  response?: TrafficResponse;
  realtimeSamples?: TrafficRateSample[];
}

export default function TrafficTrendChart({ response, realtimeSamples, metric }: Props) {
  const unit = metric === 'bytes' ? 'bytes' : 'packets';
  const txSeries = response
    ? buildTrendSeries(response, metric, 'tx')
    : buildRealtimeTrendSeries(realtimeSamples ?? [], metric, 'tx');
  const rxSeries = response
    ? buildTrendSeries(response, metric, 'rx')
    : buildRealtimeTrendSeries(realtimeSamples ?? [], metric, 'rx');
  const option = {
    animation: !window.matchMedia?.('(prefers-reduced-motion: reduce)').matches,
    grid: { left: 96, right: 24, top: 52, bottom: 68 },
    legend: { data: ['TX 上传', 'RX 下载'], textStyle: { color: '#9db4bf', fontSize: 16 } },
    tooltip: {
      trigger: 'axis',
      renderMode: 'richText',
      textStyle: { fontSize: 16, lineHeight: 24 },
      formatter: (items: Array<{ seriesName: string; value: [string | number, number | null] }>) => {
        const timestamp = items[0]?.value?.[0];
        if (!timestamp) return '';
        const timestampText = typeof timestamp === 'number'
          ? new Date(timestamp).toISOString()
          : timestamp;
        const parsed = new Date(timestampText);
        const lines = [
          `本地 ${formatLocalTime(timestampText)}`,
          `UTC ${Number.isNaN(parsed.getTime()) ? timestampText : parsed.toISOString()}`,
        ];
        items.forEach((item) => {
          if (item.value[1] !== null) {
            lines.push(`${item.seriesName}  ${formatRate(item.value[1], unit)}`);
          }
        });
        return lines.join('\n');
      },
      axisPointer: { type: 'line' },
    },
    xAxis: {
      type: 'time',
      axisLine: { lineStyle: { color: '#31505d' } },
      axisLabel: { color: '#78919d', fontSize: 16, hideOverlap: true },
      splitLine: { show: false },
    },
    yAxis: {
      type: 'value', min: 0,
      name: metric === 'bytes' ? '字节/秒' : '包/秒',
      nameTextStyle: { color: '#78919d', fontSize: 16 },
      axisLabel: {
        color: '#78919d',
        fontSize: 16,
        formatter: (value: number) => metric === 'bytes'
          ? formatRate(value, 'bytes').replace('/s', '')
          : `${value}`,
      },
      splitLine: { lineStyle: { color: '#19313d' } },
    },
    series: [{
      name: 'TX 上传', type: 'line', showSymbol: !response, connectNulls: false,
      lineStyle: { width: 2, color: '#52c2cc' },
      itemStyle: { color: '#52c2cc' },
      data: txSeries,
    }, {
      name: 'RX 下载', type: 'line', showSymbol: !response, connectNulls: false,
      lineStyle: { width: 2, color: '#8faee8' },
      itemStyle: { color: '#8faee8' },
      data: rxSeries,
    }],
  };
  return (
    <ReactEChartsCore
      echarts={echarts}
      option={option}
      notMerge
      lazyUpdate
      style={{ width: '100%', height: 340, minHeight: 340 }}
      opts={{ renderer: 'canvas' }}
    />
  );
}
