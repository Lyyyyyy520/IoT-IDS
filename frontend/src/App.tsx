import { Fragment, lazy, Suspense } from 'react';
import { Navigate, Route, Routes, useNavigate } from 'react-router-dom';
import { Button, Result, Spin } from 'antd';
import MainLayout from './layouts/MainLayout';
import LoginPage from './pages/Login';
import MonitorPage from './pages/Monitor';
import { AuthProvider, useAuth } from './contexts/AuthContext';

const DevicesPage = lazy(() => import('./pages/Devices'));
const MobileAccessPage = lazy(() => import('./pages/MobileAccess'));
const IncidentsPage = lazy(() => import('./pages/Incidents'));
const SystemHealthPage = lazy(() => import('./pages/SystemHealth'));

function LoadingScreen() {
  return (
    <div style={{ height: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'var(--bg-base)' }}>
      <Spin size="large" />
    </div>
  );
}

function RequireAuth({ children }: { children: React.ReactNode }) {
  const { authenticated, loading } = useAuth();
  if (loading) return <LoadingScreen />;
  if (!authenticated) return <Navigate to="/login" replace />;
  return <>{children}</>;
}

function RequireWebRole({ children }: { children: React.ReactNode }) {
  const { authenticated, loading, canAccessMonitor } = useAuth();
  if (loading) return <LoadingScreen />;
  if (!authenticated) return <Navigate to="/login" replace />;
  if (!canAccessMonitor) return <Navigate to="/mobile-app-required" replace />;
  return <>{children}</>;
}

export function RequireAdmin({ children }: { children: React.ReactNode }) {
  const { authenticated, loading, isAdmin } = useAuth();
  if (loading) return <LoadingScreen />;
  if (!authenticated) return <Navigate to="/login" replace />;
  if (!isAdmin) return <Navigate to="/monitor" replace />;
  return <>{children}</>;
}

export function RequireMonitorRole({ children }: { children: React.ReactNode }) {
  const { authenticated, loading, canAccessMonitor } = useAuth();
  if (loading) return <LoadingScreen />;
  if (!authenticated) return <Navigate to="/login" replace />;
  if (!canAccessMonitor) return <Navigate to="/mobile-app-required" replace />;
  return <>{children}</>;
}

export function RequireDeviceRole({ children }: { children: React.ReactNode }) {
  return <RequireMonitorRole>{children}</RequireMonitorRole>;
}

export function RequireIncidentRole({ children }: { children: React.ReactNode }) {
  const { authenticated, loading, canAccessMonitor, user } = useAuth();
  if (loading) return <LoadingScreen />;
  if (!authenticated) return <Navigate to="/login" replace />;
  if (!canAccessMonitor) return <Navigate to="/mobile-app-required" replace />;
  return <Fragment key={user?.id ?? 'anonymous'}>{children}</Fragment>;
}

function MobileOnlyNotice() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  return (
    <div className="page-container" style={{ display: 'grid', placeItems: 'center', minHeight: '100vh' }}>
      <Result
        status="403"
        title="普通用户请使用移动端"
        subTitle={`${user?.username ?? '此账号'} 只能通过配对后的 IoT-IDS 移动端查看本人获授权的设备与提醒。`}
        extra={<Button onClick={async () => { await logout(); navigate('/login', { replace: true }); }}>退出登录</Button>}
      />
    </div>
  );
}

function CurrentLanding() {
  return <Navigate to="/monitor" replace />;
}

function AppRoutes() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route
        path="/mobile-app-required"
        element={<RequireAuth><MobileOnlyNotice /></RequireAuth>}
      />
      <Route
        path="/"
        element={<RequireWebRole><MainLayout /></RequireWebRole>}
      >
        <Route index element={<CurrentLanding />} />
        <Route path="monitor" element={<MonitorPage />} />
        <Route path="devices" element={<RequireDeviceRole><Suspense fallback={<LoadingScreen />}><DevicesPage /></Suspense></RequireDeviceRole>} />
        <Route path="incidents" element={<RequireIncidentRole><Suspense fallback={<LoadingScreen />}><IncidentsPage /></Suspense></RequireIncidentRole>} />
        <Route path="system-health" element={<RequireMonitorRole><Suspense fallback={<LoadingScreen />}><SystemHealthPage /></Suspense></RequireMonitorRole>} />
        <Route path="mobile-access" element={<RequireAdmin><Suspense fallback={<LoadingScreen />}><MobileAccessPage /></Suspense></RequireAdmin>} />
      </Route>
      <Route path="/dashboard/*" element={<Navigate to="/monitor" replace />} />
      <Route path="/alerts/*" element={<Navigate to="/incidents" replace />} />
      <Route path="/assets/*" element={<Navigate to="/devices" replace />} />
      <Route path="/traffic/*" element={<Navigate to="/devices" replace />} />
      <Route path="/analysis/*" element={<Navigate to="/monitor" replace />} />
      <Route path="/policy/*" element={<Navigate to="/system-health" replace />} />
      <Route path="/logs/*" element={<Navigate to="/system-health" replace />} />
      <Route path="/settings/*" element={<Navigate to="/system-health" replace />} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}

export default function App() {
  return <AuthProvider><AppRoutes /></AuthProvider>;
}
