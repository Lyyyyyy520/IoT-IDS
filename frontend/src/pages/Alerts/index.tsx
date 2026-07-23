import { useState, useEffect, useCallback, useRef } from 'react';
import { Table, Tag, Button, Space, Tooltip, Modal, Descriptions, message, Spin, Popconfirm, Switch, notification, Select } from 'antd';
import {
  StopOutlined,
  SearchOutlined,
  CloseCircleOutlined,
  ExportOutlined,
  EyeOutlined,
  UndoOutlined,
  ReloadOutlined,
} from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';
import { api, type AlertItem } from '../../api';

const riskColorMap: Record<string, string> = {
  critical: '#FF4444',
  high: '#FF8800',
  medium: '#FFCC00',
};

const riskLabelMap: Record<string, string> = {
  critical: '高危',
  high: '中危',
  medium: '低危',
};

export default function AlertsPage() {
  const [alerts, setAlerts] = useState<AlertItem[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [loading, setLoading] = useState(true);
  const [selectedAlert, setSelectedAlert] = useState<AlertItem | null>(null);
  const [detailOpen, setDetailOpen] = useState(false);
  const [merged, setMerged] = useState(false);
  const [riskFilter, setRiskFilter] = useState('all');
  const [typeFilter, setTypeFilter] = useState('all');
  const [timeFilter, setTimeFilter] = useState('24h');
  const [sourceFilter, setSourceFilter] = useState('all'); // 全部/仿真/真实

  const fetchAlerts = useCallback(() => {
    setLoading(true);
    const params: Record<string, string> = {
      page: String(page),
      page_size: String(pageSize),
    };
    if (merged) params.merged = 'true';
    if (riskFilter !== 'all') params.risk_level = riskFilter;
    if (typeFilter !== 'all') params.attack_type = typeFilter;
    if (timeFilter) params.time_range = timeFilter;
    api.getAlerts(params)
      .then((res) => {
        let items = res.items;
        if (sourceFilter === 'sim') items = items.filter((a: any) => (a.description||'').startsWith('[sim]'));
        else if (sourceFilter === 'real') items = items.filter((a: any) => (a.description||'').startsWith('[real]'));
        setAlerts(items); setTotal(items.length);
      })
      .catch(() => message.warning('无法连接后端，显示离线数据'))
      .finally(() => setLoading(false));
  }, [merged, riskFilter, typeFilter, timeFilter, sourceFilter, page, pageSize]);

  // 筛选变化时重置页码
  useEffect(() => { setPage(1); }, [riskFilter, typeFilter, timeFilter]);

  useEffect(() => { fetchAlerts(); }, [fetchAlerts]);

  // ---- 新告警实时轮询 ----
  const lastCheckRef = useRef<number>(0);

  // 初始化：拿到当前最新告警的 ID 作为基准
  useEffect(() => {
    fetch('/api/alerts/new?since=')
      .then((r) => r.json())
      .then((data) => {
        if (data.max_id) lastCheckRef.current = data.max_id;
      })
      .catch(() => {});
  }, []);

  useEffect(() => {
    const checkNewAlerts = () => {
      fetch(`/api/alerts/new?since_id=${lastCheckRef.current}`)
        .then((r) => r.json())
        .then((data) => {
          if (data.items && data.items.length > 0) {
            // 更新基准 ID
            lastCheckRef.current = data.max_id || lastCheckRef.current;
            // 合并通知：不管多少条，只弹一条
            const count = data.items.length;
            const hottest = data.items.reduce((a: AlertItem, b: AlertItem) => {
              const order = ['critical', 'high', 'medium', 'low'];
              return order.indexOf(a.risk_level) <= order.indexOf(b.risk_level) ? a : b;
            });

            if (count === 1) {
              notification.open({
                message: `🛡️ 新告警：${hottest.attack_type} 攻击`,
                description: `来源 IP: ${hottest.src_ip} → ${hottest.dst_ip} | 风险: ${hottest.risk_level} | ${hottest.timestamp}`,
                duration: 8,
                placement: 'topRight',
                btn: (
                  <Button size="small" type="primary" onClick={() => {
                    setSelectedAlert(hottest);
                    setDetailOpen(true);
                    notification.destroy();
                  }}>
                    查看详情
                  </Button>
                ),
              });
            } else {
              notification.open({
                message: `🛡️ 检测到 ${count} 条新告警`,
                description: `最高危：${hottest.attack_type} | 来源 ${hottest.src_ip} | 风险: ${hottest.risk_level}`,
                duration: 10,
                placement: 'topRight',
                btn: (
                  <Button size="small" type="primary" onClick={() => {
                    fetchAlerts();
                    notification.destroy();
                  }}>
                    查看全部
                  </Button>
                ),
              });
            }
            // 刷新告警列表
            fetchAlerts();
          }
        })
        .catch(() => {}); // 后端没响应时静默
    };

    const timer = setInterval(checkNewAlerts, 10000);
    return () => clearInterval(timer);
  }, [fetchAlerts]);

  // ---- Alert Actions ----
  const handleBlock = (record: AlertItem) => {
    api.blockIp(record.id)
      .then((res) => {
        message.success(res.message || `已拉黑 IP: ${record.src_ip}`);
        fetchAlerts();
        setSelectedAlert((prev) => prev?.id === record.id ? { ...prev, status: 'blocked' } : prev);
      })
      .catch(() => message.error('拉黑失败，请检查后端服务是否运行'));
  };

  const handleUnblock = (record: AlertItem) => {
    api.unblockIp(record.id)
      .then((res) => {
        message.success(res.message || `已解除对 ${record.src_ip} 的拉黑`);
        fetchAlerts();
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
      title: '来源', key: 'source', width: 70,
      render: (_, r) => {
        const d = r.description || '';
        if (d.startsWith('[real]')) return <Tag color="green">真实</Tag>;
        if (d.startsWith('[sim]')) return <Tag color="orange">仿真</Tag>;
        return <Tag color="default">-</Tag>;
      },
    },
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

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <h2 style={{ fontSize: 20, fontWeight: 600, margin: 0 }}>告警中心</h2>
        <Space>
          <Switch
            size="small"
            checked={merged}
            onChange={(v) => setMerged(v)}
          />
          <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>合并显示</span>
          <Button icon={<ExportOutlined />} onClick={() => {
            const params = new URLSearchParams();
            if (riskFilter !== 'all') params.set('risk_level', riskFilter);
            if (typeFilter !== 'all') params.set('attack_type', typeFilter);
            if (sourceFilter !== 'all') params.set('source', sourceFilter);
            if (merged) params.set('merged', 'true');
            window.open(`/api/export/excel?${params.toString()}`);
          }}>导出 Excel</Button>
        </Space>
      </div>

      {/* 筛选栏 */}
      <Space wrap size="small" style={{ marginBottom: 16 }}>
        <Select value={riskFilter} onChange={setRiskFilter} style={{ width: 140 }}
          options={[
            { value: 'all', label: '全部风险等级' },
            { value: 'critical', label: '高危' },
            { value: 'high', label: '中危' },
            { value: 'medium', label: '低危' },
          ]} />
        <Select value={typeFilter} onChange={setTypeFilter} style={{ width: 140 }}
          options={[
            { value: 'all', label: '全部攻击类型' },
            { value: 'Mirai', label: 'Mirai' },
            { value: 'Gafgyt', label: 'Gafgyt' },
            { value: 'Other', label: '其他攻击' },
          ]} />
        <Select value={timeFilter} onChange={setTimeFilter} style={{ width: 130 }}
          options={[
            { value: '1h', label: '最近1小时' },
            { value: '24h', label: '最近24小时' },
            { value: '7d', label: '最近7天' },
          ]} />
        <Select value={sourceFilter} onChange={setSourceFilter} style={{ width: 120 }}
          options={[
            { value: 'all', label: '全部来源' },
            { value: 'sim', label: '仿真' },
            { value: 'real', label: '真实' },
          ]} />
        <Button icon={<ReloadOutlined />} type="text" onClick={fetchAlerts} style={{ color: 'var(--text-secondary)' }}>
          刷新
        </Button>
      </Space>

      <Table
        columns={alertColumns}
        dataSource={alerts}
        rowKey="id"
        loading={loading}
        rowClassName={(record) => record.risk_level === 'critical' ? 'alert-row-critical' : ''}
        pagination={{
          current: page, pageSize, total,
          showSizeChanger: true, showTotal: (t) => `共 ${t} 条告警`,
          onChange: (p, ps) => { setPage(p); if (ps !== pageSize) setPageSize(ps); },
        }}
        size="middle"
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

    </div>
  );
}
