import { useEffect, useRef, useState } from 'react';
import * as d3 from 'd3';

interface TopoNode { id: string; label: string; type: string; risk: string; ip?: string; }
interface TopoLink { source: string; target: string; }

interface Props { data?: { nodes: TopoNode[]; links: TopoLink[] } | null; }

const FALLBACK_NODES: TopoNode[] = [
  { id: 'router', label: '社区路由器', type: 'router', risk: 'normal' },
  { id: 'cam1', label: '摄像头-01', type: 'camera', risk: 'normal' },
  { id: 'cam2', label: '摄像头-02', type: 'camera', risk: 'critical' },
  { id: 'door1', label: '门禁-01', type: 'door', risk: 'normal' },
  { id: 'door2', label: '门禁-02', type: 'door', risk: 'high' },
  { id: 'sensor1', label: '烟感-01', type: 'sensor', risk: 'normal' },
  { id: 'sensor2', label: '温湿度-01', type: 'sensor', risk: 'normal' },
  { id: 'socket1', label: '智能插座-01', type: 'socket', risk: 'medium' },
  { id: 'lock1', label: '智能锁-01', type: 'lock', risk: 'normal' },
  { id: 'hub1', label: '智能网关', type: 'hub', risk: 'normal' },
  { id: 'phone1', label: '业主手机', type: 'phone', risk: 'normal' },
  { id: 'server', label: '管理服务器', type: 'server', risk: 'normal' },
];

const FALLBACK_LINKS: TopoLink[] = [
  { source: 'router', target: 'cam1' }, { source: 'router', target: 'cam2' },
  { source: 'router', target: 'door1' }, { source: 'router', target: 'door2' },
  { source: 'router', target: 'hub1' },
  { source: 'hub1', target: 'sensor1' }, { source: 'hub1', target: 'sensor2' },
  { source: 'hub1', target: 'socket1' }, { source: 'hub1', target: 'lock1' },
  { source: 'router', target: 'server' }, { source: 'router', target: 'phone1' },
];

const riskColorMap: Record<string, string> = {
  critical: '#FF4444', high: '#FF8800', medium: '#FFCC00', low: '#00CC66', normal: '#58A6FF',
};

const riskLabelMap: Record<string, string> = {
  critical: '高危', high: '中危', medium: '低危', low: '安全', normal: '正常',
};

const typeIcons: Record<string, string> = {
  router: '📡', camera: '📷', door: '🚪', sensor: '🌡', socket: '🔌', lock: '🔐', hub: '🔄', phone: '📱', server: '🖥',
};

export default function Topology({ data }: Props) {
  const svgRef = useRef<SVGSVGElement>(null);
  const [selectedNode, setSelectedNode] = useState<TopoNode | null>(null);
  const nodes = data?.nodes ?? FALLBACK_NODES;
  const links = data?.links ?? FALLBACK_LINKS;

  useEffect(() => {
    if (!svgRef.current) return;
    const svg = d3.select(svgRef.current);
    svg.selectAll('*').remove();
    const width = svgRef.current.clientWidth;
    const height = 340;

    const simulation = d3.forceSimulation(nodes as any)
      .force('link', d3.forceLink(links).id((d: any) => d.id).distance(80))
      .force('charge', d3.forceManyBody().strength(-300))
      .force('center', d3.forceCenter(width / 2, height / 2))
      .force('collision', d3.forceCollide(30));

    const g = svg.append('g');

    g.selectAll('line').data(links).join('line')
      .attr('stroke', '#30363D').attr('stroke-width', 1.5).attr('stroke-opacity', 0.8);

    const node = g.selectAll('g').data(nodes).join('g').attr('cursor', 'pointer')
      .on('click', (_e: any, d: any) => {
        _e.stopPropagation();
        setSelectedNode(d);
      })
      .call(d3.drag<any, any>()
        .on('start', (e, d) => { if (!e.active) simulation.alphaTarget(0.3).restart(); d.fx = d.x; d.fy = d.y; })
        .on('drag', (e, d) => { d.fx = e.x; d.fy = e.y; })
        .on('end', (e, d) => { if (!e.active) simulation.alphaTarget(0); d.fx = null; d.fy = null; }) as any);

    node.append('circle')
      .attr('r', (d: any) => d.type === 'router' || d.type === 'hub' ? 20 : 14)
      .attr('fill', (d: any) => riskColorMap[d.risk] || '#58A6FF')
      .attr('stroke', '#0D1117').attr('stroke-width', 2).attr('opacity', 0.9);

    node.append('text')
      .attr('text-anchor', 'middle').attr('dy', '0.35em')
      .attr('font-size', (d: any) => d.type === 'router' || d.type === 'hub' ? 14 : 10)
      .text((d: any) => typeIcons[d.type] || '●');

    node.append('text')
      .attr('text-anchor', 'middle')
      .attr('dy', (d: any) => d.type === 'router' || d.type === 'hub' ? 32 : 24)
      .attr('fill', '#8B949E').attr('font-size', 10)
      .text((d: any) => d.label);

    svg.on('click', () => setSelectedNode(null));

    simulation.on('tick', () => {
      g.selectAll('line').attr('x1', (d: any) => d.source.x).attr('y1', (d: any) => d.source.y)
        .attr('x2', (d: any) => d.target.x).attr('y2', (d: any) => d.target.y);
      node.attr('transform', (d: any) => `translate(${d.x},${d.y})`);
    });

    return () => { simulation.stop(); };
  }, [nodes, links]);

  return (
    <div className="topology-container" style={{ position: 'relative' }}>
      <svg ref={svgRef} style={{ width: '100%', height: 340 }} />
      {selectedNode && (
        <div style={{
          position: 'absolute', top: 12, right: 12,
          background: '#161B22', border: '1px solid #30363D', borderRadius: 8,
          padding: 16, minWidth: 200, boxShadow: '0 4px 12px rgba(0,0,0,0.4)',
          zIndex: 10,
        }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
            <strong style={{ color: 'var(--text-primary)', fontSize: 14 }}>
              {typeIcons[selectedNode.type] || '●'} {selectedNode.label}
            </strong>
            <span style={{ cursor: 'pointer', color: '#8B949E', fontSize: 16 }} onClick={() => setSelectedNode(null)}>✕</span>
          </div>
          <div style={{ fontSize: 12, color: 'var(--text-secondary)', lineHeight: 2 }}>
            <div>类型: {selectedNode.type}</div>
            {selectedNode.ip && <div>IP: {selectedNode.ip}</div>}
            <div>风险: <span style={{ color: riskColorMap[selectedNode.risk] || '#58A6FF' }}>{riskLabelMap[selectedNode.risk] || selectedNode.risk}</span></div>
          </div>
        </div>
      )}
    </div>
  );
}
