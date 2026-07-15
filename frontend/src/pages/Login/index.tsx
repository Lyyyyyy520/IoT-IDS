import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Form, Input, Button, Card, message, Typography } from 'antd';
import { UserOutlined, LockOutlined, SafetyCertificateOutlined } from '@ant-design/icons';
import { useAuth } from '../../contexts/AuthContext';

const { Title, Text } = Typography;

export default function LoginPage() {
  const [loading, setLoading] = useState(false);
  const { login } = useAuth();
  const navigate = useNavigate();

  const onFinish = async (values: { username: string; password: string }) => {
    setLoading(true);
    const result = await login(values.username, values.password);
    setLoading(false);
    if (result.success) {
      message.success('登录成功');
      navigate('/dashboard', { replace: true });
    } else {
      message.error(result.message || '登录失败');
    }
  };

  return (
    <div
      style={{
        height: '100vh',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        background: 'linear-gradient(135deg, #0D1117 0%, #161B22 50%, #0D2137 100%)',
        position: 'relative',
        overflow: 'hidden',
      }}
    >
      {/* Background decorative grid */}
      <div
        style={{
          position: 'absolute',
          inset: 0,
          backgroundImage: `linear-gradient(rgba(48,54,61,0.1) 1px, transparent 1px),
                            linear-gradient(90deg, rgba(48,54,61,0.1) 1px, transparent 1px)`,
          backgroundSize: '60px 60px',
          pointerEvents: 'none',
        }}
      />

      {/* Animated circle decorations */}
      <div
        style={{
          position: 'absolute',
          width: 400, height: 400,
          borderRadius: '50%',
          background: 'radial-gradient(circle, rgba(88,166,255,0.08) 0%, transparent 70%)',
          top: -100, right: -100,
          pointerEvents: 'none',
        }}
      />
      <div
        style={{
          position: 'absolute',
          width: 300, height: 300,
          borderRadius: '50%',
          background: 'radial-gradient(circle, rgba(255,68,68,0.06) 0%, transparent 70%)',
          bottom: -80, left: -80,
          pointerEvents: 'none',
        }}
      />

      {/* Login Card */}
      <Card
        style={{
          width: 420,
          border: '1px solid var(--border-color)',
          boxShadow: '0 8px 32px rgba(0,0,0,0.5)',
          zIndex: 1,
        }}
        bodyStyle={{ padding: '40px 36px' } as any}
      >
        <div style={{ textAlign: 'center', marginBottom: 32 }}>
          <SafetyCertificateOutlined
            style={{ fontSize: 48, color: 'var(--accent-cyan)', marginBottom: 12 }}
          />
          <Title level={3} style={{ color: 'var(--text-primary)', margin: 0 }}>
            IoT IDS
          </Title>
          <Text style={{ color: 'var(--text-secondary)', fontSize: 13 }}>
            智慧社区入侵检测管理平台
          </Text>
        </div>

        <Form onFinish={onFinish} size="large">
          <Form.Item
            name="username"
            rules={[{ required: true, message: '请输入账号' }]}
          >
            <Input
              prefix={<UserOutlined style={{ color: 'var(--text-muted)' }} />}
              placeholder="账号"
              autoComplete="username"
            />
          </Form.Item>
          <Form.Item
            name="password"
            rules={[{ required: true, message: '请输入密码' }]}
          >
            <Input.Password
              prefix={<LockOutlined style={{ color: 'var(--text-muted)' }} />}
              placeholder="密码"
              autoComplete="current-password"
            />
          </Form.Item>
          <Form.Item style={{ marginBottom: 12 }}>
            <Button
              type="primary"
              htmlType="submit"
              loading={loading}
              block
              style={{ height: 44, fontSize: 15 }}
            >
              登 录
            </Button>
          </Form.Item>
        </Form>

        <div
          style={{
            textAlign: 'center',
            color: 'var(--text-muted)',
            fontSize: 12,
            marginTop: 16,
          }}
        >
          <div>演示账号：admin / admin123</div>
          <div style={{ marginTop: 4 }}>只读账号：guest / guest123</div>
        </div>
      </Card>

      {/* Footer */}
      <div
        style={{
          position: 'absolute',
          bottom: 20,
          color: 'var(--text-muted)',
          fontSize: 11,
          textAlign: 'center',
        }}
      >
        天津理工大学 · 计算机科学与工程学院 · 2026
      </div>
    </div>
  );
}
