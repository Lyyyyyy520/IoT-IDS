import { render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { RequireDeviceRole } from './App';

const auth = vi.hoisted(() => ({
  authenticated: true,
  loading: false,
  role: 'admin',
}));

vi.mock('./contexts/AuthContext', () => ({
  useAuth: () => ({
    authenticated: auth.authenticated,
    loading: auth.loading,
    isAdmin: auth.role === 'admin',
    canAccessMonitor: auth.role === 'admin' || auth.role === 'operator',
  }),
  AuthProvider: ({ children }: { children: React.ReactNode }) => children,
}));

function renderGuard() {
  return render(
    <MemoryRouter
      initialEntries={['/devices']}
      future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
    >
      <Routes>
        <Route path="/login" element={<div>登录页</div>} />
        <Route path="/dashboard" element={<div>普通用户首页</div>} />
        <Route
          path="/devices"
          element={<RequireDeviceRole><div>设备管理工作区</div></RequireDeviceRole>}
        />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  auth.authenticated = true;
  auth.loading = false;
  auth.role = 'admin';
});

describe('RequireDeviceRole', () => {
  it.each(['admin', 'operator'])('allows the %s role', (role) => {
    auth.role = role;
    renderGuard();
    expect(screen.getByText('设备管理工作区')).toBeInTheDocument();
  });

  it('rejects a user at the route boundary', () => {
    auth.role = 'user';
    renderGuard();
    expect(screen.getByText('普通用户首页')).toBeInTheDocument();
    expect(screen.queryByText('设备管理工作区')).not.toBeInTheDocument();
  });

  it('keeps anonymous visitors in the existing login flow', () => {
    auth.authenticated = false;
    auth.role = 'user';
    renderGuard();
    expect(screen.getByText('登录页')).toBeInTheDocument();
  });
});
