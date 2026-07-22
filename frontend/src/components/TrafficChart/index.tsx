import { useEffect, useRef } from 'react';
import * as echarts from 'echarts';

interface Props {
  showProtocol?: boolean;
  data?: { time: string; normal: number; attack: number }[];
  pieData?: { type: string; count: number }[];
}

export default function TrafficChart({ showProtocol = false, data, pieData }: Props) {
  const chartRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!chartRef.current) return;
    const chart = echarts.init(chartRef.current, 'dark');

    if (showProtocol) {
      const colorMap: Record<string, string> = {
        '正常流量': '#00CC66', 'Mirai': '#FF4444', 'Gafgyt': '#FF8800', '其他攻击': '#FFCC00',
      };
      const fallbackColors = ['#58A6FF', '#FF8800', '#39D2C0', '#FF4444'];
      const pieItems = pieData && pieData.length > 0
        ? pieData.map((d, i) => ({
            value: d.count, name: d.type,
            itemStyle: { color: colorMap[d.type] || fallbackColors[i % fallbackColors.length] }
          }))
        : [{ value: 1, name: '暂无数据', itemStyle: { color: '#484F58' } }];

      chart.setOption({
        tooltip: { trigger: 'item', formatter: '{b}: {c} 条 ({d}%)' },
        legend: { bottom: 0, textStyle: { color: '#8B949E', fontSize: 11 } },
        series: [
          {
            type: 'pie',
            radius: ['45%', '72%'],
            center: ['50%', '45%'],
            label: { show: false },
            data: pieItems,
          },
        ],
      });
    } else {
      const times = (data || []).map((d) => d.time);
      const normalData = (data || []).map((d) => d.normal);
      const attackData = (data || []).map((d) => d.attack);

      chart.setOption({
        tooltip: { trigger: 'axis' },
        legend: {
          data: ['正常流量', '攻击流量'],
          bottom: 0,
          textStyle: { color: '#8B949E' },
        },
        grid: { left: 50, right: 20, top: 20, bottom: 40 },
        xAxis: {
          type: 'category',
          data: times,
          axisLine: { lineStyle: { color: '#30363D' } },
          axisLabel: { color: '#8B949E', fontSize: 11 },
        },
        yAxis: {
          type: 'value',
          name: '包数/s',
          nameTextStyle: { color: '#8B949E' },
          axisLabel: { color: '#8B949E' },
          splitLine: { lineStyle: { color: '#21262D' } },
        },
        series: [
          {
            name: '正常流量',
            type: 'line',
            smooth: true,
            data: normalData,
            lineStyle: { color: '#00CC66', width: 2 },
            areaStyle: { color: 'rgba(0,204,102,0.08)' },
            symbol: 'none',
          },
          {
            name: '攻击流量',
            type: 'line',
            smooth: true,
            data: attackData,
            lineStyle: { color: '#FF4444', width: 2 },
            areaStyle: { color: 'rgba(255,68,68,0.10)' },
            symbol: 'none',
          },
        ],
      });
    }

    const handleResize = () => chart.resize();
    window.addEventListener('resize', handleResize);
    return () => {
      window.removeEventListener('resize', handleResize);
      chart.dispose();
    };
  }, [showProtocol, data]);

  return <div ref={chartRef} style={{ width: '100%', height: 280 }} />;
}
