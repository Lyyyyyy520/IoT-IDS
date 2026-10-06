import { useEffect, useMemo, useRef, useState } from 'react';
import { Modal } from 'antd';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../../contexts/AuthContext';
import {
  MobileAccessApiError,
  type MobileAccountStatus,
  type MobileSession,
} from '../../api/v3MobileAccess';
import { useMobileAccessWorkspace } from '../../features/mobileAccess/useMobileAccessWorkspace';
import './mobile-access.css';

function errorMessage(error: MobileAccessApiError | null): string | null {
  if (!error) return null;
  if (error.kind === 'unauthorized') return '登录已失效，请重新登录。';
  if (error.kind === 'forbidden') return '当前账号没有 APP 访问管理权限。';
  if (error.kind === 'conflict') return '数据已被其他管理员修改，请加载最新版本。';
  if (error.kind === 'rate_limited') return '请求过于频繁，请稍后手动重试。';
  if (error.kind === 'unavailable') return '数据库或移动访问服务尚未准备。';
  if (error.kind === 'network') return '无法连接后端；当前页面不会使用演示数据。';
  if (error.kind === 'invalid_response') return '后端响应格式不符合契约。';
  return error.message || '请求失败。';
}

function formatTime(value: string | null): string {
  if (!value) return '—';
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString();
}

function shortSession(value: string): string {
  return value.length <= 14 ? value : `${value.slice(0, 8)}…${value.slice(-4)}`;
}

function countdown(seconds: number): string {
  const minutes = Math.floor(seconds / 60);
  return `${String(minutes).padStart(2, '0')}:${String(seconds % 60).padStart(2, '0')}`;
}

