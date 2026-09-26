import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { MobileAccessApiError, type MobileUser } from '../../api/v3MobileAccess';
import MobileAccessPage from '.';

const mocks = vi.hoisted(() => ({ workspace: null as unknown }));

vi.mock('../../contexts/AuthContext', () => ({
  useAuth: () => ({ isAdmin: true }),
}));

vi.mock('../../features/mobileAccess/useMobileAccessWorkspace', () => ({
  useMobileAccessWorkspace: () => mocks.workspace,
}));

function user(overrides: Partial<MobileUser> = {}): MobileUser {
  return {
    user_id: 7,
    username: 'resident_7',
    display_name: '住户七',
    mobile_only: true,
    account_status: 'active',
    profile_version: 1,
    created_at: '2026-09-21T01:00:00+00:00',
    updated_at: '2026-09-21T01:00:00+00:00',
    disabled_at: null,
    disabled_reason: null,
    device_scope_count: 0,
    area_scope_count: 0,
    active_session_count: 0,
    revoked_session_count: 0,
    unused_pairing: { available: false, expires_at: null },
    ...overrides,
  };
}

function workspace(overrides: Record<string, unknown> = {}) {
  return {
    filters: { search: '', accountStatus: 'all', mobileOnly: 'all' },
    setFilters: vi.fn(),
    clearFilters: vi.fn(),
    users: [],
    total: 0,
    page: 0,
    pageSize: 50,
    setPage: vi.fn(),
    listLoading: false,
    listError: null,
    refreshUsers: vi.fn().mockResolvedValue(undefined),
    selectedUser: null,
    selectUser: vi.fn(),
    scopes: null,
    deviceDraft: [],
    setDeviceDraft: vi.fn(),
    areaDraft: [],
    setAreaDraft: vi.fn(),
    scopeConflict: false,
    loadSelected: vi.fn().mockResolvedValue(undefined),
    detailLoading: false,
    detailError: null,
    devices: [],
    areas: [],
    sessions: [],
    sessionStatus: 'all',
    setSessionStatus: vi.fn(),
    sessionsLoading: false,
    sessionsError: null,
    sessionPage: 0,
    setSessionPage: vi.fn(),
    sessionTotal: 0,
    sessionHasMore: false,
    refreshSessions: vi.fn().mockResolvedValue(undefined),
    pairing: null,
    pairingSeconds: 0,
    clearPairing: vi.fn(),
    mutationPending: false,
    createUser: vi.fn().mockResolvedValue(user()),
    updateUser: vi.fn().mockResolvedValue(user({ profile_version: 2 })),
    saveScopes: vi.fn().mockResolvedValue(undefined),
    startPairing: vi.fn().mockResolvedValue(undefined),
    revokeSession: vi.fn().mockResolvedValue(undefined),
    ...overrides,
  };
}

