import { useEffect, useRef } from 'react';
import * as echarts from 'echarts';

interface Props {
  data?: { data: [number, number, number][]; days: string[]; hours: string[] } | null;
}

export default function Heatmap({ data }: Props) {
  const chartRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!chartRef.current) return;
    const chart = echarts.init(chartRef.current, 'dark');

    const days = data?.days ?? ['周一','周二','周三','周四','周五','周六','周日'];
    const hours = data?.hours ?? ['00','02','04','06','08','10','12','14','16','18','20','22'];
    const heatData = data?.data ?? [];

    // Generate fallback data if empty
    let finalData: [number, number, number][] = heatData as [number, number, number][];
    if (finalData.length === 0) {
      for (let d = 0; d < 7; d++) {
        for (let h = 0; h < 12; h++) {
          let value = Math.floor(Math.random() * 30);
          if (h < 2 || h > 9) value += Math.floor(Math.random() * 40);
          if (d >= 5) value += Math.floor(Math.random() * 20);
          finalData.push([h, d, Math.min(value, 100)]);
        }
      }
    }

    chart.setOption({
      tooltip: {
        position: 'top',
        formatter: (p: any) => `${days[p.value[1]]} ${hours[p.value[0]]}:00<br/>攻击强度: ${p.value[2]}`,
      },
      grid: { left: 55, right: 30, top: 10, bottom: 40 },
      xAxis: {
        type: 'category', data: hours, splitArea: { show: true },
        axisLabel: { color: '#8B949E', fontSize: 10 },
        axisLine: { lineStyle: { color: '#30363D' } },
      },
      yAxis: {
        type: 'category', data: days, splitArea: { show: true },
        axisLabel: { color: '#8B949E', fontSize: 10 },
        axisLine: { lineStyle: { color: '#30363D' } },
      },
      visualMap: {
        min: 0, max: 100, calculable: true, orient: 'horizontal',
        left: 'center', bottom: 0,
        inRange: { color: ['#161B22', '#00CC66', '#FFCC00', '#FF8800', '#FF4444'] },
        textStyle: { color: '#8B949E' },
      },
      series: [{
        type: 'heatmap', data: finalData, label: { show: false },
        emphasis: { itemStyle: { shadowBlur: 10, shadowColor: 'rgba(255,68,68,0.5)' } },
      }],
    });

    const handleResize = () => chart.resize();
    window.addEventListener('resize', handleResize);
    return () => { window.removeEventListener('resize', handleResize); chart.dispose(); };
  }, [data]);

  return <div ref={chartRef} style={{ width: '100%', height: 340 }} />;
}
