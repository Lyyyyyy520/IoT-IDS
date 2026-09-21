import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { DeviceApiError } from '../../api/v3Devices';
import { deviceDetail, listItem } from '../../test/deviceFixtures';
import DevicesPage from './index';

const state = vi.hoisted(() => ({
  role: 'admin',
  workspace: null as any,
  options: null as any,
}));

vi.mock('../../contexts/AuthContext', () => ({
  useAuth: () => ({
    isAdmin: state.role === 'admin',
    user: { username: state.role === 'admin' ? 'admin-a' : 'operator-a', role: state.role },
  }),
}));

vi.mock('../../features/devices/useDeviceWorkspace', () => ({
  useDeviceWorkspace: (options: unknown) => {
    state.options = options;
    return state.workspace;
  },
}));
vi.mock('../../features/traffic/DeviceTrafficPanel', () => ({
  default: () => <div>设备流量分析测试面板</div>,
}));

function workspace(overrides: Record<string, unknown> = {}) {
  return {
    filters: {
      search: '',
      connectionStatus: 'all',
      operationMode: 'all',
      areaId: '',
      lifecycle: 'all',
    },
    patchFilters: vi.fn(),
    clearFilters: vi.fn(),
    items: [listItem()],
    total: 1,
    listLoading: false,
    loadingMore: false,
    listError: null,
    lastUpdatedAt: '2026-09-20T02:00:00Z',
    hasMore: false,
    loadMore: vi.fn(),
    refreshList: vi.fn(),
    selectedId: deviceDetail.device_id,
    detail: deviceDetail,
    detailLoading: false,
    detailError: null,
    selectionNotice: null,
    selectDevice: vi.fn(),
    refreshDetail: vi.fn().mockResolvedValue(undefined),
    mutationPending: false,
    createDevice: vi.fn().mockResolvedValue(deviceDetail),
    updateDevice: vi.fn().mockResolvedValue(deviceDetail),
    setOperationMode: vi.fn().mockResolvedValue(deviceDetail),
    retireDevice: vi.fn().mockResolvedValue({
      ...deviceDetail,
      operation_mode: 'disabled',
      lifecycle_status: 'retired',
      retired_at: '2026-09-20T03:00:00Z',
      credential_revocation_required: true,
    }),
    restoreDevice: vi.fn().mockResolvedValue({
      ...deviceDetail,
      connection_status: 'unknown',
      credential_reverification_required: true,
    }),
    deleteDevice: vi.fn().mockResolvedValue({
      device_id: deviceDetail.device_id,
      deleted: true,
      retained_management_audit: true,
    }),
    ...overrides,
  };
}

function renderPage(initialEntry = '/devices') {
  return render(
    <MemoryRouter
      initialEntries={[initialEntry]}
      future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
    >
      <DevicesPage />
    </MemoryRouter>,
  );
}

function openManage() {
  fireEvent.click(screen.getByRole('tab', { name: '管理' }));
}

beforeEach(() => {
  state.role = 'admin';
  state.options = null;
  state.workspace = workspace();
});

