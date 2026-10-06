import { act, render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import MainLayout from './MainLayout';

const auth = vi.hoisted(() => ({ role: 'admin' }));

vi.mock('../contexts/AuthContext', () => ({
  useAuth: () => ({
    user: { username: 'tester', role: auth.role },
    isAdmin: auth.role === 'admin',
    canAccessMonitor: auth.role === 'admin' || auth.role === 'operator',
    logout: vi.fn(),
  }),
}));

async function renderLayout() {
  await act(async () => {
    render(
      <MemoryRouter
        initialEntries={['/dashboard']}
        future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
      >
        <Routes>
          <Route path="/" element={<MainLayout />}>
            <Route path="dashboard" element={<div>内容</div>} />
          </Route>
        </Routes>
      </MemoryRouter>,
    );
    await Promise.resolve();
  });
}

beforeEach(() => {
  auth.role = 'admin';
});

describe('MainLayout system health navigation', () => {
  it.each(['admin', 'operator'])('shows health workspace to %s', async (role) => {
    auth.role = role;
    await renderLayout();
    expect(screen.getByText('系统健康')).toBeInTheDocument();
  });

  it('hides health workspace from regular users', async () => {
    auth.role = 'user';
    await renderLayout();
    expect(screen.queryByText('系统健康')).not.toBeInTheDocument();
  });
});

describe('MainLayout mobile access navigation', () => {
  it('shows APP access administration only to administrators', async () => {
    await renderLayout();
    expect(screen.getByText('移动用户管理')).toBeInTheDocument();
  });

  it.each(['operator', 'user'])('does not render the entry for %s', async (role) => {
    auth.role = role;
    await renderLayout();
    expect(screen.queryByText('移动用户管理')).not.toBeInTheDocument();
  });
});
