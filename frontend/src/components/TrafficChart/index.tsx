import { useEffect, useRef } from 'react';
import * as echarts from 'echarts';

interface Props {
  showProtocol?: boolean;
  data?: { time: string; normal: number; attack: number }[];
}

export default function TrafficChart({ showProtocol = false, data }: Props) {
  const chartRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!chartRef.current) return;
    const chart = echarts.init(chartRef.current, 'dark');

    if (showProtocol) {
      chart.setOption({
        tooltip: { trigger: 'item' },
        legend: { bottom: 0, textStyle: { color: '#8B949E', fontSize: 11 } },
        series: [
          {
            type: 'pie',
            radius: ['45%', '72%'],
            center: ['50%', '45%'],
            label: { show: false },
            data: [
              { value: 45, name: 'HTTP', itemStyle: { color: '#58A6FF' } },
              { value: 22, name: 'MQTT', itemStyle: { color: '#39D2C0' } },
              { value: 15, name: 'DNS', itemStyle: { color: '#BC8CFF' } },
              { value: 10, name: 'CoAP', itemStyle: { color: '#FF8800' } },
              { value: 8, name: '其他', itemStyle: { color: '#484F58' } },
            ],
          },
        ],
      });
    } else {
      const hasData = data && data.length > 0;
      const times = hasData ? data!.map((d) => d.time) : ['13:00', '13:15', '13:30', '13:45', '14:00', '14:15', '14:30'];
      const normalData = hasData ? data!.map((d) => d.normal) : [1200, 1350, 1180, 1420, 1280, 1390, 1450];
      const attackData = hasData ? data!.map((d) => d.attack) : [20, 15, 45, 38, 23, 55, 42];

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
