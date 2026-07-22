import { useState } from 'react';
import { Outlet, useNavigate, useLocation } from 'react-router-dom';
import { Layout, Menu, Button, Select, DatePicker, Space, Tag, Dropdown } from 'antd';
import type { MenuProps } from 'antd';
import {
  DashboardOutlined,
  AlertOutlined,
  RadarChartOutlined,
  SettingOutlined,
  MenuFoldOutlined,
  MenuUnfoldOutlined,
  ReloadOutlined,
  UserOutlined,
  LogoutOutlined,
  ClusterOutlined,
  SafetyOutlined,
  FileTextOutlined,
  SwapOutlined,
  MonitorOutlined,
} from '@ant-design/icons';
import { useAuth } from '../contexts/AuthContext';

const { Sider, Content } = Layout;

type MenuItem = Required<MenuProps>['items'][number];

const navItems: MenuItem[] = [
  { key: '/dashboard', icon: <DashboardOutlined />, label: '态势大屏' },
  { key: '/alerts', icon: <AlertOutlined />, label: '告警中心' },
  { key: '/analysis', icon: <RadarChartOutlined />, label: '分析视图' },
  { key: '/traffic', icon: <SwapOutlined />, label: '流量分析' },
  { key: '/policy', icon: <SafetyOutlined />, label: '策略管理' },
  { key: '/assets', icon: <MonitorOutlined />, label: '资产监控' },
  { key: '/logs', icon: <FileTextOutlined />, label: '审计日志' },
  { key: '/settings', icon: <SettingOutlined />, label: '系统设置' },
];

export default function MainLayout() {
  const [collapsed, setCollapsed] = useState(false);
  const navigate = useNavigate();
  const location = useLocation();
  const { user, logout } = useAuth();

  const currentKey = '/' + location.pathname.split('/')[1];

  const handleLogout = async () => {
    await logout();
    navigate('/login', { replace: true });
  };

  const userMenuItems: MenuProps['items'] = [
    {
      key: 'role',
      label: (
        <span style={{ color: 'var(--text-muted)', fontSize: 12 }}>
          {user?.role === 'admin' ? '超级管理员' : '只读访客'}
        </span>
      ),
      disabled: true,
    },
    { type: 'divider' },
    {
      key: 'logout',
      icon: <LogoutOutlined />,
      label: '退出登录',
      danger: true,
      onClick: handleLogout,
    },
  ];

  return (
    <Layout style={{ height: '100vh' }}>
      {/* ---- Sidebar ---- */}
      <Sider
        collapsible
        collapsed={collapsed}
        onCollapse={setCollapsed}
        trigger={null}
        width={240}
        style={{ borderRight: '1px solid var(--border-color)', overflow: 'auto' }}
      >
        {/* Logo */}
        <div
          style={{
            height: 64,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            borderBottom: '1px solid var(--border-color)',
            cursor: 'pointer',
          }}
          onClick={() => navigate('/dashboard')}
        >
          {collapsed ? (
            <span style={{ color: 'var(--accent-cyan)', fontSize: 20, fontWeight: 700 }}>ID</span>
          ) : (
            <span style={{ color: 'var(--accent-cyan)', fontSize: 16, fontWeight: 700 }}>
              🛡️ IoT IDS
            </span>
          )}
        </div>

        <Menu
          theme="dark"
          mode="inline"
          selectedKeys={[currentKey]}
          items={navItems}
          onClick={({ key }) => navigate(key)}
          style={{ borderInlineEnd: 'none', marginTop: 8 }}
        />

        {/* Bottom zone */}
        <div style={{ position: 'absolute', bottom: 16, width: '100%' }}>
          {/* User info + logout */}
          {!collapsed && user && (
            <div
              style={{
                padding: '8px 16px',
                borderTop: '1px solid var(--border-color)',
                marginBottom: 8,
              }}
            >
              <Dropdown menu={{ items: userMenuItems }} trigger={['click']} placement="topRight">
                <div
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: 8,
                    cursor: 'pointer',
                    padding: '4px 8px',
                    borderRadius: 6,
                    transition: 'background 0.2s',
                  }}
                >
                  <div
                    style={{
                      width: 32,
                      height: 32,
                      borderRadius: '50%',
                      background: 'var(--accent-blue)',
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      fontSize: 14,
                      color: '#fff',
                    }}
                  >
                    {user.username.charAt(0).toUpperCase()}
                  </div>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ fontSize: 13, color: 'var(--text-primary)', lineHeight: 1.3 }}>
                      {user.username}
                    </div>
                    <div style={{ fontSize: 11, color: 'var(--text-muted)' }}>
                      {user.role === 'admin' ? '管理员' : '访客'}
                    </div>
                  </div>
                </div>
              </Dropdown>
            </div>
          )}

          {/* Collapse button */}
          <div style={{ textAlign: 'center' }}>
            <Button
              type="text"
              icon={collapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />}
              onClick={() => setCollapsed(!collapsed)}
              style={{ color: 'var(--text-secondary)', fontSize: 16 }}
            />
          </div>
        </div>
      </Sider>

      {/* ---- Main Area ---- */}
      <Layout>
        {/* Top Filter Bar */}
        <div className="filter-bar">
          <Space wrap size="small">
            <Select
              defaultValue="all"
              style={{ width: 140 }}
              options={[
                { value: 'all', label: '全部风险等级' },
                { value: 'critical', label: '🔴 高危' },
                { value: 'high', label: '🟠 中危' },
                { value: 'medium', label: '🟡 低危' },
                { value: 'low', label: '🟢 安全' },
              ]}
            />
            <Select
              defaultValue="all"
              style={{ width: 140 }}
              options={[
                { value: 'all', label: '全部攻击类型' },
                { value: 'Mirai', label: 'Mirai' },
                { value: 'Gafgyt', label: 'Gafgyt' },
                { value: 'Other', label: '其他攻击' },
              ]}
            />
            <Select
              defaultValue="24h"
              style={{ width: 130 }}
              options={[
                { value: '1h', label: '最近1小时' },
                { value: '24h', label: '最近24小时' },
                { value: '7d', label: '最近7天' },
              ]}
            />
            <Button icon={<ReloadOutlined />} type="text" style={{ color: 'var(--text-secondary)' }}>
              刷新
            </Button>
          </Space>
          {/* Right-side: logout shortcut */}
          <div style={{ marginLeft: 'auto' }}>
            <Button
              type="text"
              icon={<LogoutOutlined />}
              onClick={handleLogout}
              style={{ color: 'var(--text-muted)', fontSize: 12 }}
            >
              退出
            </Button>
          </div>
        </div>

        {/* Page Content */}
        <Content className="page-container">
          <Outlet />
        </Content>
      </Layout>
    </Layout>
  );
}
