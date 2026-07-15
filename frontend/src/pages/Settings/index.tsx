import { Card, Form, Select, Slider, InputNumber, Switch, Button, Divider, Descriptions, Space } from 'antd';

export default function SettingsPage() {
  return (
    <div>
      <h2 style={{ marginBottom: 20, fontSize: 20, fontWeight: 600 }}>系统配置</h2>

      <Card title="检测设置" style={{ marginBottom: 16 }}>
        <Form layout="vertical" style={{ maxWidth: 600 }}>
          <Form.Item label="检测模式">
            <Select
              defaultValue="offline"
              options={[
                { value: 'offline', label: '离线文件检测' },
                { value: 'realtime', label: '实时网卡检测' },
              ]}
            />
          </Form.Item>
          <Form.Item label="置信度阈值">
            <Slider
              min={0.5}
              max={1.0}
              step={0.05}
              defaultValue={0.85}
              marks={{ 0.5: '0.5', 0.7: '0.7', 0.85: '0.85', 0.95: '0.95', 1.0: '1.0' }}
            />
          </Form.Item>
          <Form.Item label="告警合并时间窗口（分钟）">
            <InputNumber min={1} max={60} defaultValue={5} />
          </Form.Item>
          <Form.Item label="自动拉黑高危IP">
            <Switch defaultChecked={false} />
          </Form.Item>
          <Form.Item>
            <Button type="primary">保存设置</Button>
          </Form.Item>
        </Form>
      </Card>

      <Card title="模型信息" style={{ marginBottom: 16 }}>
        <Descriptions column={2} size="small">
          <Descriptions.Item label="模型名称">CNN-LSTM-Light</Descriptions.Item>
          <Descriptions.Item label="框架">PyTorch → ONNX</Descriptions.Item>
          <Descriptions.Item label="输入维度">21 维社区特征集</Descriptions.Item>
          <Descriptions.Item label="输出">5 分类</Descriptions.Item>
          <Descriptions.Item label="模型体积">~2.5 MB</Descriptions.Item>
          <Descriptions.Item label="推理延迟">&lt; 10 ms</Descriptions.Item>
          <Descriptions.Item label="准确率">99.91% (N-BaIoT)</Descriptions.Item>
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
        <p>IoT IDS v1.0 — 基于轻量化深度学习的智慧社区 IoT 僵尸网络入侵检测系统</p>
        <p>天津理工大学 · 计算机科学与工程学院 · 2026</p>
        <p>团队成员：李云锦 | 谢庚泉 | 李津涛 | 杨明敏 | 韦思杨 | 胡宇翔</p>
      </div>
    </div>
  );
}

