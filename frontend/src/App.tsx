import { Fragment, lazy, Suspense } from 'react';
import { Routes, Route, Navigate } from 'react-router-dom';
import { Spin } from 'antd';
import MainLayout from './layouts/MainLayout';
import LoginPage from './pages/Login';
import Dashboard from './pages/Dashboard';
import Alerts from './pages/Alerts';
import Traffic from './pages/Traffic';
import Policy from './pages/Policy';
import Assets from './pages/Assets';
import Logs from './pages/Logs';
import Settings from './pages/Settings';
import MonitorPage from './pages/Monitor';
import { AuthProvider, useAuth } from './contexts/AuthContext';

const DevicesPage = lazy(() => import('./pages/Devices'));
const MobileAccessPage = lazy(() => import('./pages/MobileAccess'));
const IncidentsPage = lazy(() => import('./pages/Incidents'));

function LoadingScreen() {
  return (
    <div
      style={{
        height: '100vh',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        background: 'var(--bg-base)',
      }}
    >
      <Spin size="large" />
    </div>
  );
}

/** Redirect unauthenticated visitors to the login page. */
function RequireAuth({ children }: { children: React.ReactNode }) {
  const { authenticated, loading } = useAuth();

  if (loading) return <LoadingScreen />;
  if (!authenticated) return <Navigate to="/login" replace />;
  return <>{children}</>;
}

/** Administrator-only legacy pages; the backend remains the authority. */
export function RequireAdmin({ children }: { children: React.ReactNode }) {
  const { authenticated, loading, isAdmin } = useAuth();

  if (loading) return <LoadingScreen />;
  if (!authenticated) return <Navigate to="/login" replace />;
  if (!isAdmin) return <Navigate to="/dashboard" replace />;
  return <>{children}</>;
}

export function RequireMonitorRole({ children }: { children: React.ReactNode }) {
  const { authenticated, loading, canAccessMonitor } = useAuth();

  if (loading) return <LoadingScreen />;
  if (!authenticated) return <Navigate to="/login" replace />;
  if (!canAccessMonitor) return <Navigate to="/dashboard" replace />;
  return <>{children}</>;
}

export function RequireDeviceRole({ children }: { children: React.ReactNode }) {
  const { authenticated, loading, canAccessMonitor } = useAuth();

  if (loading) return <LoadingScreen />;
  if (!authenticated) return <Navigate to="/login" replace />;
  if (!canAccessMonitor) return <Navigate to="/dashboard" replace />;
  return <>{children}</>;
}

export function RequireIncidentRole({ children }: { children: React.ReactNode }) {
  const { authenticated, loading, canAccessMonitor, user } = useAuth();
  if (loading) return <LoadingScreen />;
  if (!authenticated) return <Navigate to="/login" replace />;
  if (!canAccessMonitor) return <Navigate to="/dashboard" replace />;
  return <Fragment key={user?.id ?? 'anonymous'}>{children}</Fragment>;
}

function AuthenticatedLanding() {
  const { canAccessMonitor } = useAuth();
  return <Navigate to={canAccessMonitor ? '/monitor' : '/dashboard'} replace />;
}

export default function App() {
  return (
    <AuthProvider>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route
          path="/monitor"
          element={
            <RequireMonitorRole>
              <MonitorPage />
            </RequireMonitorRole>
          }
        />

        <Route
          path="/"
          element={
            <RequireAuth>
              <MainLayout />
            </RequireAuth>
          }
        >
          <Route index element={<AuthenticatedLanding />} />

          {/* Visible to all authenticated users. */}
          <Route path="dashboard" element={<Dashboard />} />
          <Route path="alerts" element={<Alerts />} />
          <Route path="traffic" element={<Traffic />} />
          <Route path="assets" element={<Assets />} />
          <Route path="settings" element={<Settings />} />
          <Route
            path="devices"
            element={
              <RequireDeviceRole>
                <Suspense fallback={<LoadingScreen />}>
                  <DevicesPage />
                </Suspense>
              </RequireDeviceRole>
            }
          />
          <Route
            path="incidents"
            element={
              <RequireIncidentRole>
                <Suspense fallback={<LoadingScreen />}>
                  <IncidentsPage />
                </Suspense>
              </RequireIncidentRole>
            }
          />

          {/* Administrator-only pages. */}
          <Route path="policy" element={<RequireAdmin><Policy /></RequireAdmin>} />
          <Route path="logs" element={<RequireAdmin><Logs /></RequireAdmin>} />
          <Route
            path="mobile-access"
            element={
              <RequireAdmin>
                <Suspense fallback={<LoadingScreen />}>
                  <MobileAccessPage />
                </Suspense>
              </RequireAdmin>
            }
          />
        </Route>

        <Route path="*" element={<Navigate to="/dashboard" replace />} />
      </Routes>
    </AuthProvider>
  );
}
