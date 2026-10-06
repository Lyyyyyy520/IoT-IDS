import { useEffect, useState, type FormEvent } from 'react';
import type { ClaimCandidateInput, DiscoveryCandidate } from '../../api/v3Discovery';
import type { DeviceImportance, DeviceProfileSource } from '../../api/v3Devices';
import { discoveryErrorMessage } from './useDiscoveryWorkspace';
import './discovery.css';

const STATUS_LABEL: Record<DiscoveryCandidate['status'], string> = {
  pending: '待人工核验', ignored: '已忽略', claimed: '已认领', conflict: '身份冲突',
};
const SOURCE_LABEL: Record<string, string> = {
  mqtt_unknown: '未知设备心跳', dhcp: 'DHCP 观察', arp: 'ARP 观察', probe: '探针观察', other: '其他来源',
};
const IMPORTANCE: DeviceImportance[] = ['low', 'normal', 'high', 'critical'];
const PROFILE_SOURCES: Array<Exclude<DeviceProfileSource, 'unclassified'>> = ['physical', 'virtual', 'gateway'];

export interface DiscoveryWorkspaceState {
  filters: { search: string; status: 'all' | DiscoveryCandidate['status']; source: string; conflict: 'all' | 'conflict' | 'clear' };
  patchFilters: (value: Record<string, string>) => void;
  clearFilters: () => void;
  items: DiscoveryCandidate[];
  total: number;
  hasMore: boolean;
  loading: boolean;
  error: { kind: string; requestId: string | null } | null;
  lastUpdatedAt: string | null;
  selectedId: string | null;
  detail: DiscoveryCandidate | null;
  detailLoading: boolean;
  detailError: { kind: string; requestId: string | null } | null;
  actionPending: boolean;
  refreshList: () => Promise<void>;
  loadMore: () => Promise<void>;
  selectCandidate: (id: string | null) => Promise<void>;
  claimCandidate: (input: ClaimCandidateInput) => Promise<{ device: { device_id: string }; credential_provisioning_required: true; provisioning_message: string }>;
  ignoreCandidate: (reason: string) => Promise<unknown>;
  restoreCandidate: () => Promise<unknown>;
}

function time(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? '时间不可用' : date.toLocaleString();
}
function errorMessage(error: { kind: string; requestId: string | null } | null): string {
  if (!error) return '';
  const common = {
    bad_request: '请求内容不符合要求。', unauthorized: '登录已失效。', forbidden: '当前账号没有权限。',
    not_found: '候选已不存在，请刷新列表。', conflict: '候选版本或身份已冲突，请刷新后重新核验。',
    too_large: '请求内容过大。', unavailable: '设备发现服务尚未准备好。', network: '无法连接设备发现服务。',
    invalid_response: '服务返回的数据格式异常。', http: '请求失败。', aborted: '',
  } as Record<string, string>;
  const base = common[error.kind] ?? '设备发现服务暂时不可用。';
  return error.requestId ? `${base}（请求编号：${error.requestId}）` : base;
}

