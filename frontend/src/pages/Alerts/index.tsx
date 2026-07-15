import { useState, useEffect, useCallback } from 'react';
import { Table, Tag, Button, Space, Tooltip, Modal, Descriptions, message, Spin } from 'antd';
import {
  StopOutlined,
  SearchOutlined,
  CloseCircleOutlined,
  ExportOutlined,
  EyeOutlined,
} from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';
import { api, type AlertItem } from '../../api';

const riskColorMap: Record<string, string> = {
  critical: '#FF4444',
  high: '#FF8800',
  medium: '#FFCC00',
  low: '#00CC66',
};

const riskLabelMap: Record<string, string> = {
  critical: '高危',
  high: '中危',
  medium: '低危',
  low: '安全',
};

export default function AlertsPage() {
  const [alerts, setAlerts] = useState<AlertItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedAlert, setSelectedAlert] = useState<AlertItem | null>(null);
  const [detailOpen, setDetailOpen] = useState(false);

  const fetchAlerts = useCallback(() => {
    setLoading(true);
    api.getAlerts()
      .then((res) => setAlerts(res.items))
      .catch(() => message.warning('无法连接后端，显示离线数据'))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => { fetchAlerts(); }, [fetchAlerts]);

  const handleBlock = (record: AlertItem) => {
    api.blockIp(record.id)
      .then(() => message.success(`已拉黑 IP: ${record.src_ip}`))
      .catch(() => message.info(`模拟拉黑: ${record.src_ip}`));
  };

  const handleTrace = (record: AlertItem) => {
    api.traceAlert(record.id)
      .then((res) => message.info(res.trace_info || '溯源信息已生成'))
      .catch(() => message.info(`模拟溯源: 攻击来源 ${record.src_ip}，位于社区网络3号区域`));
  };

  const handleFalsePositive = (record: AlertItem) => {
    api.markFalsePositive(record.id)
      .then(() => message.success('已标记为误报'))
      .catch(() => message.success('模拟标记误报'));
  };

  const columns: ColumnsType<AlertItem> = [
    {
      title: '风险等级',
      dataIndex: 'risk_level',
      key: 'risk_level',
      width: 100,
      sorter: (a, b) => {
        const order = ['critical', 'high', 'medium', 'low'];
        return order.indexOf(a.risk_level) - order.indexOf(b.risk_level);
      },
      defaultSortOrder: 'ascend',
      render: (level: string) => <Tag color={riskColorMap[level]}>{riskLabelMap[level]}</Tag>,
    },
    { title: '攻击类型', dataIndex: 'attack_type', key: 'attack_type', width: 100 },
    { title: '源 IP', dataIndex: 'src_ip', key: 'src_ip', width: 150 },
    { title: '目标 IP', dataIndex: 'dst_ip', key: 'dst_ip', width: 150 },
    {
      title: '置信度', dataIndex: 'confidence', key: 'confidence', width: 90,
      render: (v: number) => `${(v * 100).toFixed(0)}%`,
    },
    {
      title: '重复', dataIndex: 'merged_count', key: 'merged_count', width: 70,
      render: (v: number) => (v > 1 ? <Tag>×{v}</Tag> : '-'),
    },
    { title: '时间', dataIndex: 'timestamp', key: 'timestamp', width: 170 },
    {
      title: '状态', dataIndex: 'status', key: 'status', width: 80,
      render: (s: string) => {
        const map: Record<string, { color: string; label: string }> = {
          new: { color: 'red', label: '新' },
          reviewed: { color: 'orange', label: '已阅' },
          resolved: { color: 'green', label: '已处理' },
        };
        return <Tag color={map[s]?.color}>{map[s]?.label || s}</Tag>;
      },
    },
    {
      title: '操作', key: 'actions', width: 200,
      render: (_, record) => (
        <Space size="small">
          <Tooltip title="查看详情">
            <Button size="small" icon={<EyeOutlined />} type="text"
              onClick={() => { setSelectedAlert(record); setDetailOpen(true); }} />
          </Tooltip>
          <Tooltip title="拉黑 IP">
            <Button size="small" icon={<StopOutlined />} type="text" danger
              onClick={() => handleBlock(record)} />
          </Tooltip>
          <Tooltip title="溯源分析">
            <Button size="small" icon={<SearchOutlined />} type="text"
              onClick={() => handleTrace(record)} />
          </Tooltip>
          <Tooltip title="标记误报">
            <Button size="small" icon={<CloseCircleOutlined />} type="text"
              onClick={() => handleFalsePositive(record)} />
          </Tooltip>
        </Space>
      ),
    },
  ];

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <h2 style={{ fontSize: 20, fontWeight: 600, margin: 0 }}>告警中心</h2>
        <Button icon={<ExportOutlined />}>导出 Excel</Button>
      </div>

      <Table
        columns={columns}
        dataSource={alerts}
        rowKey="id"
        loading={loading}
        rowClassName={(record) => record.risk_level === 'critical' ? 'alert-row-critical' : ''}
        pagination={{ pageSize: 20, showSizeChanger: true, showTotal: (t) => `共 ${t} 条告警` }}
        size="middle"
      />

      {/* Detail Modal */}
      <Modal
        title="告警详情"
        open={detailOpen}
        onCancel={() => setDetailOpen(false)}
        footer={[
          <Button key="block" danger icon={<StopOutlined />}
            onClick={() => { if (selectedAlert) handleBlock(selectedAlert); }}>拉黑 IP</Button>,
          <Button key="trace" icon={<SearchOutlined />}
            onClick={() => { if (selectedAlert) handleTrace(selectedAlert); }}>溯源分析</Button>,
          <Button key="fp" icon={<CloseCircleOutlined />}
            onClick={() => { if (selectedAlert) handleFalsePositive(selectedAlert); }}>标记误报</Button>,
          <Button key="close" type="primary" onClick={() => setDetailOpen(false)}>关闭</Button>,
        ]}
        width={640}
      >
        {selectedAlert && (
          <Descriptions column={2} size="small" bordered>
            <Descriptions.Item label="风险等级">
              <Tag color={riskColorMap[selectedAlert.risk_level]}>{riskLabelMap[selectedAlert.risk_level]}</Tag>
            </Descriptions.Item>
            <Descriptions.Item label="攻击类型">{selectedAlert.attack_type}</Descriptions.Item>
            <Descriptions.Item label="源 IP">{selectedAlert.src_ip}</Descriptions.Item>
            <Descriptions.Item label="目标 IP">{selectedAlert.dst_ip}</Descriptions.Item>
            <Descriptions.Item label="置信度">{(selectedAlert.confidence * 100).toFixed(1)}%</Descriptions.Item>
            <Descriptions.Item label="重复次数">{selectedAlert.merged_count}</Descriptions.Item>
            <Descriptions.Item label="时间">{selectedAlert.timestamp}</Descriptions.Item>
            <Descriptions.Item label="状态">{selectedAlert.status}</Descriptions.Item>
            <Descriptions.Item label="描述" span={2}>{selectedAlert.description || '暂无详细描述'}</Descriptions.Item>
          </Descriptions>
        )}
      </Modal>
    </div>
  );
}
