import { useState, useEffect, useCallback } from 'react';
import { Table, Tag, Button, Space, Modal, Form, Input, Select, message, Popconfirm } from 'antd';
import { PlusOutlined, EditOutlined, DeleteOutlined } from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';
import { api } from '../../api';

const typeMap: Record<string, string> = { camera: '摄像头', door: '门禁', sensor: '传感器', router: '路由器', hub: '网关', socket: '插座', lock: '智能锁', other: '其他' };
const statusColor: Record<string, string> = { online: 'green', offline: 'default', alert: 'red' };
const statusLabel: Record<string, string> = { online: '在线', offline: '离线', alert: '告警' };
const riskColor: Record<string, string> = { critical: '#FF4444', high: '#FF8800', medium: '#FFCC00', low: '#00CC66' };
const riskLabel: Record<string, string> = { critical: '高危', high: '中危', medium: '低危', low: '安全' };

export default function AssetsPage() {
  const [assets, setAssets] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [modalOpen, setModalOpen] = useState(false);
  const [editing, setEditing] = useState<any>(null);
  const [form] = Form.useForm();

  const fetchAssets = useCallback(() => {
    setLoading(true);
    api.getAssets().then((res) => setAssets(res.items)).finally(() => setLoading(false));
  }, []);

  useEffect(() => { fetchAssets(); }, [fetchAssets]);

  const handleSave = () => {
    form.validateFields().then((values) => {
      const req = editing
        ? api.updateAsset(editing.id, values)
        : api.createAsset(values);
      req.then(() => {
        message.success(editing ? '设备已更新' : '设备已添加');
        setModalOpen(false);
        fetchAssets();
      });
    });
  };

  const handleDelete = (id: number) => {
    api.deleteAsset(id).then(() => { message.success('已删除'); fetchAssets(); });
  };

  const columns: ColumnsType<any> = [
    { title: '设备名称', dataIndex: 'name', width: 140 },
    { title: 'IP 地址', dataIndex: 'ip_address', width: 160 },
    { title: 'MAC 地址', dataIndex: 'mac_address', width: 160, render: (v: string) => v || '-' },
    {
      title: '设备类型', dataIndex: 'device_type', width: 100,
      render: (v: string) => typeMap[v] || v || '其他',
    },
    {
      title: '状态', dataIndex: 'status', width: 80,
      render: (v: string) => <Tag color={statusColor[v] || 'default'}>{statusLabel[v] || v}</Tag>,
    },
    {
      title: '风险等级', dataIndex: 'risk_level', width: 100,
      render: (v: string) => <Tag color={riskColor[v]}>{riskLabel[v] || v}</Tag>,
    },
    { title: '最后在线', dataIndex: 'last_seen', width: 170 },
    {
      title: '操作', width: 120,
      render: (_, r) => (
        <Space>
          <Button size="small" icon={<EditOutlined />} type="text" onClick={() => { setEditing(r); form.setFieldsValue(r); setModalOpen(true); }} />
          <Popconfirm title="确定删除？" onConfirm={() => handleDelete(r.id)}>
            <Button size="small" icon={<DeleteOutlined />} type="text" danger />
          </Popconfirm>
        </Space>
      ),
    },
  ];

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 16 }}>
        <h2 style={{ fontSize: 20, fontWeight: 600, margin: 0 }}>资产监控</h2>
        <Button type="primary" icon={<PlusOutlined />} onClick={() => { setEditing(null); form.resetFields(); setModalOpen(true); }}>
          添加设备
        </Button>
      </div>

      <div style={{ display: 'flex', gap: 12, marginBottom: 16 }}>
        {[
          { label: '全部', key: '' },
          { label: '在线', key: 'online' },
          { label: '离线', key: 'offline' },
          { label: '告警', key: 'alert' },
        ].map((t) => (
          <Tag key={t.key} color={t.key === 'online' ? 'green' : t.key === 'offline' ? 'default' : t.key === 'alert' ? 'red' : 'blue'}
            style={{ cursor: 'pointer' }}>
            {t.label}: {assets.filter((a) => !t.key || a.status === t.key).length}
          </Tag>
        ))}
      </div>

      <Table columns={columns} dataSource={assets} rowKey="id" loading={loading} size="middle"
        rowClassName={(r) => r.status === 'alert' ? 'alert-row-critical' : ''}
        pagination={{ pageSize: 20 }} />

      <Modal title={editing ? '编辑设备' : '添加设备'} open={modalOpen}
        onCancel={() => setModalOpen(false)} onOk={handleSave} width={480}>
        <Form form={form} layout="vertical" initialValues={{ device_type: 'other', status: 'online', risk_level: 'low' }}>
          <Form.Item label="设备名称" name="name" rules={[{ required: true }]}>
            <Input placeholder="例: 摄像头-01" />
          </Form.Item>
          <Form.Item label="IP 地址" name="ip_address" rules={[{ required: true }]}>
            <Input placeholder="例: 192.168.1.10" />
          </Form.Item>
          <Form.Item label="MAC 地址" name="mac_address">
            <Input placeholder="例: 00:1a:2b:3c:4d:01" />
          </Form.Item>
          <Form.Item label="设备类型" name="device_type">
            <Select options={Object.entries(typeMap).map(([k, v]) => ({ value: k, label: v }))} />
          </Form.Item>
          <Form.Item label="状态" name="status">
            <Select options={[
              { value: 'online', label: '在线' }, { value: 'offline', label: '离线' }, { value: 'alert', label: '告警' },
            ]} />
          </Form.Item>
          <Form.Item label="风险等级" name="risk_level">
            <Select options={[
              { value: 'critical', label: '高危' }, { value: 'high', label: '中危' }, { value: 'medium', label: '低危' }, { value: 'low', label: '安全' },
            ]} />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
