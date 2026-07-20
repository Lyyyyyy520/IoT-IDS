import { useEffect, useRef } from 'react';
import * as echarts from 'echarts';

interface MitreStage { name: string; mitre: string; desc: string; active: boolean; }
interface MitreLink { source: string; target: string; value: number; }

interface Props {
  data?: { stages: MitreStage[]; links: MitreLink[] } | null;
}

const FALLBACK_STAGES: MitreStage[] = [
  { name: '初始侦查', mitre: 'Recon', desc: '端口扫描探测', active: false },
  { name: '初始访问', mitre: 'InitAccess', desc: '漏洞利用与暴力破解', active: false },
  { name: '漏洞利用', mitre: 'Execution', desc: '远程代码执行', active: false },
  { name: 'C2通信', mitre: 'C2', desc: 'C2服务器通信', active: false },
  { name: '数据窃取', mitre: 'Exfil', desc: '敏感数据外传', active: false },
];

const stageIcons: Record<string, string> = {
  '初始侦查': '🔍', '初始访问': '🔑', '漏洞利用': '💥', 'C2通信': '🕸️', '数据窃取': '📤',
};

export default function MitreAttack({ data }: Props) {
  const chartRef = useRef<HTMLDivElement>(null);
  const stages = data?.stages ?? FALLBACK_STAGES;
  const links = data?.links ?? [];

  useEffect(() => {
    if (!chartRef.current) return;
    const chart = echarts.init(chartRef.current, 'dark');

    const finalLinks = links.length > 0
      ? links
      : stages.slice(0, -1).map((_, i) => ({
          source: stages[i].mitre,
          target: stages[i + 1].mitre,
          value: Math.floor(Math.random() * 30) + 10,
        }));

    chart.setOption({
      tooltip: {
        trigger: 'item',
        formatter: (p: any) => {
          if (p.dataType === 'node') {
            const s = stages.find((st) => st.mitre === p.name);
            return s ? `${stageIcons[s.name] || '●'} ${s.name}<br/>${s.desc}` : p.name;
          }
          return `${p.data.source} → ${p.data.target}<br/>攻击事件: ${p.data.value}`;
        },
      },
      series: [{
        type: 'sankey', layout: 'none', emphasis: { focus: 'adjacency' }, nodeAlign: 'left',
        data: stages.map((s) => ({
          name: s.mitre,
          itemStyle: { color: s.active ? '#FF8800' : '#30363D', borderColor: '#30363D' },
        })),
        links: finalLinks.map((l) => ({
          source: l.source, target: l.target, value: l.value,
          lineStyle: { color: 'gradient', curveness: 0.5, opacity: 0.35 },
        })),
      }],
    });

    const handleResize = () => chart.resize();
    window.addEventListener('resize', handleResize);
    return () => { window.removeEventListener('resize', handleResize); chart.dispose(); };
  }, [data, stages, links]);

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 8, padding: '0 8px' }}>
        {stages.map((s, i) => (
          <div key={i} style={{ textAlign: 'center', fontSize: 11, flex: 1, opacity: s.active ? 1 : 0.3 }}>
            <div style={{ fontSize: 18 }}>{stageIcons[s.name] || '●'}</div>
            <div style={{ fontWeight: 500, color: s.active ? 'var(--text-primary)' : 'var(--text-muted)' }}>{s.name}</div>
            <div style={{ fontSize: 10, color: 'var(--text-muted)' }}>{s.desc}</div>
          </div>
        ))}
      </div>
      <div ref={chartRef} style={{ width: '100%', height: 180 }} />
    </div>
  );
}
