import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { IncidentApiError } from '../../api/v3Incidents';
import { incidentDetail, incidentListItem } from '../../test/incidentFixtures';
import { listItem } from '../../test/deviceFixtures';
import IncidentsPage from './index';

const state = vi.hoisted(() => ({ role: 'admin', workspace: null as any, options: null as any }));

vi.mock('../../contexts/AuthContext', () => ({
  useAuth: () => ({ isAdmin: state.role === 'admin', user: { username: `${state.role}-test`, role: state.role } }),
}));
vi.mock('../../features/incidents/useIncidentWorkspace', () => ({
  useIncidentWorkspace: (options: unknown) => { state.options = options; return state.workspace; },
}));

function workspace(overrides: Record<string, unknown> = {}) {
  return {
    incidentFilters: { search: '', status: 'all', severity: 'all', source: 'all', deviceId: '', from: '', to: '' },
    setIncidentFilters: vi.fn(), clearIncidentFilters: vi.fn(),
    incidents: [incidentListItem], incidentTotal: 1, incidentPage: 0, incidentLoading: false,
    incidentError: null, incidentUpdatedAt: incidentListItem.updated_at,
    setIncidentPage: vi.fn(), refreshIncidents: vi.fn(), selectedIncidentId: incidentDetail.incident_id,
    incidentDetail, incidentDetailLoading: false, incidentDetailError: null,
    selectIncident: vi.fn().mockResolvedValue(undefined), createIncident: vi.fn().mockResolvedValue(incidentDetail),
    transitionIncident: vi.fn().mockResolvedValue({ ...incidentDetail, status: 'acknowledged', incident_version: 2 }),
    mutationPending: false, realtime: 'connected', stale: false,
    helpFilters: { status: 'all', category: 'all', userId: '', deviceId: '', incidentId: '', from: '', to: '' },
    setHelpFilters: vi.fn(), clearHelpFilters: vi.fn(), helpItems: [], helpTotal: 0, helpPage: 0,
    helpLoading: false, helpError: null, setHelpPage: vi.fn(), refreshHelp: vi.fn(), selectedHelpId: null,
    helpDetail: null, helpDetailLoading: false, helpDetailError: null, selectHelp: vi.fn(), updateHelp: vi.fn(),
    contact: { available: false, reason: 'support_contact_not_configured' }, contactLoading: false,
    contactError: null, loadContact: vi.fn(), updateContact: vi.fn(), pageSize: 25,
    ...overrides,
  };
}

function renderPage(entry = '/incidents') {
  return render(<MemoryRouter initialEntries={[entry]} future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
    <Routes>
      <Route path="/incidents" element={<IncidentsPage />} />
      <Route path="/devices" element={<div data-testid="devices-route" />} />
    </Routes>
  </MemoryRouter>);
}

beforeEach(() => {
  state.role = 'admin'; state.options = null; state.workspace = workspace();
  vi.restoreAllMocks();
});

