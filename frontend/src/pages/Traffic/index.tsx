import { useState, useEffect, useCallback } from 'react';
import { Card, Row, Col, Table, Tag, Button, Space, Statistic, Slider, Upload, message, Switch } from 'antd';
import { ReloadOutlined, PlayCircleOutlined, PauseCircleOutlined, UploadOutlined, InboxOutlined } from '@ant-design/icons';
import TrafficChart from '../../components/TrafficChart';
import { api } from '../../api';

const { Dragger } = Upload;

export default function TrafficPage() {
  const [detectMode, setDetectMode] = useState<'offline' | 'realtime'>('realtime');
  const [captureStatus, setCaptureStatus] = useState<any>(null);
  const [probeStatus, setProbeStatus] = useState<any>(null);
  const [trafficLogs, setTrafficLogs] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);
  const [attackRatio, setAttackRatio] = useState(() => {
    const saved = localStorage.getItem('attackRatio');
    return saved ? parseInt(saved) : 25;
  });
  const [useRealCapture, setUseRealCapture] = useState(false);
  const [pcapResult, setPcapResult] = useState<any>(() => {
    try { const s = localStorage.getItem('pcapResult'); return s ? JSON.parse(s) : null; }
    catch { return null; }
  });
  const [uploading, setUploading] = useState(false);

  const saveResult = (res: any) => {
    setPcapResult(res);
    localStorage.setItem('pcapResult', JSON.stringify(res));
  };

  const clearResult = () => {
    setPcapResult(null);
    localStorage.removeItem('pcapResult');
  };

  const handleRatioChange = (v: number) => {
    setAttackRatio(v);
    localStorage.setItem('attackRatio', String(v));
  };

  const handleStartCapture = () => {
    api.startCapture(useRealCapture, attackRatio / 100).then(() => fetchStatus());
  };

  const handleStopCapture = () => {
    api.stopCapture().then(() => fetchStatus());
  };

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

  useEffect(() => { if (detectMode === 'realtime') fetchStatus(); }, [fetchStatus, detectMode]);

  useEffect(() => {
    if (detectMode !== 'realtime') return;
    const timer = setInterval(fetchStatus, 5000);
    return () => clearInterval(timer);
  }, [fetchStatus, detectMode]);

  // PCAP 上传检测
  const handlePcapUpload = (file: File) => {
    setUploading(true);
    api.uploadPcap(file)
      .then((res) => saveResult(res))
      .catch(() => message.error('文件检测失败'))
      .finally(() => setUploading(false));
    return false;
  };

  // 从检测结果生成饼图数据和 TOP5
  const pieData = pcapResult?.results
    ? Object.entries(
        pcapResult.results.reduce((acc: any, r: any) => {
          const name = r.class_name || 'Unknown';
          acc[name] = (acc[name] || 0) + 1;
          return acc;
        }, {})
      ).map(([type, count]) => ({ type, count: count as number }))
    : [];

  const top5 = [...pieData].sort((a: any, b: any) => b.count - a.count).slice(0, 5);

  // Excel 导出
  const exportExcel = () => {
    if (!pcapResult?.results) return;
    const header = '<tr><th>序号</th><th>分类</th><th>置信度</th><th>风险等级</th><th>是否攻击</th></tr>';
    const rows = pcapResult.results.map((r: any, i: number) =>
      `<tr><td>${i+1}</td><td>${r.class_name}</td><td>${(r.confidence*100).toFixed(1)}%</td><td>${r.risk_level}</td><td>${r.is_attack?'是':'否'}</td></tr>`
    ).join('');
    const html = `<html><meta charset="utf-8"><body><table border="1">${header}${rows}</table></body></html>`;
    const blob = new Blob([html], { type: 'application/vnd.ms-excel' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = `检测结果_${new Date().toISOString().slice(0,10)}.xls`;
    a.click(); URL.revokeObjectURL(url);
    message.success('导出成功');
  };

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10 }}>
        <h2 style={{ fontSize: 20, fontWeight: 600, margin: 0 }}>流量分析</h2>
        <Space>
          {detectMode === 'realtime' && (
            <Button icon={<ReloadOutlined />} onClick={fetchStatus} loading={loading}>刷新</Button>
          )}
        </Space>
      </div>

      {/* 模式切换 */}
      <div style={{ marginBottom: 16, display: 'flex', gap: 8 }}>
        <Button type={detectMode === 'realtime' ? 'primary' : 'default'} onClick={() => setDetectMode('realtime')}>
          📡 实时网卡检测
        </Button>
        <Button type={detectMode === 'offline' ? 'primary' : 'default'} onClick={() => setDetectMode('offline')}>
          📁 离线文件检测
        </Button>
      </div>

      {detectMode === 'offline' ? (
        /* ===== 离线模式：PCAP 上传检测 ===== */
        <>
          <Card title="PCAP 文件离线检测" style={{ marginBottom: 16 }}>
            <Dragger
              accept=".pcap,.pcapng,.cap"
              showUploadList={false}
              beforeUpload={handlePcapUpload}
              disabled={uploading}
            >
              <p className="ant-upload-drag-icon"><InboxOutlined /></p>
              <p className="ant-upload-text">点击或拖拽 PCAP 文件到此区域上传</p>
              <p className="ant-upload-hint">支持 .pcap / .pcapng / .cap 格式</p>
            </Dragger>
          </Card>

          {pcapResult && (
            <>
              <Card title="检测摘要" size="small" style={{ marginBottom: 16 }}
                extra={<Space>
                  <Button size="small" onClick={exportExcel}>导出 Excel</Button>
                  <Button size="small" danger onClick={clearResult}>清除结果</Button>
                </Space>}>
                <Row gutter={16}>
                  <Col span={6}><Statistic title="文件名" value={pcapResult.filename || '-'} /></Col>
                  <Col span={6}><Statistic title="总样本数" value={pcapResult.total_samples || 0} /></Col>
                  <Col span={6}><Statistic title="攻击样本" value={pcapResult.attack_count || 0}
                    valueStyle={{ color: pcapResult.attack_count > 0 ? 'var(--risk-critical)' : 'var(--risk-low)' }} /></Col>
                  <Col span={6}><Statistic title="正常样本" value={(pcapResult.total_samples||0) - (pcapResult.attack_count||0)}
                    valueStyle={{ color: 'var(--risk-low)' }} /></Col>
                </Row>
              </Card>

              <Row gutter={[16, 16]} style={{ marginBottom: 16 }}>
                <Col xs={24} md={10}>
                  <Card title="攻击类型分布" size="small">
                    <TrafficChart showProtocol pieData={pieData} />
                  </Card>
                </Col>
                <Col xs={24} md={14}>
                  <Card title="攻击类型 TOP 5" size="small">
                    <Table dataSource={top5.map((d: any, i: number) => ({ ...d, key: i, rank: i+1 }))}
                      pagination={false} size="small"
                      columns={[
                        { title: '排名', dataIndex: 'rank', width: 60, render: (v: number) => v<=3?['🥇','🥈','🥉'][v-1]:v },
                        { title: '攻击类型', dataIndex: 'type' },
                        { title: '数量', dataIndex: 'count', render: (v: number) => <Tag>{v}</Tag> },
                      ]}
                      locale={{ emptyText: '暂无数据' }} />
                  </Card>
                </Col>
              </Row>

              {pcapResult.results && pcapResult.results.length > 0 && (
                <Card title="检测明细" size="small">
                  <Table dataSource={pcapResult.results} rowKey={(_, i) => String(i)}
                    size="small" pagination={{ defaultPageSize: 20, pageSizeOptions: ['10','20'], showSizeChanger: true, showTotal: (t) => `共 ${t} 条` }}
                    columns={[
                      { title: '#', render: (_, __, i) => i+1, width: 50 },
                      { title: '分类', dataIndex: 'class_name', width: 100, render: (v: string) => <Tag color={v==='Normal'?'green':'red'}>{v}</Tag> },
                      { title: '置信度', dataIndex: 'confidence', width: 90, render: (v: number) => v ? `${(v*100).toFixed(1)}%` : '-' },
                      { title: '风险等级', dataIndex: 'risk_level', width: 90, render: (v: string) => <Tag>{v}</Tag> },
                      { title: '是否攻击', dataIndex: 'is_attack', width: 80, render: (v: boolean) => v?<Tag color="red">是</Tag>:<Tag color="green">否</Tag> },
                    ]} />
                </Card>
              )}
            </>
          )}
        </>
      ) : (
        /* ===== 实时模式：抓包控制 ===== */
        <>
          {/* Capture Mode + Ratio */}
          <Card size="small" style={{ marginBottom: 16 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 16, flexWrap: 'wrap' }}>
              <Space>
                <span style={{ color: 'var(--text-secondary)', fontSize: 13 }}>抓包模式</span>
                <Switch checked={useRealCapture} onChange={setUseRealCapture}
                  checkedChildren="真实" unCheckedChildren="仿真" />
                {useRealCapture && <span style={{ fontSize: 11, color: 'var(--risk-high)' }}>需安装 Npcap</span>}
              </Space>
              {!useRealCapture && (
                <div style={{ flex: 1, display: 'flex', alignItems: 'center', gap: 12, minWidth: 200 }}>
                  <span style={{ color: 'var(--text-secondary)', fontSize: 13, whiteSpace: 'nowrap' }}>攻击比例</span>
                  <Slider min={0} max={100} value={attackRatio} onChange={handleRatioChange} style={{ flex: 1 }}
                    tooltip={{ formatter: (v) => `${v}%` }} />
                  <span style={{ color: attackRatio > 50 ? 'var(--risk-critical)' : 'var(--text-secondary)', fontWeight: 600, minWidth: 40, textAlign: 'right' }}>{attackRatio}%</span>
                </div>
              )}
            </div>
          </Card>

          {/* Stats */}
          <Row gutter={[16, 16]} style={{ marginBottom: 16 }}>
            <Col xs={24} sm={12} md={6}>
              <Card size="small">
                <Statistic title="抓包状态" value={captureStatus?.running ? '运行中' : '已停止'}
                  valueStyle={{ color: captureStatus?.running ? 'var(--risk-low)' : 'var(--text-muted)', fontSize: 22 }}
                  suffix={captureStatus?.running
                    ? <PauseCircleOutlined onClick={handleStopCapture} style={{ cursor: 'pointer', color: 'var(--risk-critical)' }} />
                    : <PlayCircleOutlined onClick={handleStartCapture} style={{ cursor: 'pointer', color: 'var(--risk-low)' }} />} />
              </Card>
            </Col>
            <Col xs={24} sm={12} md={6}>
              <Card size="small"><Statistic title="已抓数据包" value={captureStatus?.packet_count || 0} valueStyle={{ fontSize: 22 }} /></Card>
            </Col>
            <Col xs={24} sm={12} md={6}>
              <Card size="small"><Statistic title="产生告警" value={captureStatus?.alert_count || 0}
                valueStyle={{ color: 'var(--risk-critical)', fontSize: 22 }} /></Card>
            </Col>
            <Col xs={24} sm={12} md={6}>
              <Card size="small"><Statistic title="在线探针" value={probeStatus?.online_probes || 0}
                suffix={`/ ${probeStatus?.total_probes || 0}`} valueStyle={{ color: 'var(--risk-low)', fontSize: 22 }} /></Card>
            </Col>
          </Row>

          {/* Traffic Logs */}
          <Card title="实时流量日志" size="small">
            <Table dataSource={trafficLogs} rowKey="id" size="small" pagination={{ pageSize: 15, size: 'small' }}
              columns={[
                { title: '时间', dataIndex: 'timestamp', width: 160, render: (v: string) => v?.split('T')[1]?.split('.')[0] || v },
                { title: '源 IP', dataIndex: 'src_ip', width: 140 },
                { title: '目标 IP', dataIndex: 'dst_ip', width: 140 },
                { title: '源端口', dataIndex: 'src_port', width: 80 },
                { title: '目标端口', dataIndex: 'dst_port', width: 80 },
                { title: '协议', dataIndex: 'protocol', width: 70, render: (v: string) => <Tag>{v}</Tag> },
                { title: '长度', dataIndex: 'length', width: 70 },
                { title: '标志', dataIndex: 'flags', width: 60 },
              ]}
              locale={{ emptyText: '暂无流量数据，请启动抓包' }} />
          </Card>
        </>
      )}
    </div>
  );
}
