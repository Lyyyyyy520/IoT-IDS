import { useEffect, useRef } from 'react';
import * as echarts from 'echarts';

interface Props {
  score?: number; // 0-100
}

export default function RiskGauge({ score = 0 }: Props) {
  const chartRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!chartRef.current) return;
    const chart = echarts.init(chartRef.current, 'dark');

    chart.setOption({
      series: [
        {
          type: 'gauge',
          startAngle: 210,
          endAngle: -30,
          center: ['50%', '55%'],
          radius: '90%',
          min: 0,
          max: 100,
          splitNumber: 10,
          axisLine: {
            show: true,
            lineStyle: {
              width: 18,
              color: [
                [0.3, '#00CC66'],
                [0.6, '#FFCC00'],
                [0.8, '#FF8800'],
                [1, '#FF4444'],
              ],
            },
          },
          pointer: {
            icon: 'path://M12.8,0.7l12,40.1H0.7L12.8,0.7z',
            length: '70%',
            width: 8,
            offsetCenter: [0, '-10%'],
            itemStyle: { color: 'auto' },
          },
          axisTick: { distance: -18, length: 8, lineStyle: { width: 2, color: '#8B949E' } },
          splitLine: { distance: -22, length: 16, lineStyle: { width: 3, color: '#8B949E' } },
          axisLabel: {
            color: '#8B949E',
            distance: 30,
            fontSize: 10,
          },
          anchor: {
            show: true,
            size: 16,
            itemStyle: { borderWidth: 2, borderColor: '#58A6FF' },
          },
          title: {
            show: true,
            offsetCenter: [0, '75%'],
            fontSize: 13,
            color: '#8B949E',
          },
          detail: {
            valueAnimation: true,
            fontSize: 36,
            fontWeight: 700,
            offsetCenter: [0, '40%'],
            formatter: '{value}',
            color: '#E6EDF3',
          },
          data: [{ value: score, name: '安全评分' }],
        },
      ],
    });

    const handleResize = () => chart.resize();
    window.addEventListener('resize', handleResize);
    return () => {
      window.removeEventListener('resize', handleResize);
      chart.dispose();
    };
  }, [score]);

  return <div ref={chartRef} style={{ width: '100%', height: 260 }} />;
}
