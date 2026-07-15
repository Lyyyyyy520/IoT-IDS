import { useState, useEffect, useCallback } from 'react';
import { Card, Row, Col, Table, Tag, Button, Space, Statistic } from 'antd';
import { ReloadOutlined, PlayCircleOutlined, PauseCircleOutlined } from '@ant-design/icons';
import TrafficChart from '../../components/TrafficChart';
import { api } from '../../api';

export default function TrafficPage() {
  const [captureStatus, setCaptureStatus] = useState<any>(null);
  const [probeStatus, setProbeStatus] = useState<any>(null);
  const [trafficLogs, setTrafficLogs] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);

  const fetchStatus = useCallback(() => {
    setLoading(true);
    Promise.all([
      api.getCaptureStatus().catch(() => null),
      api.getProbeStatus().catch(() => null),
      api.getTrafficLogs().catch(() => []),
    ]).then(([cap, probe, logs]) => {
      setCaptureStatus(cap);
      setProbeStatus(probe);
      setTrafficLogs(logs?.items || []);
    }).finally(() => setLoading(false));
  }, []);

  useEffect(() => { fetchStatus(); }, [fetchStatus]);

  const handleStartCapture = () => {
    api.startCapture(false).then(() => {
      fetchStatus();
    });
  };

  const handleStopCapture = () => {
    api.stopCapture().then(() => {
      fetchStatus();
    });
  };

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 20 }}>
        <h2 style={{ fontSize: 20, fontWeight: 600, margin: 0 }}>流量分析</h2>
        <Space>
          <Button icon={<ReloadOutlined />} onClick={fetchStatus} loading={loading}>刷新</Button>
        </Space>
      </div>

      {/* Capture Control */}
      <Row gutter={[16, 16]} style={{ marginBottom: 16 }}>
        <Col xs={24} sm={12} md={6}>
          <Card size="small">
            <Statistic
              title="抓包状态"
              value={captureStatus?.running ? '运行中' : '已停止'}
              valueStyle={{ color: captureStatus?.running ? 'var(--risk-low)' : 'var(--text-muted)', fontSize: 22 }}
              suffix={
                captureStatus?.running
                  ? <PauseCircleOutlined onClick={handleStopCapture} style={{ cursor: 'pointer', color: 'var(--risk-critical)' }} />
                  : <PlayCircleOutlined onClick={handleStartCapture} style={{ cursor: 'pointer', color: 'var(--risk-low)' }} />
              }
            />
          </Card>
        </Col>
        <Col xs={24} sm={12} md={6}>
          <Card size="small">
            <Statistic title="已抓数据包" value={captureStatus?.packet_count || 0} valueStyle={{ fontSize: 22 }} />
          </Card>
        </Col>
        <Col xs={24} sm={12} md={6}>
          <Card size="small">
            <Statistic title="产生告警" value={captureStatus?.alert_count || 0} valueStyle={{ color: 'var(--risk-critical)', fontSize: 22 }} />
          </Card>
        </Col>
        <Col xs={24} sm={12} md={6}>
          <Card size="small">
            <Statistic
              title="在线探针"
              value={probeStatus?.online_probes || 0}
              suffix={`/ ${probeStatus?.total_probes || 0}`}
              valueStyle={{ color: 'var(--risk-low)', fontSize: 22 }}
            />
          </Card>
        </Col>
      </Row>

      {/* Traffic Chart + Protocol Distribution */}
      <Row gutter={[16, 16]} style={{ marginBottom: 16 }}>
        <Col xs={24} lg={14}>
          <Card title="流量趋势" size="small">
            <TrafficChart />
          </Card>
        </Col>
        <Col xs={24} lg={10}>
          <Card title="协议分布" size="small">
            <TrafficChart showProtocol />
          </Card>
        </Col>
      </Row>

      {/* Traffic Logs Table */}
      <Card title="实时流量日志" size="small">
        <Table
          dataSource={trafficLogs}
          rowKey="id"
          size="small"
          pagination={{ pageSize: 15, size: 'small' }}
          columns={[
            { title: '时间', dataIndex: 'timestamp', width: 160, render: (v: string) => v?.split('T')[1]?.split('.')[0] || v },
            { title: '源 IP', dataIndex: 'src_ip', width: 140 },
            { title: '目标 IP', dataIndex: 'dst_ip', width: 140 },
            { title: '源端口', dataIndex: 'src_port', width: 80 },
            { title: '目标端口', dataIndex: 'dst_port', width: 80 },
            {
              title: '协议', dataIndex: 'protocol', width: 70,
              render: (v: string) => <Tag>{v}</Tag>,
            },
            { title: '长度', dataIndex: 'length', width: 70 },
            { title: '标志', dataIndex: 'flags', width: 60 },
          ]}
          locale={{ emptyText: '暂无流量数据，请启动抓包' }}
        />
      </Card>
    </div>
  );
}