describe('DevicesPage permissions and server-backed interactions', () => {
  it('shows write and danger operations to admin, but keeps operator strictly read-only', async () => {
    const rendered = renderPage();
    await screen.findByText('设备流量分析测试面板');
    openManage();
    expect(screen.getByRole('button', { name: '新增设备档案' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '编辑档案' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '退役设备' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: /危险操作/ })).toBeInTheDocument();

    rendered.unmount();
    state.role = 'operator';
    renderPage();
    await screen.findByText('设备流量分析测试面板');
    expect(screen.getByText('值守人员 · 只读')).toBeInTheDocument();
    expect(screen.queryByRole('tab', { name: '管理' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '新增设备档案' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '编辑档案' })).not.toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: /危险操作/ })).not.toBeInTheDocument();
  });

  it('passes the query device_id into the workspace for automatic selection', () => {
    renderPage('/devices?device_id=camera-01');
    expect(state.options.initialDeviceId).toBe('camera-01');
    expect(screen.getByRole('tab', { name: '流量' })).toHaveAttribute('aria-selected', 'true');
  });

  it('creates without an IP field and reports the real unknown server state', async () => {
    const created = {
      ...deviceDetail,
      device_id: 'sensor-02',
      display_name: '仓库温湿度计',
      connection_status: 'unknown' as const,
      ip_address: null,
      state_version: 0,
    };
    state.workspace.createDevice.mockResolvedValue(created);
    renderPage();

    fireEvent.click(screen.getByRole('button', { name: '新增设备档案' }));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).queryByLabelText(/^IP$/i)).not.toBeInTheDocument();
    fireEvent.change(within(dialog).getByRole('textbox', { name: /device_id/ }), { target: { value: 'sensor-02' } });
    fireEvent.change(within(dialog).getByLabelText('MAC'), { target: { value: 'AA:BB:CC:DD:EE:02' } });
    fireEvent.change(within(dialog).getByLabelText('显示名称'), { target: { value: '仓库温湿度计' } });
    fireEvent.change(within(dialog).getByLabelText('设备类型'), { target: { value: 'sensor' } });
    fireEvent.click(within(dialog).getByRole('button', { name: '创建设备档案' }));

    await waitFor(() => expect(state.workspace.createDevice).toHaveBeenCalledWith({
      device_id: 'sensor-02',
      mac: 'AA:BB:CC:DD:EE:02',
      display_name: '仓库温湿度计',
      device_type: 'sensor',
      area_id: null,
      importance: 'normal',
      profile_source: 'physical',
    }));
    expect(await screen.findByRole('status')).toHaveTextContent('当前真实连接状态为未知');
  });

  it('submits the current profile_version when editing', async () => {
    renderPage();
    openManage();
    fireEvent.click(screen.getByRole('button', { name: '编辑档案' }));
    const dialog = await screen.findByRole('dialog');
    fireEvent.change(within(dialog).getByLabelText('显示名称'), { target: { value: '东门主摄像头' } });
    fireEvent.click(within(dialog).getByRole('button', { name: '保存档案' }));

    await waitFor(() => expect(state.workspace.updateDevice).toHaveBeenCalledWith(
      deviceDetail.device_id,
      expect.objectContaining({
        display_name: '东门主摄像头',
        expected_profile_version: 3,
      }),
    ));
  });

  it('keeps local edits after a profile version conflict and never retries automatically', async () => {
    state.workspace.updateDevice.mockRejectedValue(new DeviceApiError(
      'conflict',
      '档案版本冲突',
      { code: 'profile_version_conflict' },
    ));
    renderPage();
    openManage();
    fireEvent.click(screen.getByRole('button', { name: '编辑档案' }));
    const dialog = await screen.findByRole('dialog');
    const nameInput = within(dialog).getByLabelText('显示名称');
    fireEvent.change(nameInput, { target: { value: '未提交的新名称' } });
    fireEvent.click(within(dialog).getByRole('button', { name: '保存档案' }));

    expect(await within(dialog).findByText(/其他操作修改/)).toBeInTheDocument();
    expect(nameInput).toHaveValue('未提交的新名称');
    expect(within(dialog).getByRole('button', { name: '重新加载最新数据' })).toBeInTheDocument();
    expect(state.workspace.updateDevice).toHaveBeenCalledTimes(1);
  });

  it('keeps operation mode separate from connection state and explains the mode effect', async () => {
    renderPage();
    fireEvent.click(screen.getByRole('tab', { name: '概览' }));
    expect(screen.getByText('连接：在线')).toBeInTheDocument();
    expect(screen.getByText('模式：运行中')).toBeInTheDocument();
    openManage();
    fireEvent.click(screen.getByRole('button', { name: '修改运行模式' }));
    const dialog = await screen.findByRole('dialog');
    fireEvent.change(within(dialog).getByLabelText('运行模式'), { target: { value: 'maintenance' } });
    expect(within(dialog).getByText(/不会改变真实连接状态/)).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole('button', { name: '确认修改模式' }));
    await waitFor(() => expect(state.workspace.setOperationMode).toHaveBeenCalledWith(
      deviceDetail.device_id,
      'maintenance',
      3,
    ));
  });

  it('shows the manual credential warning after retirement', async () => {
    renderPage();
    openManage();
    fireEvent.click(screen.getByRole('button', { name: '退役设备' }));
    const dialog = await screen.findByRole('dialog');
    fireEvent.change(within(dialog).getByLabelText('退役原因'), { target: { value: '设备下线更换' } });
    fireEvent.click(within(dialog).getByRole('checkbox'));
    fireEvent.click(within(dialog).getByRole('button', { name: '确认退役' }));
    await waitFor(() => expect(state.workspace.retireDevice).toHaveBeenCalledWith(
      deviceDetail.device_id,
      '设备下线更换',
      3,
    ));
    expect(await screen.findByRole('status')).toHaveTextContent('MQTT 凭据仍需人工吊销');
  });

  it('does not imply online after restoring a retired device', async () => {
    state.workspace.detail = {
      ...deviceDetail,
      lifecycle_status: 'retired',
      operation_mode: 'disabled',
      connection_status: 'offline',
      retired_at: '2026-09-20T03:00:00Z',
    };
    renderPage();
    openManage();
    fireEvent.click(screen.getByRole('button', { name: '恢复设备' }));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText(/不会把设备自动设置为 online/)).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole('button', { name: '确认恢复' }));
    expect(await screen.findByRole('status')).toHaveTextContent('当前连接仍为 unknown');
    expect(screen.getByRole('status')).toHaveTextContent('MQTT 凭据需要重新核验');
  });

  it('disables deletion when history exists and requires an exact name when it does not', async () => {
    const rendered = renderPage();
    openManage();
    expect(screen.getByText('状态观测', { selector: 'li' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '不可恢复地删除设备' })).toBeDisabled();

    rendered.unmount();
    state.workspace = workspace({
      detail: { ...deviceDetail, can_delete: true, delete_blocking_reasons: [], references: {
        ...deviceDetail.references,
        state_observations: 0,
        mqtt_boot_sessions: 0,
        mqtt_cursor: 0,
        non_management_events: 0,
      } },
    });
    renderPage();
    openManage();
    const confirmation = screen.getByLabelText(/手工输入完整设备名称/);
    fireEvent.change(confirmation, { target: { value: '东门摄像' } });
    expect(screen.getByRole('button', { name: '不可恢复地删除设备' })).toBeDisabled();
    fireEvent.change(confirmation, { target: { value: deviceDetail.display_name } });
    const deleteButton = screen.getByRole('button', { name: '不可恢复地删除设备' });
    expect(deleteButton).toBeEnabled();
    fireEvent.click(deleteButton);
    await waitFor(() => expect(state.workspace.deleteDevice).toHaveBeenCalledWith(
      deviceDetail.device_id,
      deviceDetail.display_name,
    ));
    expect(await screen.findByRole('status')).toHaveTextContent('已彻底删除');
  });

  it('renders legal empty and categorized API error states without fallback devices', () => {
    state.workspace = workspace({ items: [], total: 0, detail: null, selectedId: null });
    const rendered = renderPage();
    expect(screen.getByText('没有匹配的真实设备')).toBeInTheDocument();
    expect(screen.queryByText('东门摄像头')).not.toBeInTheDocument();
    rendered.unmount();

    state.workspace = workspace({
      items: [],
      total: 0,
      detail: null,
      selectedId: null,
      listError: new DeviceApiError('unavailable', '数据库未准备', { status: 503, requestId: 'req-503' }),
    });
    renderPage();
    expect(screen.getByRole('alert')).toHaveTextContent('设备数据库或管理服务尚未准备');
    expect(screen.getByRole('alert')).toHaveTextContent('req-503');
  });
});
