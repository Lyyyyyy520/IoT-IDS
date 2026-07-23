import { useState, useEffect } from 'react';
import { Card, Row, Col, Spin, Table, Tag, Button, Space } from 'antd';
import { ArrowUpOutlined } from '@ant-design/icons';
import TrafficChart from '../../components/TrafficChart';
import AlertCard from '../../components/AlertCard';
import RiskGauge from '../../components/RiskGauge';
import Topology from '../../components/Topology';
import Heatmap from '../../components/Heatmap';
import { api, type DashboardStats } from '../../api';

const FALLBACK_DASH: DashboardStats = {
  total_scanned: 0, alerts_today: 0, total_alerts: 0,
  active_threats: 0, total_assets: 0, online_assets: 0,
  risk_score: 85, system_status: 'normal',
  traffic_history: [], attack_distribution: [],
  recent_alerts: [],
};

export default function DashboardPage() {
  const [stats, setStats] = useState<DashboardStats | null>(null);
  const [topoData, setTopoData] = useState(null);
  const [heatData, setHeatData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [dashSource, setDashSource] = useState<'sim' | 'real'>(() => {
    return (localStorage.getItem('dashSource') === 'real') ? 'real' : 'sim';
  });

  const fetchAll = () => {
    setLoading(true);
    const params: Record<string, string> = { source: dashSource };
    Promise.all([
      api.getDashboardStats(params).catch(() => FALLBACK_DASH),
      api.getTopology().catch(() => null),
      api.getHeatmap().catch(() => null),
    ]).then(([s, topo, heat]) => {
      setStats(s as DashboardStats);
      setTopoData(topo);
      setHeatData(heat);
    }).finally(() => setLoading(false));
  };

  useEffect(() => { fetchAll(); }, [dashSource]);

  // 每 10 秒刷新 KPI
  useEffect(() => {
    const timer = setInterval(() => {
      api.getDashboardStats({ source: dashSource }).then(setStats).catch(() => {});
    }, 10000);
    return () => clearInterval(timer);
  }, [dashSource]);

  if (loading) {
    return <div style={{ textAlign: 'center', paddingTop: 100 }}><Spin size="large" /></div>;
  }

  const s = stats || FALLBACK_DASH;

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <h2 style={{ fontSize: 20, fontWeight: 600, margin: 0 }}>态势感知大屏</h2>
        <Space>
          <Button type={dashSource === 'sim' ? 'primary' : 'default'} size="small"
            onClick={() => { setDashSource('sim'); localStorage.setItem('dashSource', 'sim'); }}>
            🎮 仿真
          </Button>
          <Button type={dashSource === 'real' ? 'primary' : 'default'} size="small"
            onClick={() => { setDashSource('real'); localStorage.setItem('dashSource', 'real'); }}>
            🛰️ 真实
          </Button>
        </Space>
      </div>

      {/* Row 1: Risk Gauge + KPI Cards */}
      <Row gutter={[16, 16]} style={{ marginBottom: 16 }}>
        <Col xs={24} sm={8} lg={6}>
          <Card size="small">
            <RiskGauge score={s.risk_score ?? 85} />
          </Card>
        </Col>
        <Col xs={24} sm={16} lg={18}>
          <Row gutter={[12, 12]}>
            {[
              { label: '今日告警', value: s.alerts_today, color: 'var(--risk-critical)' },
              { label: '活跃威胁', value: s.active_threats, color: 'var(--risk-high)' },
              { label: '累计告警', value: s.total_alerts, color: 'var(--risk-medium)' },
              { label: '在线资产', value: `${s.online_assets}/${s.total_assets}`, color: 'var(--risk-low)' },
              { label: '检测流量', value: (s.total_scanned ?? 0).toLocaleString(), color: 'var(--accent-blue)' },
              { label: '系统状态', value: s.system_status === 'normal' ? '正常运行' : '⚠ 告警中', color: s.system_status === 'normal' ? 'var(--risk-low)' : 'var(--risk-high)' },
            ].map((k, i) => (
              <Col xs={12} sm={8} key={i}>
                <div className="stat-card" style={{ padding: '14px 18px' }}>
                  <span className="stat-label">{k.label}</span>
                  <span className="stat-value" style={{ color: k.color, fontSize: 24 }}>{k.value}</span>
                  <span className="stat-trend"><ArrowUpOutlined style={{ color: 'var(--risk-critical)', fontSize: 10 }} />{' '}实时</span>
                </div>
              </Col>
            ))}
          </Row>
        </Col>
      </Row>

      {/* Row 2: TOP5 + 最近告警 */}
      <Row gutter={[16, 16]} style={{ marginBottom: 16 }}>
        <Col xs={24} lg={14}>
          <Card title="攻击来源 TOP 5" size="small">
            <Table
              dataSource={(s.attack_distribution || []).map((d: any, i: number) => ({ ...d, key: i, rank: i + 1 }))}
              columns={[
                { title: '排名', dataIndex: 'rank', width: 60, render: (v: number) => v <= 3 ? ['🥇','🥈','🥉'][v-1] : v },
                { title: '攻击类型', dataIndex: 'type' },
                { title: '数量', dataIndex: 'count', render: (v: number) => <Tag>{v}</Tag> },
              ]}
              pagination={false} size="small" locale={{ emptyText: '暂无攻击数据' }} style={{ minHeight: 200 }}
            />
          </Card>
        </Col>
        <Col xs={24} lg={10}>
          <Card title="最近告警" size="small" styles={{ body: { padding: '4px 0' } }}>
            {s.recent_alerts?.length ? (
              s.recent_alerts.map((a: any) => <AlertCard key={a.id} alert={a} compact />)
            ) : (
              <div style={{ textAlign: 'center', padding: 40, color: 'var(--text-muted)', fontSize: 13 }}>✅ 暂无告警，系统运行正常</div>
            )}
          </Card>
        </Col>
      </Row>

      {/* Row 3: 攻击趋势 + 攻击类型分布 */}
      <Row gutter={[16, 16]} style={{ marginBottom: 16 }}>
        <Col xs={24} lg={14}>
          <Card title="攻击趋势" size="small">
            <TrafficChart data={s.traffic_history} />
          </Card>
        </Col>
        <Col xs={24} lg={10}>
          <Card title="攻击类型分布" size="small">
            <TrafficChart showProtocol pieData={s.attack_distribution} />
          </Card>
        </Col>
      </Row>

      {/* Row 4: 网络拓扑图 + 攻击热力地图 */}
      <Row gutter={[16, 16]} style={{ marginBottom: 16 }}>
        <Col xs={24} lg={12}>
          <Card title="网络拓扑图" size="small">
            <Topology data={topoData} />
          </Card>
        </Col>
        <Col xs={24} lg={12}>
          <Card title="攻击热力地图" size="small">
            <Heatmap data={heatData} />
          </Card>
        </Col>
      </Row>

    </div>
  );
}
