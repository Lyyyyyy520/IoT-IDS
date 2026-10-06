import { render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { RequireAdmin } from './App';

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
      initialEntries={['/mobile-access']}
      future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
    >
      <Routes>
        <Route path="/login" element={<div>登录页</div>} />
        <Route path="/monitor" element={<div>工作台</div>} />
        <Route
          path="/mobile-access"
          element={<RequireAdmin><div>APP 访问管理工作区</div></RequireAdmin>}
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

describe('mobile access administrator route boundary', () => {
  it('allows administrators', () => {
    renderGuard();
    expect(screen.getByText('APP 访问管理工作区')).toBeInTheDocument();
  });

  it.each(['operator', 'user'])('rejects the %s role before the page is rendered', (role) => {
    auth.role = role;
    renderGuard();
    expect(screen.getByText('工作台')).toBeInTheDocument();
    expect(screen.queryByText('APP 访问管理工作区')).not.toBeInTheDocument();
  });

  it('keeps anonymous visitors in the existing login flow', () => {
    auth.authenticated = false;
    renderGuard();
    expect(screen.getByText('登录页')).toBeInTheDocument();
  });
});
