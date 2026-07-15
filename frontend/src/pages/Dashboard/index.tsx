import { useState, useEffect } from 'react';
import { Card, Row, Col, Spin, Table, Tag } from 'antd';
import { ArrowUpOutlined } from '@ant-design/icons';
import TrafficChart from '../../components/TrafficChart';
import AlertCard from '../../components/AlertCard';
import RiskGauge from '../../components/RiskGauge';
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
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.getDashboardStats()
      .then(setStats)
      .catch(() => setStats(FALLBACK_DASH))
      .finally(() => setLoading(false));
  }, []);

  if (loading) {
    return <div style={{ textAlign: 'center', paddingTop: 100 }}><Spin size="large" /></div>;
  }

  const s = stats || FALLBACK_DASH;

  return (
    <div>
      <h2 style={{ marginBottom: 20, fontSize: 20, fontWeight: 600 }}>态势感知大屏</h2>

      {/* Row 1: Risk Gauge + KPI Cards */}
      <Row gutter={[16, 16]} style={{ marginBottom: 16 }}>
        {/* Risk Score Gauge */}
        <Col xs={24} sm={8} lg={6}>
          <Card size="small">
            <RiskGauge score={s.risk_score ?? 85} />
          </Card>
        </Col>

        {/* KPI Cards */}
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
                  <span className="stat-value" style={{ color: k.color, fontSize: 24 }}>
                    {k.value}
                  </span>
                  <span className="stat-trend">
                    <ArrowUpOutlined style={{ color: 'var(--risk-critical)', fontSize: 10 }} />
                    {' '}实时
                  </span>
                </div>
              </Col>
            ))}
          </Row>
        </Col>
      </Row>

      {/* Row 2: Traffic + Attack Distribution + TOP5 */}
      <Row gutter={[16, 16]} style={{ marginBottom: 16 }}>
        <Col xs={24} lg={14}>
          <Card title="攻击趋势" size="small">
            <TrafficChart />
          </Card>
        </Col>
        <Col xs={24} lg={10}>
          <Card title="攻击类型分布" size="small">
            <TrafficChart showProtocol />
          </Card>
        </Col>
      </Row>

      {/* Row 3: TOP5 IPs + Recent Alerts */}
      <Row gutter={[16, 16]}>
        <Col xs={24} lg={10}>
          <Card title="最近告警" size="small" styles={{ body: { padding: '4px 0' } }}>
            {s.recent_alerts?.length ? (
              s.recent_alerts.map((a: any) => (
                <AlertCard key={a.id} alert={a} compact />
              ))
            ) : (
              <div style={{ textAlign: 'center', padding: 40, color: 'var(--text-muted)', fontSize: 13 }}>
                ✅ 暂无告警，系统运行正常
              </div>
            )}
          </Card>
        </Col>
        <Col xs={24} lg={14}>
          <Card title="攻击来源 TOP 5" size="small">
            <Table
              dataSource={(s.attack_distribution || []).map((d: any, i: number) => ({ ...d, key: i, rank: i + 1 }))}
              columns={[
                { title: '排名', dataIndex: 'rank', width: 60, render: (v: number) => v <= 3 ? ['🥇','🥈','🥉'][v-1] : v },
                { title: '攻击类型', dataIndex: 'type' },
                { title: '数量', dataIndex: 'count', render: (v: number) => <Tag>{v}</Tag> },
              ]}
              pagination={false}
              size="small"
              locale={{ emptyText: '暂无攻击数据' }}
              style={{ minHeight: 200 }}
            />
          </Card>
        </Col>
      </Row>
    </div>
  );
}