export default function DiscoveryPanel({
  workspace, isAdmin, onClaimed,
}: { workspace: DiscoveryWorkspaceState; isAdmin: boolean; onClaimed: (deviceId: string, message: string) => void }) {
  const { filters, detail, selectedId } = workspace;
  const [claimDraft, setClaimDraft] = useState({ device_id: '', display_name: '', device_type: '', area_id: '', importance: 'normal' as DeviceImportance, profile_source: 'physical' as Exclude<DeviceProfileSource, 'unclassified'> });
  const [ignoreReason, setIgnoreReason] = useState('');
  const [actionError, setActionError] = useState('');
  const [resolveConflict, setResolveConflict] = useState(false);

  useEffect(() => {
    setClaimDraft({ device_id: '', display_name: '', device_type: '', area_id: '', importance: 'normal', profile_source: 'physical' });
    setIgnoreReason('');
    setActionError('');
    setResolveConflict(false);
  }, [selectedId]);

  const claim = async (event: FormEvent) => {
    event.preventDefault();
    if (!detail) return;
    setActionError('');
    try {
      const result = await workspace.claimCandidate({
        expected_candidate_version: detail.candidate_version,
        ...claimDraft,
        area_id: claimDraft.area_id.trim() || null,
        ...(detail.conflict ? { resolve_identity_conflict: resolveConflict } : {}),
      });
      onClaimed(result.device.device_id, result.provisioning_message);
    } catch (error) {
      const err = error as { kind?: string; code?: string; requestId?: string | null };
      setActionError(err.kind ? `${errorMessage({ kind: err.kind, requestId: err.requestId ?? null })}${err.code ? ` 错误码：${err.code}` : ''}` : '认领失败，请刷新候选后重试。草稿已保留。');
    }
  };

  const ignore = async () => {
    setActionError('');
    try { await workspace.ignoreCandidate(ignoreReason.trim()); setIgnoreReason(''); }
    catch (error) {
      const err = error as { kind?: string; code?: string; requestId?: string | null };
      setActionError(`${errorMessage({ kind: err.kind ?? '', requestId: err.requestId ?? null })}${err.code ? ` 错误码：${err.code}` : ''} 草稿已保留。`);
    }
  };
  const restore = async () => {
    setActionError('');
    try { await workspace.restoreCandidate(); }
    catch (error) {
      const err = error as { kind?: string; code?: string; requestId?: string | null };
      setActionError(`${errorMessage({ kind: err.kind ?? '', requestId: err.requestId ?? null })}${err.code ? ` 错误码：${err.code}` : ''}`);
    }
  };

  const isBoundMacConflict = detail?.conflict_reason === 'mac_already_bound';
  return (
    <section className="discovery-workspace" aria-label="待确认设备工作区">
      <div className="discovery-list-pane">
        <div className="discovery-heading">
          <div><p className="devices-eyebrow">QUARANTINED IDENTITY EVIDENCE</p><h2>待确认设备</h2></div>
          <span className="devices-total" aria-label={`${workspace.total} 个候选`}>{workspace.total}</span>
        </div>
        <div className="discovery-filters">
          <label>搜索 device ID / MAC<input value={filters.search} onChange={(event) => workspace.patchFilters({ search: event.target.value })} /></label>
          <label>状态<select value={filters.status} onChange={(event) => workspace.patchFilters({ status: event.target.value })}>
            <option value="all">全部状态</option><option value="pending">待核验</option><option value="conflict">身份冲突</option><option value="ignored">已忽略</option><option value="claimed">已认领</option>
          </select></label>
          <label>来源<select value={filters.source} onChange={(event) => workspace.patchFilters({ source: event.target.value })}>
            <option value="all">全部来源</option>{Object.entries(SOURCE_LABEL).map(([source, label]) => <option value={source} key={source}>{label}</option>)}
          </select></label>
          <label>冲突筛选<select value={filters.conflict} onChange={(event) => workspace.patchFilters({ conflict: event.target.value })}>
            <option value="all">全部</option><option value="conflict">有冲突</option><option value="clear">无冲突</option>
          </select></label>
          <div className="discovery-filter-actions">
            <button type="button" className="devices-button ghost" onClick={workspace.clearFilters}>清空筛选</button>
            <button type="button" className="devices-button" onClick={() => void workspace.refreshList()} disabled={workspace.loading}>刷新</button>
          </div>
        </div>
        <div className="discovery-list-meta"><span>{workspace.loading ? '正在同步…' : `共 ${workspace.total} 个候选`}</span><span>{workspace.lastUpdatedAt ? `更新于 ${new Date(workspace.lastUpdatedAt).toLocaleTimeString()}` : '尚未同步'}</span></div>
        {workspace.error && <div role="alert" className="devices-inline-error">{errorMessage(workspace.error)}</div>}
        <div className="discovery-candidate-list" aria-live="polite">
          {!workspace.loading && !workspace.error && workspace.items.length === 0 && <div className="devices-empty"><strong>当前没有待确认候选</strong><span>这里只显示隔离区内的真实发现证据。</span></div>}
          {workspace.items.map((candidate) => (
            <button type="button" key={candidate.candidate_id} className={`discovery-candidate ${selectedId === candidate.candidate_id ? 'selected' : ''}`} onClick={() => void workspace.selectCandidate(candidate.candidate_id)}>
              <span className={`candidate-mark ${candidate.conflict ? 'conflict' : candidate.status}`} aria-hidden="true" />
              <span className="candidate-main"><strong>{candidate.proposed_device_id ?? '未提供设备 ID'}</strong><small>{candidate.device_type_hint ?? '类型未知'} · {(candidate.sources ?? []).map((source) => SOURCE_LABEL[source] ?? source).join('、') || '来源待加载'}</small><small>首次 {time(candidate.first_seen_at)} · 最近 {time(candidate.last_seen_at)}</small></span>
              <span className="candidate-side"><b>{STATUS_LABEL[candidate.status]}</b><small>{candidate.observation_count} 次观察</small><small>v{candidate.candidate_version}</small></span>
            </button>
          ))}
        </div>
        {workspace.hasMore && <button type="button" className="devices-load-more" disabled={workspace.loading} onClick={() => void workspace.loadMore()}>
          {workspace.loading ? '正在加载…' : '加载更多候选'}
        </button>}
      </div>

      <div className="discovery-detail-pane">
        {!selectedId && <div className="devices-empty"><strong>选择一个候选查看证据</strong><span>候选证据不会自动成为可信设备。</span></div>}
        {selectedId && workspace.detailLoading && <div className="devices-empty">正在读取候选详情…</div>}
        {selectedId && workspace.detailError && <div role="alert" className="devices-inline-error">{errorMessage(workspace.detailError)}<button type="button" className="devices-button" onClick={() => void workspace.selectCandidate(selectedId)}>重试</button></div>}
        {detail && !workspace.detailLoading && (
          <>
            <div className="discovery-detail-heading">
              <div><p className="devices-eyebrow">UNTRUSTED CANDIDATE · {detail.candidate_id}</p><h2>{detail.proposed_device_id ?? '未提供建议设备 ID'}</h2></div>
              <span className={`candidate-status ${detail.conflict ? 'conflict' : detail.status}`}>{STATUS_LABEL[detail.status]}</span>
            </div>
            <div className="candidate-trust-warning" role="note">此身份仍未受信任。观察到的 IP 和设备 ID 仅作为核验线索，不会成为可信连接状态或历史归属。</div>
            {isAdmin ? (
              <>
                <dl className="candidate-evidence-grid">
                  <div><dt>规范化 MAC</dt><dd>{detail.mac_address ?? '未提供'}</dd></div>
                  <div><dt>候选 IP（仅线索）</dt><dd>{detail.latest_ip ?? '未提供'}</dd></div>
                  <div><dt>来源</dt><dd>{(detail.sources ?? []).map((source) => SOURCE_LABEL[source] ?? source).join('、') || '无'}</dd></div>
                  <div><dt>观察数量 / 来源数量</dt><dd>{detail.observation_count} / {detail.source_count}</dd></div>
                  <div><dt>首次 / 最近发现</dt><dd>{time(detail.first_seen_at)}<br />{time(detail.last_seen_at)}</dd></div>
                  <div><dt>候选版本</dt><dd>{detail.candidate_version}</dd></div>
                  {detail.conflict_reason && <div className="wide"><dt>冲突原因</dt><dd>{detail.conflict_reason === 'multiple_proposed_device_ids' ? `同一 MAC 声称了多个设备 ID：${(detail.conflicting_proposed_device_ids ?? []).join('、')}` : detail.conflict_reason === 'deduplication_key_reused' ? '同一启动会话与序号出现了不同身份或 IP 证据；需人工核对后再决定是否认领。' : detail.conflict_reason === 'mac_already_bound' ? '该 MAC 已绑定到可信设备；必须先核验并处理现有设备绑定。' : detail.conflict_reason}</dd></div>}
                </dl>
                <section className="candidate-observations"><h3>脱敏观察记录</h3>
                  {(detail.observations ?? []).length === 0 ? <p>尚无可展示观察。</p> : (detail.observations ?? []).map((observation, index) => (
                    <div className="candidate-observation" key={`${observation.source}-${observation.received_at}-${index}`}>
                      <strong>{SOURCE_LABEL[observation.source] ?? observation.source}</strong><time>{time(observation.received_at)}</time>
                      <span>{observation.proposed_device_id ? `声称 ID：${observation.proposed_device_id}` : '未提供设备 ID'}{observation.ip_address ? ` · IP 线索：${observation.ip_address}` : ''}</span>
                      {observation.sanitized_metadata.device_type_hint && <span>类型线索：{observation.sanitized_metadata.device_type_hint}</span>}
                    </div>
                  ))}
                </section>
              </>
            ) : (
              <div className="candidate-trust-warning">值守人员可以查看候选状态，但身份地址证据仅对管理员开放。</div>
            )}
            {actionError && <div role="alert" className="devices-inline-error">{actionError}<button type="button" className="devices-button ghost" onClick={() => { void workspace.refreshList(); void workspace.selectCandidate(selectedId); }}>刷新并保留草稿</button></div>}
            {isAdmin && !['claimed'].includes(detail.status) && (
              <div className="candidate-actions">
                {detail.status === 'ignored' ? (
                  <section className="candidate-action-card"><h3>恢复到待核验</h3><p>恢复不会自动信任该候选；之后仍需管理员人工认领。</p><button type="button" className="devices-button" disabled={workspace.actionPending} onClick={() => void restore()}>恢复候选</button></section>
                ) : (
                  <>
                    <form className="candidate-action-card" onSubmit={(event) => void claim(event)}>
                      <h3>人工核验并认领</h3>
                      <p>必须手工确认稳定 device ID 和档案。建议 ID 不会自动填入。</p>
                      {detail.conflict && detail.conflict_reason !== 'mac_already_bound' && <label className="candidate-confirm"><input type="checkbox" checked={resolveConflict} onChange={(event) => setResolveConflict(event.target.checked)} />我已核对上述冲突证据，并由以下手工填写的身份信息明确解决冲突</label>}
                      <div className="candidate-form-grid">
                        <label>device_id（认领后不可修改）<input required maxLength={64} value={claimDraft.device_id} onChange={(event) => setClaimDraft({ ...claimDraft, device_id: event.target.value })} /></label>
                        <label>显示名称<input required maxLength={100} value={claimDraft.display_name} onChange={(event) => setClaimDraft({ ...claimDraft, display_name: event.target.value })} /></label>
                        <label>设备类型<input required maxLength={64} value={claimDraft.device_type} onChange={(event) => setClaimDraft({ ...claimDraft, device_type: event.target.value })} /></label>
                        <label>区域 ID<input maxLength={64} value={claimDraft.area_id} onChange={(event) => setClaimDraft({ ...claimDraft, area_id: event.target.value })} /></label>
                        <label>重要性<select value={claimDraft.importance} onChange={(event) => setClaimDraft({ ...claimDraft, importance: event.target.value as DeviceImportance })}>{IMPORTANCE.map((value) => <option key={value} value={value}>{value}</option>)}</select></label>
                        <label>档案来源<select value={claimDraft.profile_source} onChange={(event) => setClaimDraft({ ...claimDraft, profile_source: event.target.value as ClaimCandidateInput['profile_source'] })}>{PROFILE_SOURCES.map((value) => <option key={value} value={value}>{value}</option>)}</select></label>
                      </div>
                      <p className="candidate-provision-note">认领仅建立 unknown 档案，不复制观察到的 IP 或心跳；还需人工配置专属 MQTT 凭据与 ACL，不会自动加入移动用户授权范围。</p>
                      <button type="submit" className="devices-button primary" disabled={workspace.actionPending || isBoundMacConflict || (detail.conflict && !resolveConflict)}>确认认领候选</button>
                    </form>
                    <section className="candidate-action-card"><h3>忽略候选</h3><label>忽略原因<textarea required maxLength={500} value={ignoreReason} onChange={(event) => setIgnoreReason(event.target.value)} /></label><button type="button" className="devices-button warning" disabled={workspace.actionPending || !ignoreReason.trim()} onClick={() => void ignore()}>保留证据并忽略</button></section>
                  </>
                )}
              </div>
            )}
            {!isAdmin && <p className="read-only-badge">operator · 只读</p>}
          </>
        )}
      </div>
    </section>
  );
}
