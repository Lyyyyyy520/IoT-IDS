import { useState, useEffect, useCallback } from 'react';
import { Table, Tag, Button, Space, Tooltip, Modal, Descriptions, message, Spin, Tabs, Popconfirm, Switch } from 'antd';
import {
  StopOutlined,
  SearchOutlined,
  CloseCircleOutlined,
  ExportOutlined,
  EyeOutlined,
  UndoOutlined,
} from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';
import { api, type AlertItem, type BlocklistItem } from '../../api';

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
  const [activeTab, setActiveTab] = useState('alerts');
  const [merged, setMerged] = useState(false);

  // ---- Blacklist state ----
  const [blocklist, setBlocklist] = useState<BlocklistItem[]>([]);
  const [blocklistLoading, setBlocklistLoading] = useState(false);
  const [selectedBlock, setSelectedBlock] = useState<BlocklistItem | null>(null);
  const [blockDetailOpen, setBlockDetailOpen] = useState(false);

  const fetchAlerts = useCallback(() => {
    setLoading(true);
    const params: Record<string, string> = {};
    if (merged) params.merged = 'true';
    api.getAlerts(params)
      .then((res) => setAlerts(res.items))
      .catch(() => message.warning('无法连接后端，显示离线数据'))
      .finally(() => setLoading(false));
  }, [merged]);

  const fetchBlocklist = useCallback(() => {
    setBlocklistLoading(true);
    api.getBlocklist()
      .then((res) => setBlocklist(res.items))
      .catch(() => message.warning('无法获取黑名单数据'))
      .finally(() => setBlocklistLoading(false));
  }, []);

  useEffect(() => { fetchAlerts(); fetchBlocklist(); }, [fetchAlerts, fetchBlocklist]);

  // ---- Alert Actions ----
  const handleBlock = (record: AlertItem) => {
    api.blockIp(record.id)
      .then((res) => {
        message.success(res.message || `已拉黑 IP: ${record.src_ip}`);
        fetchAlerts();
        fetchBlocklist();
        setSelectedAlert((prev) => prev?.id === record.id ? { ...prev, status: 'blocked' } : prev);
      })
      .catch(() => message.error('拉黑失败，请检查后端服务是否运行'));
  };

  const handleUnblock = (record: AlertItem) => {
    api.unblockIp(record.id)
      .then((res) => {
        message.success(res.message || `已解除对 ${record.src_ip} 的拉黑`);
        fetchAlerts();
        fetchBlocklist();
        setSelectedAlert((prev) => prev?.id === record.id ? { ...prev, status: 'reviewed' } : prev);
      })
      .catch(() => message.error('解除拉黑失败，请检查后端服务是否运行'));
  };

  const handleTrace = (record: AlertItem) => {
    api.traceAlert(record.id)
      .then((res) => {
        Modal.info({
          title: '溯源分析结果',
          content: <pre style={{ whiteSpace: 'pre-wrap', fontSize: 13, maxHeight: 400, overflow: 'auto' }}>{res.trace_info}</pre>,
          width: 520,
        });
        fetchAlerts();
      })
      .catch(() => message.error('溯源失败，请检查后端服务是否运行'));
  };

  const handleFalsePositive = (record: AlertItem) => {
    api.markFalsePositive(record.id)
      .then((res) => {
        message.success(res.message || '已标记为误报');
        fetchAlerts();
        setSelectedAlert((prev) => prev?.id === record.id ? { ...prev, status: 'false_positive' } : prev);
      })
      .catch(() => message.error('操作失败，请检查后端服务是否运行'));
  };

  const handleUnmarkFalsePositive = (record: AlertItem) => {
    api.unmarkFalsePositive(record.id)
      .then((res) => {
        message.success(res.message || '已撤销误报标记');
        fetchAlerts();
        setSelectedAlert((prev) => prev?.id === record.id ? { ...prev, status: 'reviewed' } : prev);
      })
      .catch(() => message.error('操作失败，请检查后端服务是否运行'));
  };

  // ---- Blacklist Actions ----
  const handleBlocklistUnblock = (record: BlocklistItem) => {
    api.deleteBlocklist(record.id)
      .then((res) => {
        message.success(res.message || `已解除对 ${record.ip_address} 的拉黑`);
        fetchBlocklist();
        fetchAlerts(); // 同步刷新告警列表
      })
      .catch(() => message.error('解除拉黑失败，请检查后端服务是否运行'));
  };

  // ---- Alert Table Columns ----
  const alertColumns: ColumnsType<AlertItem> = [
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
      render: (v: number) => (
        <span style={{ color: v > 1 ? 'var(--accent-orange, #fa8c16)' : 'var(--text-secondary)' }}>{v}</span>
      ),
    },
    { title: '时间', dataIndex: 'timestamp', key: 'timestamp', width: 170 },
    {
      title: '状态', dataIndex: 'status', key: 'status', width: 80,
      render: (s: string) => {
        const map: Record<string, { color: string; label: string }> = {
          new: { color: 'red', label: '新' },
          reviewed: { color: 'orange', label: '已阅' },
          resolved: { color: 'green', label: '已处理' },
          blocked: { color: 'volcano', label: '已拉黑' },
          false_positive: { color: 'default', label: '误报' },
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
          {record.status === 'blocked' ? (
            <Tooltip title="解除拉黑">
              <Button size="small" icon={<UndoOutlined />} type="text" style={{ color: '#52c41a' }}
                onClick={() => handleUnblock(record)} />
            </Tooltip>
          ) : (
            <Tooltip title="拉黑 IP">
              <Button size="small" icon={<StopOutlined />} type="text" danger
                onClick={() => handleBlock(record)} />
            </Tooltip>
          )}
          <Tooltip title="溯源分析">
            <Button size="small" icon={<SearchOutlined />} type="text"
              onClick={() => handleTrace(record)} />
          </Tooltip>
          {record.status === 'false_positive' ? (
            <Tooltip title="撤销误报">
              <Button size="small" icon={<UndoOutlined />} type="text" style={{ color: '#52c41a' }}
                onClick={() => handleUnmarkFalsePositive(record)} />
            </Tooltip>
          ) : (
            <Tooltip title="标记误报">
              <Button size="small" icon={<CloseCircleOutlined />} type="text"
                onClick={() => handleFalsePositive(record)} />
            </Tooltip>
          )}
        </Space>
      ),
    },
  ];

  // ---- Blacklist Table Columns ----
  const blocklistColumns: ColumnsType<BlocklistItem> = [
    { title: 'IP 地址', dataIndex: 'ip_address', key: 'ip_address', width: 160 },
    {
      title: '风险等级',
      dataIndex: 'risk_level',
      key: 'risk_level',
      width: 100,
      render: (level: string | null) =>
        level ? <Tag color={riskColorMap[level] || '#888'}>{riskLabelMap[level] || level}</Tag> : '-',
    },
    { title: '攻击类型', dataIndex: 'attack_type', key: 'attack_type', width: 110, render: (v: string | null) => v || '-' },
    { title: '目标 IP', dataIndex: 'dst_ip', key: 'dst_ip', width: 150, render: (v: string | null) => v || '-' },
    { title: '拉黑时间', dataIndex: 'blocked_at', key: 'blocked_at', width: 170 },
    {
      title: '操作', key: 'actions', width: 160,
      render: (_, record) => (
        <Space size="small">
          <Tooltip title="查看详情">
            <Button size="small" icon={<EyeOutlined />} type="text"
              onClick={() => { setSelectedBlock(record); setBlockDetailOpen(true); }} />
          </Tooltip>
          <Popconfirm
            title="确定解除拉黑？"
            description={`将解除对 ${record.ip_address} 的拉黑`}
            onConfirm={() => handleBlocklistUnblock(record)}
            okText="确定"
            cancelText="取消"
          >
            <Tooltip title="解除拉黑">
              <Button size="small" icon={<UndoOutlined />} type="text" />
            </Tooltip>
          </Popconfirm>
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

      <Tabs
        activeKey={activeTab}
        onChange={setActiveTab}
        items={[
          {
            key: 'alerts',
            label: (
              <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
                📋 告警列表
                <Switch
                  size="small"
                  checked={merged}
                  onChange={(v) => setMerged(v)}
                  onClick={(_, e) => e.stopPropagation()}
                />
                <span style={{ fontSize: 12, color: 'var(--text-muted)', fontWeight: 400 }}>合并显示</span>
              </span>
            ),
            children: (
              <Table
                columns={alertColumns}
                dataSource={alerts}
                rowKey="id"
                loading={loading}
                rowClassName={(record) => record.risk_level === 'critical' ? 'alert-row-critical' : ''}
                pagination={{ pageSize: 20, showSizeChanger: true, showTotal: (t) => `共 ${t} 条告警` }}
                size="middle"
              />
            ),
          },
          {
            key: 'blocklist',
            label: `🛑 黑名单`,
            children: (
              <Table
                columns={blocklistColumns}
                dataSource={blocklist}
                rowKey="id"
                loading={blocklistLoading}
                pagination={{ pageSize: 20, showSizeChanger: true, showTotal: (t) => `共 ${t} 条记录` }}
                size="middle"
                locale={{ emptyText: '暂无被拉黑的 IP' }}
              />
            ),
          },
        ]}
      />

      {/* Alert Detail Modal */}
      <Modal
        title="告警详情"
        open={detailOpen}
        onCancel={() => setDetailOpen(false)}
        footer={[
          selectedAlert?.status === 'blocked' ? (
            <Button key="unblock" icon={<UndoOutlined />} style={{ color: '#52c41a' }}
              onClick={() => { if (selectedAlert) handleUnblock(selectedAlert); }}>解除拉黑</Button>
          ) : (
            <Button key="block" danger icon={<StopOutlined />}
              onClick={() => { if (selectedAlert) handleBlock(selectedAlert); }}>拉黑 IP</Button>
          ),
          <Button key="trace" icon={<SearchOutlined />}
            onClick={() => { if (selectedAlert) handleTrace(selectedAlert); }}>溯源分析</Button>,
          selectedAlert?.status === 'false_positive' ? (
            <Button key="unfp" icon={<UndoOutlined />} style={{ color: '#52c41a' }}
              onClick={() => { if (selectedAlert) handleUnmarkFalsePositive(selectedAlert); }}>撤销误报</Button>
          ) : (
            <Button key="fp" icon={<CloseCircleOutlined />}
              onClick={() => { if (selectedAlert) handleFalsePositive(selectedAlert); }}>标记误报</Button>
          ),
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

      {/* Blocklist Detail Modal */}
      <Modal
        title={`黑名单详情 — ${selectedBlock?.ip_address || ''}`}
        open={blockDetailOpen}
        onCancel={() => setBlockDetailOpen(false)}
        footer={[
          <Button key="unblock" danger icon={<UndoOutlined />}
            onClick={() => { if (selectedBlock) { handleBlocklistUnblock(selectedBlock); setBlockDetailOpen(false); } }}>解除拉黑</Button>,
          <Button key="close" type="primary" onClick={() => setBlockDetailOpen(false)}>关闭</Button>,
        ]}
        width={640}
      >
        {selectedBlock && (
          <Descriptions column={2} size="small" bordered>
            <Descriptions.Item label="IP 地址">{selectedBlock.ip_address}</Descriptions.Item>
            <Descriptions.Item label="攻击类型">{selectedBlock.attack_type || '-'}</Descriptions.Item>
            <Descriptions.Item label="风险等级">
              {selectedBlock.risk_level ? (
                <Tag color={riskColorMap[selectedBlock.risk_level]}>{riskLabelMap[selectedBlock.risk_level]}</Tag>
              ) : '-'}
            </Descriptions.Item>
            <Descriptions.Item label="目标 IP">{selectedBlock.dst_ip || '-'}</Descriptions.Item>
            <Descriptions.Item label="拉黑时间">{selectedBlock.blocked_at}</Descriptions.Item>
            <Descriptions.Item label="关联告警">#{selectedBlock.alert_id || '无'}</Descriptions.Item>
            <Descriptions.Item label="告警状态">
              {selectedBlock.alert_status ? (
                <Tag>{selectedBlock.alert_status}</Tag>
              ) : '-'}
            </Descriptions.Item>
            <Descriptions.Item label="拉黑原因" span={2}>{selectedBlock.reason || '-'}</Descriptions.Item>
          </Descriptions>
        )}
      </Modal>
    </div>
  );
}