export default function MobileAccessPage() {
  const { isAdmin } = useAuth();
  const navigate = useNavigate();
  const workspace = useMobileAccessWorkspace();
  const [notice, setNotice] = useState<string | null>(null);
  const [actionError, setActionError] = useState<MobileAccessApiError | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [createUsername, setCreateUsername] = useState('');
  const [createDisplayName, setCreateDisplayName] = useState('');
  const [editOpen, setEditOpen] = useState(false);
  const [editDisplayName, setEditDisplayName] = useState('');
  const [disableOpen, setDisableOpen] = useState(false);
  const [disableReason, setDisableReason] = useState('');
  const [revokeTarget, setRevokeTarget] = useState<MobileSession | null>(null);
  const [copyHelp, setCopyHelp] = useState<string | null>(null);
  const pairingCodeRef = useRef<HTMLElement | null>(null);

  const unauthorized = [
    workspace.listError,
    workspace.detailError,
    workspace.sessionsError,
    actionError,
  ].some((error) => error?.kind === 'unauthorized');
  useEffect(() => {
    if (unauthorized) navigate('/login', { replace: true });
  }, [navigate, unauthorized]);

  useEffect(() => {
    if (workspace.selectedUser) {
      setEditDisplayName(workspace.selectedUser.display_name);
    }
  }, [workspace.selectedUser]);

  const userNames = useMemo(
    () => new Map(workspace.users.map((user) => [user.user_id, user.display_name])),
    [workspace.users],
  );
  const canPair = Boolean(
    workspace.selectedUser?.account_status === 'active'
    && workspace.scopes
    && workspace.scopes.scopes.length > 0
    && !workspace.scopeConflict,
  );
  const scopeDirty = Boolean(workspace.scopes) && (
    workspace.deviceDraft.slice().sort().join('|') !== workspace.scopes!.scopes
      .filter((scope) => scope.scope_kind === 'device')
      .map((scope) => scope.scope_value).sort().join('|')
    || workspace.areaDraft.slice().sort().join('|') !== workspace.scopes!.scopes
      .filter((scope) => scope.scope_kind === 'area')
      .map((scope) => scope.scope_value).sort().join('|')
  );

  const run = async (operation: () => Promise<unknown>, success?: string) => {
    setActionError(null);
    try {
      await operation();
      if (success) setNotice(success);
    } catch (error) {
      setActionError(
        error instanceof MobileAccessApiError
          ? error
          : new MobileAccessApiError('http', '操作失败', { cause: error }),
      );
    }
  };

  const submitCreate = () => run(async () => {
    await workspace.createUser(createUsername, createDisplayName);
    setCreateOpen(false);
    setCreateUsername('');
    setCreateDisplayName('');
  }, '移动用户已创建；默认没有任何设备可见范围。');

  const submitEdit = () => run(async () => {
    await workspace.updateUser({ display_name: editDisplayName });
    setEditOpen(false);
  }, '显示名称已更新。');

  const submitDisable = () => run(async () => {
    await workspace.updateUser({
      account_status: 'disabled',
      disabled_reason: disableReason,
    });
    workspace.clearPairing();
    setDisableOpen(false);
    setDisableReason('');
  }, '用户已禁用；移动会话已撤销，未使用配对码已失效。');

  const restore = () => run(
    () => workspace.updateUser({ account_status: 'active' }),
    '用户已恢复，但旧会话不会恢复，请重新配对。',
  );

  const saveScopes = () => run(
    workspace.saveScopes,
    '可见范围已使用服务器新版本保存。',
  );

  const createPairing = () => run(workspace.startPairing);

  const copyPairing = async () => {
    if (!workspace.pairing) return;
    setCopyHelp(null);
    try {
      await navigator.clipboard.writeText(workspace.pairing.pairing_code);
      setCopyHelp('配对码已复制；请通过可信渠道交给用户。');
    } catch {
      pairingCodeRef.current?.focus();
      setCopyHelp('复制失败，请手工选择上方配对码。');
    }
  };

  if (!isAdmin) {
    return (
      <main className="mobile-access-denied">
        <h1>权限不足</h1>
        <p>APP 访问管理仅对管理员开放。</p>
      </main>
    );
  }

  return (
    <main className="mobile-access-page">
      <header className="mobile-access-header">
        <div>
          <p className="mobile-access-eyebrow">SCOPED APP ACCESS</p>
          <h1>APP访问管理</h1>
          <p>普通用户、可见范围、一次性配对和移动会话集中管理</p>
        </div>
        <button type="button" className="ma-button primary" onClick={() => setCreateOpen(true)}>
          创建移动用户
        </button>
      </header>

      {notice && (
        <div className="ma-banner success" role="status">
          <span>{notice}</span>
          <button type="button" onClick={() => setNotice(null)} aria-label="关闭提示">×</button>
        </div>
      )}
      {(actionError || workspace.listError) && (
        <div className="ma-banner error" role="alert">
          {errorMessage(actionError ?? workspace.listError)}
        </div>
      )}

      <div className="mobile-access-grid">
        <section className="ma-panel users-panel" aria-labelledby="mobile-users-title">
          <div className="ma-panel-title">
            <div><h2 id="mobile-users-title">移动用户</h2><span>{workspace.total} 个 role=user 账号</span></div>
            <button type="button" className="ma-button ghost" onClick={() => void workspace.refreshUsers()}>
              刷新
            </button>
          </div>
          <div className="ma-filters">
            <label>
              <span>搜索</span>
              <input
                value={workspace.filters.search}
                onChange={(event) => workspace.setFilters({ search: event.target.value })}
                placeholder="用户名或显示名称"
              />
            </label>
            <label>
              <span>状态</span>
              <select
                value={workspace.filters.accountStatus}
                onChange={(event) => workspace.setFilters({
                  accountStatus: event.target.value as MobileAccountStatus | 'all',
                })}
              >
                <option value="all">全部</option>
                <option value="active">启用</option>
                <option value="disabled">禁用</option>
              </select>
            </label>
            <label>
              <span>账号来源</span>
              <select
                value={workspace.filters.mobileOnly}
                onChange={(event) => workspace.setFilters({
                  mobileOnly: event.target.value as 'all' | 'true' | 'false',
                })}
              >
                <option value="all">全部</option>
                <option value="true">仅移动账号</option>
                <option value="false">已有普通账号</option>
              </select>
            </label>
          </div>
          <div className="ma-user-list" aria-busy={workspace.listLoading}>
            {workspace.listLoading && workspace.users.length === 0 && <p className="ma-empty">正在加载真实用户…</p>}
            {!workspace.listLoading && workspace.users.length === 0 && <p className="ma-empty">没有符合条件的普通用户</p>}
            {workspace.users.map((user) => (
              <button
                type="button"
                key={user.user_id}
                className={`ma-user-card ${workspace.selectedUser?.user_id === user.user_id ? 'selected' : ''}`}
                onClick={() => workspace.selectUser(user)}
              >
                <span className="ma-user-name">{user.display_name}</span>
                <span className="ma-user-id">@{user.username}</span>
                <span className="ma-user-meta">
                  <b className={`status ${user.account_status}`}>
                    {user.account_status === 'active' ? '启用' : '禁用'}
                  </b>
                  <span>{user.mobile_only ? '仅 APP' : '已有账号'}</span>
                  <span>范围 {user.device_scope_count + user.area_scope_count}</span>
                  <span>会话 {user.active_session_count}</span>
                </span>
              </button>
            ))}
          </div>
          <div className="ma-pagination">
            <button
              type="button"
              disabled={workspace.page === 0}
              onClick={() => workspace.setPage(Math.max(0, workspace.page - 1))}
            >上一页</button>
            <span>第 {workspace.page + 1} 页</span>
            <button
              type="button"
              disabled={(workspace.page + 1) * workspace.pageSize >= workspace.total}
              onClick={() => workspace.setPage(workspace.page + 1)}
            >下一页</button>
          </div>
        </section>

        <section className="ma-panel scope-panel" aria-labelledby="scope-title">
          {!workspace.selectedUser ? (
            <div className="ma-empty large">
              <h2 id="scope-title">用户与可见范围</h2>
              <p>从左侧选择用户后配置真实设备和区域。</p>
            </div>
          ) : (
            <>
              <div className="ma-panel-title">
                <div>
                  <h2 id="scope-title">{workspace.selectedUser.display_name}</h2>
                  <span>@{workspace.selectedUser.username} · profile v{workspace.selectedUser.profile_version}</span>
                </div>
                <span className={`ma-account-state ${workspace.selectedUser.account_status}`}>
                  {workspace.selectedUser.account_status === 'active' ? '启用' : '已禁用'}
                </span>
              </div>
              {workspace.detailError && <div className="ma-inline-error">{errorMessage(workspace.detailError)}</div>}
              <div className="ma-profile-actions">
                <button type="button" className="ma-button ghost" onClick={() => setEditOpen(true)}>编辑名称</button>
                {workspace.selectedUser.account_status === 'active' ? (
                  <button type="button" className="ma-button danger" onClick={() => setDisableOpen(true)}>禁用用户</button>
                ) : (
                  <button type="button" className="ma-button primary" onClick={() => void restore()}>恢复用户</button>
                )}
              </div>

              <div className="scope-editor">
                <fieldset>
                  <legend>设备范围</legend>
                  {workspace.devices.length === 0 && <p className="ma-empty">当前没有真实 v3 设备可选</p>}
                  {workspace.devices.map((device) => (
                    <label key={device.device_id} className="scope-option">
                      <input
                        type="checkbox"
                        checked={workspace.deviceDraft.includes(device.device_id)}
                        onChange={(event) => workspace.setDeviceDraft(
                          event.target.checked
                            ? [...workspace.deviceDraft, device.device_id]
                            : workspace.deviceDraft.filter((id) => id !== device.device_id),
                        )}
                      />
                      <span><b>{device.display_name}</b><small>{device.device_id} · {device.area_id ?? '未分区'}</small></span>
                    </label>
                  ))}
                </fieldset>
                <fieldset>
                  <legend>区域范围</legend>
                  {workspace.areas.length === 0 && <p className="ma-empty">真实设备中尚无区域</p>}
                  {workspace.areas.map((area) => (
                    <label key={area} className="scope-option">
                      <input
                        type="checkbox"
                        checked={workspace.areaDraft.includes(area)}
                        onChange={(event) => workspace.setAreaDraft(
                          event.target.checked
                            ? [...workspace.areaDraft, area]
                            : workspace.areaDraft.filter((value) => value !== area),
                        )}
                      />
                      <span><b>{area}</b><small>动态包含当前属于此区域的设备</small></span>
                    </label>
                  ))}
                </fieldset>
              </div>
              {workspace.scopeConflict && (
                <div className="ma-conflict" role="alert">
                  <p>范围已被其他管理员修改。本地选择仍保留，未自动覆盖或重试。</p>
                  <button
                    type="button"
                    onClick={() => void workspace.loadSelected(workspace.selectedUser!.user_id)}
                  >加载服务器最新版本</button>
                </div>
              )}
              <div className="scope-actions">
                <span>scope v{workspace.scopes?.scope_version ?? '—'} · 已选 {workspace.deviceDraft.length} 台设备 / {workspace.areaDraft.length} 个区域</span>
                <button
                  type="button"
                  className="ma-button primary"
                  disabled={!scopeDirty || workspace.mutationPending}
                  onClick={() => void saveScopes()}
                >保存完整范围</button>
              </div>

              <div className="pairing-section">
                <div>
                  <h3>一次性配对</h3>
                  <p>仅启用且已保存至少一个范围的用户可以生成；新代码会使旧代码失效。</p>
                </div>
                <button
                  type="button"
                  className="ma-button primary"
                  disabled={!canPair || workspace.mutationPending}
                  onClick={() => void createPairing()}
                >生成一次性配对码</button>
              </div>
            </>
          )}
        </section>

        <section className="ma-panel sessions-panel" aria-labelledby="sessions-title">
          <div className="ma-panel-title">
            <div><h2 id="sessions-title">移动会话</h2><span>{workspace.selectedUser ? '当前用户' : '全部用户'}</span></div>
            <button type="button" className="ma-button ghost" onClick={() => void workspace.refreshSessions()}>刷新</button>
          </div>
          <label className="session-filter">
            <span>状态</span>
            <select
              value={workspace.sessionStatus}
              onChange={(event) => workspace.setSessionStatus(
                event.target.value as typeof workspace.sessionStatus,
              )}
            >
              <option value="all">全部</option>
              <option value="active">活跃</option>
              <option value="revoked">已撤销</option>
              <option value="expired">已过期</option>
            </select>
          </label>
          {workspace.sessionsError && <div className="ma-inline-error">{errorMessage(workspace.sessionsError)}</div>}
          <div className="session-list" aria-busy={workspace.sessionsLoading}>
            {!workspace.sessionsLoading && workspace.sessions.length === 0 && (
              <p className="ma-empty">没有符合条件的移动会话</p>
            )}
            {workspace.sessions.map((item) => {
              const active = !item.revoked_at && new Date(item.refresh_expires_at).getTime() > Date.now();
              return (
                <article className="session-card" key={item.session_id}>
                  <div className="session-heading">
                    <div><b>{item.client_display_name}</b><span>{userNames.get(item.user_id) ?? item.username ?? `用户 ${item.user_id}`}</span></div>
                    <span className={active ? 'session-active' : 'session-revoked'}>{active ? '活跃' : '已结束'}</span>
                  </div>
                  <dl>
                    <div><dt>Session</dt><dd title={item.session_id}>{shortSession(item.session_id)}</dd></div>
                    <div><dt>实例</dt><dd>{item.client_instance_id}</dd></div>
                    <div><dt>签发</dt><dd>{formatTime(item.issued_at)}</dd></div>
                    <div><dt>最后活动</dt><dd>{formatTime(item.last_seen_at)}</dd></div>
                    <div><dt>Access 到期</dt><dd>{formatTime(item.access_expires_at)}</dd></div>
                    <div><dt>Refresh 到期</dt><dd>{formatTime(item.refresh_expires_at)}</dd></div>
                  </dl>
                  {item.revoked_reason && <p className="session-reason">原因：{item.revoked_reason}</p>}
                  {active && (
                    <button type="button" className="ma-button danger" onClick={() => setRevokeTarget(item)}>
                      撤销会话
                    </button>
                  )}
                </article>
              );
            })}
          </div>
          <div className="ma-pagination" aria-label="移动会话分页">
            <button
              type="button"
              disabled={workspace.sessionPage === 0 || workspace.sessionsLoading}
              onClick={() => workspace.setSessionPage(Math.max(0, workspace.sessionPage - 1))}
            >上一页</button>
            <span>第 {workspace.sessionPage + 1} 页 · 共 {workspace.sessionTotal} 条</span>
            <button
              type="button"
              disabled={!workspace.sessionHasMore || workspace.sessionsLoading}
              onClick={() => workspace.setSessionPage(workspace.sessionPage + 1)}
            >下一页</button>
          </div>
        </section>
      </div>

      <Modal
        title="创建普通移动用户"
        open={createOpen}
        onCancel={() => setCreateOpen(false)}
        onOk={() => void submitCreate()}
        okText="创建"
        cancelText="取消"
        confirmLoading={workspace.mutationPending}
        destroyOnHidden
      >
        <div className="ma-form">
          <label><span>用户名</span><input value={createUsername} onChange={(e) => setCreateUsername(e.target.value)} autoComplete="off" /></label>
          <label><span>显示名称</span><input value={createDisplayName} onChange={(e) => setCreateDisplayName(e.target.value)} /></label>
          <p>角色固定为 user，不创建或返回 Web 登录密码；创建后默认无设备范围。</p>
        </div>
      </Modal>

      <Modal
        title="编辑显示名称"
        open={editOpen}
        onCancel={() => setEditOpen(false)}
        onOk={() => void submitEdit()}
        confirmLoading={workspace.mutationPending}
      >
        <label className="ma-modal-label"><span>显示名称</span><input value={editDisplayName} onChange={(e) => setEditDisplayName(e.target.value)} /></label>
      </Modal>

      <Modal
        title="禁用移动用户"
        open={disableOpen}
        onCancel={() => setDisableOpen(false)}
        onOk={() => void submitDisable()}
        okButtonProps={{ danger: true, disabled: !disableReason.trim() }}
        okText="确认禁用"
        confirmLoading={workspace.mutationPending}
      >
        <div className="ma-warning-copy">
          <p>禁用会立即撤销所有移动 session，并使未使用配对码失效。</p>
          <p>恢复后旧 session 不会恢复，用户必须重新配对。</p>
        </div>
        <label className="ma-modal-label"><span>禁用原因</span><textarea value={disableReason} onChange={(e) => setDisableReason(e.target.value)} /></label>
      </Modal>

      <Modal
        title="一次性配对码"
        open={Boolean(workspace.pairing)}
        onCancel={() => { workspace.clearPairing(); setCopyHelp(null); }}
        footer={[
          <button type="button" className="ma-button ghost" key="copy" onClick={() => void copyPairing()}>复制</button>,
          <button type="button" className="ma-button primary" key="close" onClick={() => { workspace.clearPairing(); setCopyHelp(null); }}>关闭并清除</button>,
        ]}
        destroyOnHidden
      >
        {workspace.pairing && (
          <div className="pairing-dialog">
            <p>该代码只显示一次、只能成功使用一次。关闭、切换用户或过期后立即清除。</p>
            <code ref={pairingCodeRef} tabIndex={0}>{workspace.pairing.pairing_code}</code>
            <div className="pairing-countdown" aria-live="polite">
              剩余时间 <b>{countdown(workspace.pairingSeconds)}</b>
              <span>到期：{formatTime(workspace.pairing.expires_at)}</span>
            </div>
            {copyHelp && <p role="status">{copyHelp}</p>}
          </div>
        )}
      </Modal>

      <Modal
        title="撤销移动会话"
        open={Boolean(revokeTarget)}
        onCancel={() => setRevokeTarget(null)}
        onOk={() => void run(async () => {
          if (!revokeTarget) return;
          await workspace.revokeSession(revokeTarget.session_id);
          setRevokeTarget(null);
        }, '移动会话已撤销。')}
        okButtonProps={{ danger: true }}
        okText="确认撤销"
        confirmLoading={workspace.mutationPending}
      >
        <p>撤销后该客户端的 access 和 refresh token 将立即失效。重复撤销是安全的，但不会重新激活会话。</p>
      </Modal>
    </main>
  );
}
