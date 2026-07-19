import { useState, useEffect, useCallback } from 'react';
import { Table, Tag } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { api } from '../../api';

export default function LogsPage() {
  const [data, setData] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);

  const fetch = useCallback(() => {
    api.getAuditLogs().then((res) => setData(res.items)).finally(() => setLoading(false));
  }, []);
  useEffect(() => { fetch(); }, [fetch]);

  const columns: ColumnsType<any> = [
    { title: '用户', dataIndex: 'username', width: 100 },
    {
      title: '操作', dataIndex: 'action', width: 140,
      render: (v: string) => {
        const map: Record<string, string> = {
          login: '登录', logout: '退出', block_ip: '拉黑IP', unblock_ip: '解除拉黑',
          mark_fp: '标记误报', unmark_fp: '撤销误报', trace_alert: '溯源分析',
          create_policy: '新增策略', update_policy: '编辑策略', delete_policy: '删除策略',
          capture_start: '开始抓包', capture_stop: '停止抓包',
        };
        return <Tag>{map[v] || v}</Tag>;
      },
    },
    { title: '详情', dataIndex: 'detail', ellipsis: true },
    { title: 'IP', dataIndex: 'ip_address', width: 140, render: (v: string) => v || '-' },
    { title: '时间', dataIndex: 'created_at', width: 170 },
  ];

  return (
    <div>
      <h2 style={{ fontSize: 20, fontWeight: 600, margin: '0 0 16px' }}>审计日志</h2>
      <Table columns={columns} dataSource={data} rowKey="id" loading={loading} size="middle"
        pagination={{ pageSize: 20, showTotal: (t) => `共 ${t} 条` }} />
    </div>
  );
}