describe('IncidentsPage', () => {
  it('shows server-backed incident details and keeps public and admin evidence separate', () => {
    renderPage();
    expect(screen.getByRole('heading', { name: '门厅摄像机通信异常' })).toBeInTheDocument();
    expect(screen.getByText('疑似来源')).toBeInTheDocument();
    expect(screen.getByText('仅管理端：')).toBeInTheDocument();
    expect(screen.getByText('APP 用户可见预览')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '确认处理' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '标记事件已解决' })).not.toBeInTheDocument();
  });

  it('hides manual creation from operators while retaining read and disposition views', () => {
    state.role = 'operator';
    renderPage();
    expect(screen.getByText('值守人员 · 事件处置')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '创建人工事件' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '确认处理' })).toBeInTheDocument();
  });

  it('loads an incident selected by Monitor deep link', () => {
    renderPage(`/incidents?incident_id=${incidentDetail.incident_id}`);
    expect(state.options.initialIncidentId).toBe(incidentDetail.incident_id);
  });

  it('keeps a truthful no-recorded-events state', () => {
    state.workspace = workspace({ incidents: [], incidentTotal: 0, incidentDetail: null, selectedIncidentId: null });
    renderPage();
    expect(screen.getByText('当前没有已记录事件')).toBeInTheDocument();
    expect(screen.getByText('这不代表系统安全或没有攻击。')).toBeInTheDocument();
  });

  it('does not offer reopen actions for terminal incidents', () => {
    state.workspace = workspace({
      incidentDetail: { ...incidentDetail, status: 'resolved', resolved_at: '2026-09-23T04:00:00Z' },
    });
    renderPage();
    expect(screen.getByText('该事件已处于终态，本版本不提供重新打开操作。')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /确认处理|进入恢复中|标记已解决|作为误报结案/ })).not.toBeInTheDocument();
  });

  it('creates only a manual event from a real selected device and provides no IP field', async () => {
    const { v3DevicesApi } = await import('../../api/v3Devices');
    vi.spyOn(v3DevicesApi, 'listDevices').mockResolvedValue({ items: [listItem()], total: 1, limit: 100, offset: 0 });
    renderPage();
    fireEvent.click(screen.getByRole('button', { name: '创建人工事件' }));
    const dialog = await screen.findByRole('dialog');
    await within(dialog).findByText('东门摄像头');
    expect(within(dialog).queryByLabelText(/^IP$/i)).not.toBeInTheDocument();
    fireEvent.change(within(dialog).getByLabelText('事件类型'), { target: { value: 'device_anomaly' } });
    fireEvent.change(within(dialog).getByLabelText('管理员标题'), { target: { value: '真实人工事件' } });
    fireEvent.change(within(dialog).getByLabelText('管理员摘要 · 仅管理端'), { target: { value: '需要人工复核。' } });
    fireEvent.change(within(dialog).getByLabelText('用户标题 · APP 可见'), { target: { value: '设备需要关注' } });
    fireEvent.change(within(dialog).getByLabelText('用户摘要 · APP 可见'), { target: { value: '管理员正在核查设备服务。' } });
    fireEvent.click(within(dialog).getByLabelText(/东门摄像头/));
    fireEvent.click(within(dialog).getByRole('button', { name: '创建事件' }));
    await waitFor(() => expect(state.workspace.createIncident).toHaveBeenCalledWith(expect.objectContaining({
      source: 'manual', publish_to_mobile: true,
      devices: [{ device_id: 'camera-01', incident_role: 'affected', user_visible: true }],
    })));
  });

  it('shows a live privacy warning while public incident copy contains an IP', async () => {
    const { v3DevicesApi } = await import('../../api/v3Devices');
    vi.spyOn(v3DevicesApi, 'listDevices').mockResolvedValue({ items: [listItem()], total: 1, limit: 100, offset: 0 });
    renderPage();
    fireEvent.click(screen.getByRole('button', { name: '创建人工事件' }));
    const dialog = await screen.findByRole('dialog');
    fireEvent.change(within(dialog).getByLabelText('用户摘要 · APP 可见'), { target: { value: '设备 10.0.0.5 需要检查' } });
    expect(within(dialog).getByRole('status')).toHaveTextContent('APP 用户可见文案不能包含 IP');
    expect(state.workspace.createIncident).not.toHaveBeenCalled();
  });

  it('warns about Chinese port details in public progress before submission', async () => {
    renderPage();
    fireEvent.click(screen.getByRole('button', { name: '确认处理' }));
    const dialog = await screen.findByRole('dialog');
    fireEvent.change(within(dialog).getByLabelText(/公开处理进度/), { target: { value: '端口：1883 正在检查' } });
    expect(within(dialog).getByRole('status')).toHaveTextContent('APP 用户可见文案不能包含 IP、MAC、端口');
  });

  it('retains help-response drafts on version conflict without retrying the write', async () => {
    const detail = (await import('../../test/incidentFixtures')).helpDetail;
    const updateHelp = vi.fn().mockRejectedValue(new IncidentApiError('conflict', 'opaque backend detail', { code: 'help_request_version_conflict' }));
    state.workspace = workspace({
      helpItems: [detail], helpTotal: 1, selectedHelpId: detail.help_request_id,
      helpDetail: detail, updateHelp,
    });
    renderPage();
    fireEvent.click(screen.getByRole('button', { name: '用户求助' }));
    const input = await screen.findByLabelText('公开回复 · APP 用户可见');
    fireEvent.change(input, { target: { value: '已为您安排进一步协助。' } });
    fireEvent.click(screen.getByRole('button', { name: '更新处理进度' }));
    expect(await screen.findByText(/其他管理员更新/)).toBeInTheDocument();
    expect(screen.getByLabelText('公开回复 · APP 用户可见')).toHaveValue('已为您安排进一步协助。');
    expect(screen.getByRole('button', { name: '加载最新版本（保留草稿）' })).toBeInTheDocument();
    expect(updateHelp).toHaveBeenCalledTimes(1);
  });

  it('requires an explicit false-positive confirmation and states audit history is retained', async () => {
    renderPage();
    fireEvent.click(screen.getByRole('button', { name: '作为误报结案' }));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText(/审计记录不会删除/)).toBeInTheDocument();
    expect(within(dialog).getByRole('button', { name: '作为误报结案' })).toBeDisabled();
    fireEvent.change(within(dialog).getByLabelText(/公开处理进度/), { target: { value: '管理员已完成复核。' } });
    fireEvent.change(within(dialog).getByLabelText('误报原因 · 仅管理端'), { target: { value: '复核后无需处置。' } });
    fireEvent.click(within(dialog).getByRole('checkbox'));
    fireEvent.click(within(dialog).getByRole('button', { name: '作为误报结案' }));
    await waitFor(() => expect(state.workspace.transitionIncident).toHaveBeenCalledWith('false-positive', expect.objectContaining({ expected_incident_version: 1 })));
  });

  it('renders a help request body as plain text and separates public response from internal note', async () => {
    const detail = (await import('../../test/incidentFixtures')).helpDetail;
    state.workspace = workspace({
      helpItems: [detail], helpTotal: 1, selectedHelpId: detail.help_request_id, helpDetail: detail,
    });
    renderPage();
    fireEvent.click(screen.getByRole('button', { name: '用户求助' }));
    expect((await screen.findAllByText('<script>我家的设备无法使用</script>'))).toHaveLength(2);
    expect(document.querySelector('script')).not.toBeInTheDocument();
    expect(screen.getByText('APP 用户可见', { selector: 'span' })).toBeInTheDocument();
    expect(screen.getByText('仅 Web 管理端', { selector: 'span' })).toBeInTheDocument();
  });

  it('shows operator contact information read-only and lets only admin begin unconfigured contact setup', async () => {
    state.role = 'operator';
    const operatorView = renderPage();
    fireEvent.click(screen.getByRole('button', { name: 'APP 联系方式' }));
    expect(await screen.findByText('尚未配置公开联系方式')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '开始配置' })).not.toBeInTheDocument();
    operatorView.unmount();
    state.role = 'admin'; state.workspace = workspace();
    renderPage();
    fireEvent.click(screen.getByRole('button', { name: 'APP 联系方式' }));
    expect(await screen.findByText('尚未配置公开联系方式')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '开始配置' }));
    expect(screen.getByLabelText('显示名称')).toBeInTheDocument();
    expect(screen.getByText(/禁用后 APP 将看不到/)).toBeInTheDocument();
  });

  it('updates public contact settings with the server config version', async () => {
    const contact = {
      available: true as const, display_name: '社区值班', phone: null, email: 'help@example.test',
      working_hours: '工作日 9:00-18:00', public_note: null, enabled: true,
      config_version: 4, updated_by: 1, updated_at: '2026-09-23T03:00:00Z',
    };
    const updateContact = vi.fn().mockResolvedValue({ ...contact, config_version: 5 });
    state.workspace = workspace({ contact, updateContact });
    renderPage();
    fireEvent.click(screen.getByRole('button', { name: 'APP 联系方式' }));
    fireEvent.change(await screen.findByLabelText('显示名称'), { target: { value: '社区服务台' } });
    fireEvent.click(screen.getByRole('button', { name: '保存公开联系方式' }));
    await waitFor(() => expect(updateContact).toHaveBeenCalledWith(expect.objectContaining({
      display_name: '社区服务台', expected_config_version: 4,
    })));
  });
});
