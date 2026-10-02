import { useEffect } from 'react';
import { render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { RequireDeviceRole, RequireIncidentRole } from './App';

const auth = vi.hoisted(() => ({
  authenticated: true,
  loading: false,
  role: 'admin',
  userId: 7,
  incidentWorkspaceCleanup: vi.fn(),
}));

vi.mock('./contexts/AuthContext', () => ({
  useAuth: () => ({
    authenticated: auth.authenticated,
    loading: auth.loading,
    isAdmin: auth.role === 'admin',
    canAccessMonitor: auth.role === 'admin' || auth.role === 'operator',
    user: { id: auth.userId, username: `${auth.role}-test`, role: auth.role },
  }),
  AuthProvider: ({ children }: { children: React.ReactNode }) => children,
}));

function IncidentWorkspaceProbe() {
  useEffect(() => () => auth.incidentWorkspaceCleanup(), []);
  return <div>事件处置工作区</div>;
}

function guardTree(path = '/devices') {
  return (
    <MemoryRouter
      initialEntries={[path]}
      future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
    >
      <Routes>
        <Route path="/login" element={<div>登录页</div>} />
        <Route path="/mobile-app-required" element={<div>普通用户请使用移动端</div>} />
        <Route
          path="/devices"
          element={<RequireDeviceRole><div>设备管理工作区</div></RequireDeviceRole>}
        />
        <Route
          path="/incidents"
          element={<RequireIncidentRole><IncidentWorkspaceProbe /></RequireIncidentRole>}
        />
      </Routes>
    </MemoryRouter>
  );
}

function renderGuard(path = '/devices') {
  return render(guardTree(path));
}

beforeEach(() => {
  auth.authenticated = true;
  auth.loading = false;
  auth.role = 'admin';
  auth.userId = 7;
  auth.incidentWorkspaceCleanup.mockClear();
});

describe('v3 management route guards', () => {
  it.each(['admin', 'operator'])('allows the %s role', (role) => {
    auth.role = role;
    renderGuard();
    expect(screen.getByText('设备管理工作区')).toBeInTheDocument();
  });

  it('rejects a user at the route boundary', () => {
    auth.role = 'user';
    renderGuard();
    expect(screen.getByText('普通用户请使用移动端')).toBeInTheDocument();
    expect(screen.queryByText('设备管理工作区')).not.toBeInTheDocument();
  });

  it('keeps anonymous visitors in the existing login flow', () => {
    auth.authenticated = false;
    auth.role = 'user';
    renderGuard();
    expect(screen.getByText('登录页')).toBeInTheDocument();
  });

  it.each(['admin', 'operator'])('allows %s into the incident workspace', (role) => {
    auth.role = role;
    renderGuard('/incidents');
    expect(screen.getByText('事件处置工作区')).toBeInTheDocument();
  });

  it('rejects a user at the incident route boundary', () => {
    auth.role = 'user';
    renderGuard('/incidents');
    expect(screen.getByText('普通用户请使用移动端')).toBeInTheDocument();
    expect(screen.queryByText('事件处置工作区')).not.toBeInTheDocument();
  });

  it('remounts the incident workspace when the authenticated user changes', () => {
    const view = renderGuard('/incidents');
    auth.userId = 8;
    view.rerender(guardTree('/incidents'));
    expect(auth.incidentWorkspaceCleanup).toHaveBeenCalledTimes(1);
    expect(screen.getByText('事件处置工作区')).toBeInTheDocument();
  });
});
