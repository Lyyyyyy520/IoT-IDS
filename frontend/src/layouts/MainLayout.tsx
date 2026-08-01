import { useState } from 'react';
import { Outlet, useNavigate, useLocation } from 'react-router-dom';
import { Layout, Menu, Button, Dropdown } from 'antd';
import type { MenuProps } from 'antd';
import {
  DashboardOutlined,
  AlertOutlined,
  SettingOutlined,
  MenuFoldOutlined,
  MenuUnfoldOutlined,
  LogoutOutlined,
  SafetyOutlined,
  FileTextOutlined,
  SwapOutlined,
  MonitorOutlined,
} from '@ant-design/icons';
import { useAuth } from '../contexts/AuthContext';

const { Sider, Content } = Layout;

type MenuItem = Required<MenuProps>['items'][number];

const userNavItems: MenuItem[] = [
  { key: '/dashboard', icon: <DashboardOutlined />, label: '态势大屏' },
  { key: '/alerts', icon: <AlertOutlined />, label: '告警中心' },
  { key: '/traffic', icon: <SwapOutlined />, label: '流量分析' },
  { key: '/assets', icon: <MonitorOutlined />, label: '资产监控' },
  { key: '/settings', icon: <SettingOutlined />, label: '系统设置' },
];

const adminNavItems: MenuItem[] = [
  { key: '/dashboard', icon: <DashboardOutlined />, label: '态势大屏' },
  { key: '/alerts', icon: <AlertOutlined />, label: '告警中心' },
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
  const { user, isAdmin, logout } = useAuth();

  const currentKey = '/' + location.pathname.split('/')[1];
  const navItems = isAdmin ? adminNavItems : userNavItems;

  const handleLogout = async () => {
    await logout();
    navigate('/login', { replace: true });
  };

  const userMenuItems: MenuProps['items'] = [
    {
      key: 'role',
      label: (
        <span style={{ color: 'var(--text-muted)', fontSize: 12 }}>
          {isAdmin ? '管理员' : '普通用户'}
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
      <Sider
        collapsible
        collapsed={collapsed}
        onCollapse={setCollapsed}
        trigger={null}
        width={240}
        style={{ borderRight: '1px solid var(--border-color)', overflow: 'auto' }}
      >
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

        <div style={{ position: 'absolute', bottom: 16, width: '100%' }}>
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
                      {isAdmin ? '管理员' : '用户'}
                    </div>
                  </div>
                </div>
              </Dropdown>
            </div>
          )}

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

      <Layout>
        <Content className="page-container">
          <Outlet />
        </Content>
      </Layout>
    </Layout>
  );
}
