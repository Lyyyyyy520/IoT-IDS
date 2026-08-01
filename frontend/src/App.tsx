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
import { AuthProvider, useAuth } from './contexts/AuthContext';

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

/** Only the account named "admin" may enter administrator pages. */
function RequireAdmin({ children }: { children: React.ReactNode }) {
  const { authenticated, loading, isAdmin } = useAuth();

  if (loading) return <LoadingScreen />;
  if (!authenticated) return <Navigate to="/login" replace />;
  if (!isAdmin) return <Navigate to="/dashboard" replace />;
  return <>{children}</>;
}

export default function App() {
  return (
    <AuthProvider>
      <Routes>
        <Route path="/login" element={<LoginPage />} />

        <Route
          path="/"
          element={
            <RequireAuth>
              <MainLayout />
            </RequireAuth>
          }
        >
          <Route index element={<Navigate to="/dashboard" replace />} />

          {/* Visible to all authenticated users. */}
          <Route path="dashboard" element={<Dashboard />} />
          <Route path="alerts" element={<Alerts />} />
          <Route path="traffic" element={<Traffic />} />
          <Route path="assets" element={<Assets />} />
          <Route path="settings" element={<Settings />} />

          {/* Administrator-only pages. */}
          <Route path="policy" element={<RequireAdmin><Policy /></RequireAdmin>} />
          <Route path="logs" element={<RequireAdmin><Logs /></RequireAdmin>} />
        </Route>

        <Route path="*" element={<Navigate to="/dashboard" replace />} />
      </Routes>
    </AuthProvider>
  );
}
