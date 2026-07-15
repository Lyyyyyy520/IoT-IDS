import { useState, useEffect } from 'react';
import { Card, Row, Col, Spin } from 'antd';
import Topology from '../../components/Topology';
import Heatmap from '../../components/Heatmap';
import TrafficChart from '../../components/TrafficChart';
import MitreAttack from '../../components/MitreAttack';
import { api } from '../../api';

export default function AnalysisPage() {
  const [topoData, setTopoData] = useState(null);
  const [heatData, setHeatData] = useState(null);
  const [mitreData, setMitreData] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    Promise.all([
      api.getTopology().catch(() => null),
      api.getHeatmap().catch(() => null),
      api.getMitre().catch(() => null),
    ]).then(([topo, heat, mitre]) => {
      setTopoData(topo);
      setHeatData(heat);
      setMitreData(mitre);
    }).finally(() => setLoading(false));
  }, []);

  if (loading) {
    return <div style={{ textAlign: 'center', paddingTop: 100 }}><Spin size="large" /></div>;
  }

  return (
    <div>
      <h2 style={{ marginBottom: 20, fontSize: 20, fontWeight: 600 }}>分析视图</h2>

      <Row gutter={[16, 16]}>
        <Col xs={24} lg={12}>
          <Card title="网络拓扑图" style={{ height: 420 }}>
            <Topology data={topoData} />
          </Card>
        </Col>
        <Col xs={24} lg={12}>
          <Card title="攻击热力地图" style={{ height: 420 }}>
            <Heatmap data={heatData} />
          </Card>
        </Col>
      </Row>

      <Row gutter={[16, 16]} style={{ marginTop: 16 }}>
        <Col xs={24} lg={14}>
          <Card title="流量统计">
            <TrafficChart showProtocol />
          </Card>
        </Col>
        <Col xs={24} lg={10}>
          <Card title="MITRE ATT&CK 攻击链路">
            <MitreAttack data={mitreData} />
          </Card>
        </Col>
      </Row>
    </div>
  );
}
