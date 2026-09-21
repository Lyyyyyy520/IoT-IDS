import ReactEChartsCore from 'echarts-for-react/lib/core';
import * as echarts from 'echarts/core';
import { LineChart } from 'echarts/charts';
import { GridComponent, LegendComponent, TooltipComponent } from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';
import type { TrafficResponse } from '../../api/v3Traffic';
import {
  buildTrendSeries,
  formatLocalTime,
  formatRate,
  type TrafficMetric,
} from './trafficUi';

echarts.use([LineChart, GridComponent, LegendComponent, TooltipComponent, CanvasRenderer]);

interface Props { response: TrafficResponse; metric: TrafficMetric }

export default function TrafficTrendChart({ response, metric }: Props) {
  const unit = metric === 'bytes' ? 'bytes' : 'packets';
  const txSeries = buildTrendSeries(response, metric, 'tx');
  const rxSeries = buildTrendSeries(response, metric, 'rx');
  const option = {
    animation: !window.matchMedia?.('(prefers-reduced-motion: reduce)').matches,
    grid: { left: 56, right: 18, top: 38, bottom: 45 },
    legend: { data: ['TX 上传', 'RX 下载'], textStyle: { color: '#9db4bf' } },
    tooltip: {
      trigger: 'axis',
      renderMode: 'richText',
      formatter: (items: Array<{ seriesName: string; value: [string, number | null] }>) => {
        const timestamp = items[0]?.value?.[0];
        if (!timestamp) return '';
        const parsed = new Date(timestamp);
        const lines = [
          `本地 ${formatLocalTime(timestamp)}`,
          `UTC ${Number.isNaN(parsed.getTime()) ? timestamp : parsed.toISOString()}`,
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
      axisLabel: { color: '#78919d', hideOverlap: true },
      splitLine: { show: false },
    },
    yAxis: {
      type: 'value', min: 0,
      name: metric === 'bytes' ? '字节/秒' : '包/秒',
      nameTextStyle: { color: '#78919d' },
      axisLabel: {
        color: '#78919d',
        formatter: (value: number) => metric === 'bytes'
          ? formatRate(value, 'bytes').replace('/s', '')
          : `${value}`,
      },
      splitLine: { lineStyle: { color: '#19313d' } },
    },
    series: [{
      name: 'TX 上传', type: 'line', showSymbol: false, connectNulls: false,
      lineStyle: { width: 2, color: '#52c2cc' },
      itemStyle: { color: '#52c2cc' },
      data: txSeries,
    }, {
      name: 'RX 下载', type: 'line', showSymbol: false, connectNulls: false,
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
      style={{ width: '100%', height: 300 }}
      opts={{ renderer: 'canvas' }}
    />
  );
}
