import { useEffect, useMemo, useState } from 'react';
import {
  Alert,
  Button,
  Card,
  Descriptions,
  Divider,
  Form,
  InputNumber,
  Select,
  Slider,
  Space,
  Switch,
  Tag,
  Upload,
  message,
} from 'antd';
import { ReloadOutlined, UploadOutlined } from '@ant-design/icons';
import { api, ModelItem } from '../../api';

function formatBytes(size: number) {
  if (!Number.isFinite(size)) return '-';
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / 1024 / 1024).toFixed(2)} MB`;
}

function formatUpdatedAt(timestamp: number) {
  if (!timestamp) return '-';
  return new Date(timestamp * 1000).toLocaleString();
}

export default function SettingsPage() {
  const [models, setModels] = useState<ModelItem[]>([]);
  const [activeModelId, setActiveModelId] = useState<string | null>(null);
  const [modelLoaded, setModelLoaded] = useState(false);
  const [loadingModels, setLoadingModels] = useState(false);
  const [switchingModel, setSwitchingModel] = useState(false);
  const [uploadingModel, setUploadingModel] = useState(false);

  const activeModel = useMemo(
    () => models.find((model) => model.id === activeModelId) || models.find((model) => model.active),
    [activeModelId, models],
  );

  const loadModels = async () => {
    setLoadingModels(true);
    try {
      const data = await api.getModels();
      setModels(data.items || []);
      setActiveModelId(data.active_model_id || null);
      setModelLoaded(data.model_loaded);
    } catch (error) {
      message.error('模型列表加载失败');
    } finally {
      setLoadingModels(false);
    }
  };

  useEffect(() => {
    loadModels();
  }, []);

  const handleSelectModel = async (modelId: string) => {
    setSwitchingModel(true);
    try {
      const result = await api.selectModel(modelId);
      if (result.success) {
        message.success(result.message || '模型切换成功');
        await loadModels();
      } else {
        message.error(result.message || '模型切换失败');
      }
    } catch (error) {
      message.error('模型切换失败，请确认后端服务正常');
    } finally {
      setSwitchingModel(false);
    }
  };

  const handleUploadModel = async (file: File) => {
    setUploadingModel(true);
    try {
      const result = await api.uploadModel(file);
      if (result.success) {
        message.success(result.message || '模型上传成功');
        await loadModels();
      } else {
        message.error(result.message || '模型上传失败');
      }
    } catch (error) {
      message.error('模型上传失败，请确认后端服务正常');
    } finally {
      setUploadingModel(false);
    }
    return false;
  };

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
          <Form.Item label="自动拉黑高危 IP">
            <Switch defaultChecked={false} />
          </Form.Item>
          <Form.Item>
            <Button type="primary">保存设置</Button>
          </Form.Item>
        </Form>
      </Card>

      <Card
        title="模型管理"
        extra={
          <Button icon={<ReloadOutlined />} loading={loadingModels} onClick={loadModels}>
            刷新
          </Button>
        }
        style={{ marginBottom: 16 }}
      >
        <Form layout="vertical" style={{ maxWidth: 680 }}>
          <Form.Item label="当前推理模型">
            <Space.Compact style={{ width: '100%' }}>
              <Select
                value={activeModel?.id}
                loading={loadingModels || switchingModel}
                placeholder="请选择模型"
                onChange={handleSelectModel}
                options={models.map((model) => ({
                  value: model.id,
                  label: `${model.name}${model.active ? '（当前）' : ''}`,
                }))}
              />
              <Upload
                accept=".onnx"
                showUploadList={false}
                beforeUpload={(file) => handleUploadModel(file)}
                disabled={uploadingModel}
              >
                <Button icon={<UploadOutlined />} loading={uploadingModel}>
                  上传 ONNX
                </Button>
              </Upload>
            </Space.Compact>
          </Form.Item>
        </Form>

        {!modelLoaded && (
          <Alert
            type="warning"
            showIcon
            message="当前模型未成功加载，系统会使用模拟推理结果。"
            style={{ marginBottom: 16 }}
          />
        )}

        <Descriptions column={2} size="small">
          <Descriptions.Item label="模型名称">{activeModel?.name || '-'}</Descriptions.Item>
          <Descriptions.Item label="状态">
            {modelLoaded ? <Tag color="green">已加载</Tag> : <Tag color="orange">未加载</Tag>}
          </Descriptions.Item>
          <Descriptions.Item label="文件名">{activeModel?.filename || '-'}</Descriptions.Item>
          <Descriptions.Item label="模型大小">{activeModel ? formatBytes(activeModel.size_bytes) : '-'}</Descriptions.Item>
          <Descriptions.Item label="更新时间">
            {activeModel ? formatUpdatedAt(activeModel.updated_at) : '-'}
          </Descriptions.Item>
          <Descriptions.Item label="模型格式">ONNX Runtime</Descriptions.Item>
          <Descriptions.Item label="输入维度">21 维社区特征集</Descriptions.Item>
          <Descriptions.Item label="输出类别">5 分类</Descriptions.Item>
          <Descriptions.Item label="框架">PyTorch → ONNX</Descriptions.Item>
          <Descriptions.Item label="推理延迟">&lt; 10 ms</Descriptions.Item>
          <Descriptions.Item label="基线准确率">99.91% (N-BaIoT)</Descriptions.Item>
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
        <p>IoT IDS v1.0 - 基于轻量化深度学习的智慧社区 IoT 僵尸网络入侵检测系统</p>
        <p>天津理工大学 · 计算机科学与工程学院 · 2026</p>
        <p>团队成员：李云锦 | 谢庚泉 | 李津涛 | 杨明敏 | 韦思杨 | 胡宇翔</p>
      </div>
    </div>
  );
}
