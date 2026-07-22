import { useState, useEffect } from 'react';
import { Card, Form, Select, Slider, InputNumber, Switch, Button, Divider, Descriptions, Space, message } from 'antd';
import { api } from '../../api';

export default function SettingsPage() {
  const [config, setConfig] = useState<any>({});
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    api.getConfig().then(setConfig).catch(() => {});
  }, []);

  const handleSave = () => {
    setLoading(true);
    api.updateConfig(config)
      .then(() => message.success('设置已保存'))
      .catch(() => message.error('保存失败'))
      .finally(() => setLoading(false));
  };

  const set = (key: string, val: any) => setConfig((c: any) => ({ ...c, [key]: val }));

  return (
    <div>
      <h2 style={{ marginBottom: 20, fontSize: 20, fontWeight: 600 }}>系统配置</h2>

      <Card title="检测设置" style={{ marginBottom: 16 }}>
        <Form layout="vertical" style={{ maxWidth: 600 }}>
          <Form.Item label="检测模式">
            <Select
              value={config.detection_mode || 'offline'}
              onChange={(v) => set('detection_mode', v)}
              options={[
                { value: 'offline', label: '离线文件检测' },
                { value: 'realtime', label: '实时网卡检测' },
              ]}
            />
          </Form.Item>
          <Form.Item label="置信度阈值">
            <Slider
              min={0.5} max={1.0} step={0.05}
              value={config.confidence_threshold || 0.85}
              onChange={(v) => set('confidence_threshold', v)}
              marks={{ 0.5: '0.5', 0.7: '0.7', 0.85: '0.85', 0.95: '0.95', 1.0: '1.0' }}
            />
          </Form.Item>
          <Form.Item label="告警合并时间窗口（分钟）">
            <InputNumber min={1} max={60}
              value={config.merge_window_minutes || 5}
              onChange={(v) => set('merge_window_minutes', v)} />
          </Form.Item>
          <Form.Item label="自动拉黑高危IP">
            <Switch
              checked={config.auto_block || false}
              onChange={(v) => set('auto_block', v)} />
          </Form.Item>
          <Form.Item>
            <Button type="primary" onClick={handleSave} loading={loading}>保存设置</Button>
          </Form.Item>
        </Form>
      </Card>

      <Card title="模型信息" style={{ marginBottom: 16 }}>
        <Descriptions column={2} size="small">
          <Descriptions.Item label="模型名称">CNN+LSTM 混合模型</Descriptions.Item>
          <Descriptions.Item label="框架">PyTorch → ONNX Runtime</Descriptions.Item>
          <Descriptions.Item label="输入特征">20 维（三层筛选）</Descriptions.Item>
          <Descriptions.Item label="输出分类">4 类（Normal/Mirai/Gafgyt/Other）</Descriptions.Item>
          <Descriptions.Item label="模型体积">67 KB (ONNX)</Descriptions.Item>
          <Descriptions.Item label="单条推理">1.2 ms</Descriptions.Item>
          <Descriptions.Item label="准确率">99.40% (BoT-IoT)</Descriptions.Item>
          <Descriptions.Item label="F1 值">99.40%</Descriptions.Item>
          <Descriptions.Item label="参数量">~104K (0.10M)</Descriptions.Item>
          <Descriptions.Item label="部署平台">树莓派 4B / Windows</Descriptions.Item>
        </Descriptions>
      </Card>

      <Card title="数据管理">
        <Form layout="vertical" style={{ maxWidth: 400 }}>
          <Form.Item label="历史数据保留天数">
            <InputNumber min={1} max={365} defaultValue={30} />
          </Form.Item>
          <Form.Item>
            <Space>
              <Button danger>清空历史记录</Button>
              <Button>导出全部数据</Button>
            </Space>
          </Form.Item>
        </Form>
      </Card>

      <Divider />
      <div style={{ textAlign: 'center', color: 'var(--text-muted)', fontSize: 13 }}>
        <p>IoT IDS v2.0 — 基于轻量化深度学习的智慧社区 IoT 僵尸网络入侵检测系统</p>
        <p>天津理工大学 · 计算机科学与工程学院 · 2026</p>
        <p>团队成员：李云锦 | 谢庚泉 | 李津涛 | 杨明敏 | 韦思杨 | 胡宇翔</p>
      </div>
    </div>
  );
}
