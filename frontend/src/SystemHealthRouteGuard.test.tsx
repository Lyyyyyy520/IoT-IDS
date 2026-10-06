import { render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { RequireMonitorRole } from './App';

const auth = vi.hoisted(() => ({ authenticated: true, loading: false, role: 'admin' }));
vi.mock('./contexts/AuthContext', () => ({
  useAuth: () => ({
    authenticated: auth.authenticated,
    loading: auth.loading,
    isAdmin: auth.role === 'admin',
    canAccessMonitor: auth.role === 'admin' || auth.role === 'operator',
    user: { id: 1, username: 'test', role: auth.role },
  }),
  AuthProvider: ({ children }: { children: React.ReactNode }) => children,
}));

function renderRoute() {
  return render(
    <MemoryRouter initialEntries={['/system-health']} future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
      <Routes>
        <Route path="/login" element={<div>登录页</div>} />
        <Route path="/mobile-app-required" element={<div>普通用户请使用移动端</div>} />
        <Route path="/system-health" element={<RequireMonitorRole><div>系统健康页面</div></RequireMonitorRole>} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  auth.authenticated = true;
  auth.loading = false;
  auth.role = 'admin';
});

describe('system health route access', () => {
  it.each(['admin', 'operator'])('allows %s', (role) => {
    auth.role = role;
    renderRoute();
    expect(screen.getByText('系统健康页面')).toBeInTheDocument();
  });

  it('rejects user at the route boundary', () => {
    auth.role = 'user';
    renderRoute();
    expect(screen.getByText('普通用户请使用移动端')).toBeInTheDocument();
    expect(screen.queryByText('系统健康页面')).not.toBeInTheDocument();
  });

  it('redirects anonymous visitors to login', () => {
    auth.authenticated = false;
    renderRoute();
    expect(screen.getByText('登录页')).toBeInTheDocument();
  });
});
