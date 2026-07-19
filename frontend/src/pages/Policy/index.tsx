import { useState, useEffect, useCallback } from 'react';
import { Table, Tag, Button, Space, Modal, Form, Input, Select, Switch, message, Popconfirm } from 'antd';
import { PlusOutlined, EditOutlined, DeleteOutlined } from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';
import { api } from '../../api';

const typeMap: Record<string, string> = { blacklist: '黑名单', whitelist: '白名单', rule: '防护规则' };
const actionMap: Record<string, string> = { alert: '告警', block: '阻断', allow: '放行' };

export default function PolicyPage() {
  const [policies, setPolicies] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [modalOpen, setModalOpen] = useState(false);
  const [editing, setEditing] = useState<any>(null);
  const [form] = Form.useForm();
  const [filter, setFilter] = useState('');

  const fetchPolicies = useCallback(() => {
    setLoading(true);
    api.getPolicies(filter)
      .then((res) => setPolicies(res.items))
      .finally(() => setLoading(false));
  }, [filter]);

  useEffect(() => { fetchPolicies(); }, [fetchPolicies]);

  const handleSave = () => {
    form.validateFields().then((values) => {
      const req = editing
        ? api.updatePolicy(editing.id, values)
        : api.createPolicy(values);
      req.then(() => {
        message.success(editing ? '策略已更新' : '策略已创建');
        setModalOpen(false);
        fetchPolicies();
      });
    });
  };

  const handleDelete = (id: number) => {
    api.deletePolicy(id).then(() => { message.success('已删除'); fetchPolicies(); });
  };

  const columns: ColumnsType<any> = [
    {
      title: '策略类型', dataIndex: 'policy_type', width: 100,
      render: (v: string) => {
        const colors: Record<string, string> = { blacklist: 'red', whitelist: 'green', rule: 'blue' };
        return <Tag color={colors[v]}>{typeMap[v] || v}</Tag>;
      },
    },
    { title: '目标', dataIndex: 'target', width: 180 },
    {
      title: '动作', dataIndex: 'action', width: 80,
      render: (v: string) => <Tag>{actionMap[v] || v}</Tag>,
    },
    { title: '描述', dataIndex: 'description', ellipsis: true },
    {
      title: '状态', dataIndex: 'enabled', width: 80,
      render: (v: number, r: any) => (
        <Switch size="small" checked={!!v} onChange={(checked) => api.updatePolicy(r.id, { ...r, enabled: checked ? 1 : 0 }).then(() => fetchPolicies())} />
      ),
    },
    { title: '创建时间', dataIndex: 'created_at', width: 170 },
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
        <h2 style={{ fontSize: 20, fontWeight: 600, margin: 0 }}>策略管理</h2>
        <Space>
          <Select
            placeholder="筛选类型" allowClear style={{ width: 120 }}
            value={filter}
            onChange={(v) => setFilter(v)}
            options={[
              { value: '', label: '全部' },
              { value: 'blacklist', label: '黑名单' },
              { value: 'whitelist', label: '白名单' },
              { value: 'rule', label: '防护规则' },
            ]}
          />
          <Button type="primary" icon={<PlusOutlined />} onClick={() => { setEditing(null); form.resetFields(); setModalOpen(true); }}>
            新增策略
          </Button>
        </Space>
      </div>

      <Table columns={columns} dataSource={policies} rowKey="id" loading={loading} size="middle"
        pagination={{ pageSize: 20 }} />

      <Modal title={editing ? '编辑策略' : '新增策略'} open={modalOpen}
        onCancel={() => setModalOpen(false)} onOk={handleSave} width={480}>
        <Form form={form} layout="vertical" initialValues={{ policy_type: 'blacklist', action: 'block', enabled: 1 }}>
          <Form.Item label="策略类型" name="policy_type" rules={[{ required: true }]}>
            <Select options={[
              { value: 'blacklist', label: '黑名单' },
              { value: 'whitelist', label: '白名单' },
              { value: 'rule', label: '防护规则' },
            ]} />
          </Form.Item>
          <Form.Item label="目标" name="target" rules={[{ required: true, message: '请输入 IP 地址或规则' }]}>
            <Input placeholder="例: 192.168.1.100" />
          </Form.Item>
          <Form.Item label="动作" name="action">
            <Select options={[
              { value: 'block', label: '阻断' },
              { value: 'alert', label: '告警' },
              { value: 'allow', label: '放行' },
            ]} />
          </Form.Item>
          <Form.Item label="描述" name="description">
            <Input placeholder="策略说明" />
          </Form.Item>
          <Form.Item label="启用" name="enabled" valuePropName="checked">
            <Switch />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