function renderPage() {
  return render(
    <MemoryRouter
      initialEntries={['/mobile-access']}
      future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
    >
      <Routes>
        <Route path="/mobile-access" element={<MobileAccessPage />} />
        <Route path="/login" element={<div>重新登录</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  mocks.workspace = workspace();
  window.localStorage.clear();
  window.sessionStorage.clear();
});

describe('MobileAccessPage', () => {
  it('shows legal empty user, device, area, and session states without demo data', () => {
    mocks.workspace = workspace({
      selectedUser: user(),
      scopes: { user: { user_id: 7, username: 'resident_7', role: 'user' }, scope_version: 0, scopes: [] },
    });
    renderPage();
    expect(screen.getByText('没有符合条件的普通用户')).toBeInTheDocument();
    expect(screen.getByText('当前没有真实 v3 设备可选')).toBeInTheDocument();
    expect(screen.getByText('真实设备中尚无区域')).toBeInTheDocument();
    expect(screen.getByText('没有符合条件的移动会话')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '生成一次性配对码' })).toBeDisabled();
  });

  it('creates only with username/display name and starts with no scope claim', async () => {
    const createUser = vi.fn().mockResolvedValue(user());
    mocks.workspace = workspace({ createUser });
    renderPage();
    fireEvent.click(screen.getByRole('button', { name: '创建移动用户' }));
    fireEvent.change(screen.getByLabelText('用户名'), { target: { value: 'resident_7' } });
    fireEvent.change(screen.getByLabelText('显示名称'), { target: { value: '住户七' } });
    expect(screen.queryByLabelText(/密码|角色/)).not.toBeInTheDocument();
    const dialog = screen.getByRole('dialog', { name: '创建普通移动用户' });
    fireEvent.click(within(dialog).getByRole('button', { name: /^创\s*建$/ }));
    await waitFor(() => expect(createUser).toHaveBeenCalledWith('resident_7', '住户七'));
    expect(screen.getByText('移动用户已创建；默认没有任何设备可见范围。')).toBeInTheDocument();
  });

  it('shows the one-time pairing code once and clears it when the dialog closes', () => {
    const clearPairing = vi.fn();
    mocks.workspace = workspace({
      selectedUser: user({ device_scope_count: 1 }),
      scopes: {
        user: { user_id: 7, username: 'resident_7', role: 'user' },
        scope_version: 1,
        scopes: [{
          scope_kind: 'device', scope_value: 'camera-01', scope_version: 1,
          created_at: '2026-09-21T01:00:00+00:00',
        }],
      },
      deviceDraft: ['camera-01'],
      pairing: {
        pairing_id: 'pairing-7',
        pairing_code: 'ONE-TIME-ONLY',
        expires_at: '2026-09-21T01:05:00+00:00',
        user: { user_id: 7, username: 'resident_7' },
      },
      pairingSeconds: 120,
      clearPairing,
    });
    renderPage();
    expect(screen.getAllByText('ONE-TIME-ONLY')).toHaveLength(1);
    expect(window.localStorage).toHaveLength(0);
    expect(window.sessionStorage).toHaveLength(0);
    expect(window.location.href).not.toContain('ONE-TIME-ONLY');
    fireEvent.click(screen.getByRole('button', { name: '关闭并清除' }));
    expect(clearPairing).toHaveBeenCalledTimes(1);
  });

  it('requires a written warning confirmation before disabling a user', async () => {
    const updateUser = vi.fn().mockResolvedValue(
      user({ account_status: 'disabled', profile_version: 2 }),
    );
    mocks.workspace = workspace({ selectedUser: user(), updateUser });
    renderPage();
    fireEvent.click(screen.getByRole('button', { name: '禁用用户' }));
    expect(screen.getByText(/立即撤销所有移动 session/)).toBeInTheDocument();
    expect(screen.getByText(/恢复后旧 session 不会恢复/)).toBeInTheDocument();
    const confirm = screen.getByRole('button', { name: '确认禁用' });
    expect(confirm).toBeDisabled();
    fireEvent.change(screen.getByLabelText('禁用原因'), {
      target: { value: '住户已迁出' },
    });
    fireEvent.click(confirm);
    await waitFor(() => expect(updateUser).toHaveBeenCalledWith({
      account_status: 'disabled', disabled_reason: '住户已迁出',
    }));
  });

  it.each([
    ['forbidden', '当前账号没有 APP 访问管理权限。'],
    ['rate_limited', '请求过于频繁，请稍后手动重试。'],
    ['unavailable', '数据库或移动访问服务尚未准备。'],
  ] as const)('renders %s as a safe actionable error', (kind, message) => {
    mocks.workspace = workspace({
      listError: new MobileAccessApiError(kind, 'internal details'),
    });
    renderPage();
    expect(screen.getByRole('alert')).toHaveTextContent(message);
    expect(screen.queryByText('internal details')).not.toBeInTheDocument();
  });

  it('uses the existing login-expired flow for a 401', async () => {
    mocks.workspace = workspace({
      listError: new MobileAccessApiError('unauthorized', 'expired'),
    });
    renderPage();
    await waitFor(() => expect(screen.getByText('重新登录')).toBeInTheDocument());
  });
});
