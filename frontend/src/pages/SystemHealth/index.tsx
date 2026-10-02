import { useCallback, useEffect, useState } from 'react';
import { Alert, Button, Card, Spin, Tag } from 'antd';
import { ReloadOutlined } from '@ant-design/icons';
import { getSystemHealth, type SystemHealthComponent, type SystemHealthResponse, type SystemHealthStatus } from '../../api/v3SystemHealth';
import './system-health.css';

const componentLabels: Record<string, string> = {
  api: 'API 服务', database: '数据库连接', schema: '数据库结构',
  integrity_check: '完整性检查', mqtt: 'MQTT 心跳接收',
  traffic: '设备流量聚合', event_log: 'SSE 事件日志',
  incident: '事件处置', mobile: '移动端 session',
  discovery: '未知设备发现', graph: 'GNN / Graph 能力',
};

const statusLabels: Record<SystemHealthStatus, string> = {
  ready: '就绪', warming_up: '启动中', degraded: '降级', unavailable: '不可用',
};

const reasonMessages: Record<string, string> = {
  database_path_not_configured: '尚未配置数据库路径。配置正确的现有数据库后再启动服务。',
  database_unavailable: '数据库当前不可用。根据 reason code 检查数据库和写入进程。',
  database_file_missing: '数据库文件不存在。先确认部署配置与恢复来源；服务不会自动建立空库。',
  database_open_failed: '数据库无法读取。检查文件权限、文件系统状态和 SQLite 错误日志。',
  database_busy: '数据库正被其他写入者占用。确认维护任务或运行进程后再重试。',
  database_locked: '数据库当前被锁定。确认其他进程已完成写入，再检查事务是否中断。',
  database_read_only: '数据库或目录处于只读状态。检查部署账户的文件与目录权限。',
  database_disk_full: '磁盘空间不足。先释放或扩展存储，再用备份恢复并重试写入。',
  database_corrupt: 'SQLite 报告数据库损坏。停止写入并从完整性校验通过的备份恢复。',
  database_io_error: '数据库遇到文件系统 I/O 错误。检查磁盘和文件系统后进行备份恢复评估。',
  database_wal_mode_unsupported: '数据库使用 WAL 模式。先停写并制作隔离副本，再按回滚日志模式执行维护。',
  schema_ledger_missing: '找不到 schema migration ledger。离线检查数据库版本，不要自动建库。',
  schema_incomplete: 'schema 尚未完整升级。先备份，再按迁移恢复流程处理。',
  schema_invalid: 'schema 元数据无法验证。停止写入并检查迁移 ledger。',
  schema_checksum_mismatch: 'migration checksum 不匹配。停止写入并恢复与当前版本一致的数据库。',
  schema_object_missing: '预期数据库对象缺失。先对照受控备份检查 schema。',
  schema_version_unsupported: '数据库版本高于当前服务支持版本。使用匹配版本的服务恢复。',
  legacy_schema_unavailable: '旧版业务表结构不完整。检查部署升级记录和回滚点。',
  integrity_check_failed: 'SQLite 完整性检查未通过。停止写入并从已验证备份恢复。',
  mqtt_disabled: 'MQTT 接收未启用。需要心跳接收时检查受控环境配置。',
  mqtt_managed_by_single_worker: '多进程 Web worker 不启动 MQTT；应由单独的单实例运行进程负责订阅。',
  mqtt_not_started: 'MQTT 已配置但运行服务尚未启动。检查单进程运行入口和启动日志。',
  traffic_not_started: '聚合服务尚未处理本次进程启动后的流量。',
  component_restarting: '组件正在根据本次运行重新计算状态。',
  maintenance_plan_read_only: '维护计划严格只读，因此不会写入最近计划时间。',
  mqtt_start_failed: 'MQTT 服务启动失败。检查配置和服务日志后再重启。',
  mqtt_configuration_error: 'MQTT 配置校验失败。根据 reason code 检查配置项。',
  debug_reloader_parent: '当前为开发重载器父进程，运行服务由子进程负责。',
  event_log_unavailable: '事件日志不可读。先检查 schema、数据库锁和 SQLite 状态。',
  event_log_gap_requires_snapshot: '事件日志存在断档。SSE 客户端必须重新获取快照并从新游标订阅。',
  event_log_requires_snapshot: '当前只保留事件游标。客户端必须先获取快照，再从最新游标订阅。',
  sse_replay_window_pruned: '旧事件已按保留期限清理；落后于保留窗口的客户端会收到 snapshot.required。',
  aggregation_transaction_failed: '流量聚合事务失败。检查 SQLite 写入错误和未提交事务，再确认聚合状态。',
  candidate_capacity_reached: '未知设备候选已达容量限制。先由运维人员处置现有候选，再检查配置。',
  discovery_storage_error: '未知设备发现记录写入失败。检查数据库健康和发现服务日志。',
  traffic_store_unavailable: '流量聚合存储当前不可用。检查数据库 schema、权限和 SQLite 错误。',
  worker_stop_timeout: '后台服务停止超时。确认运行时线程退出后再重启或维护数据库。',
  graph_capability_unavailable: 'GNN 与 Graph capability 保持不可用。',
  no_maintenance_run: '尚无成功的维护记录。维护计划与备份目录需由值班负责人审核。',
  request_failed: '最近请求发生服务错误。确认数据库可用后重试并检查组件日志。',
  component_degraded: '组件报告了未识别的 reason code。检查运维文档和后端日志。',
};
const reasonAdvice: Record<string, string> = {
  database_busy: '建议：核对活跃写入进程和维护窗口。',
  database_locked: '建议：确认事务已结束；不要手工删除 SQLite sidecar。',
  database_disk_full: '建议：先解决容量问题，不要在故障状态下执行 VACUUM。',
  database_corrupt: '建议：从通过 integrity_check 的备份恢复，并保存故障库供调查。',
  event_log_gap_requires_snapshot: '建议：Web 与 APP SSE 客户端重新获取当前快照。',
  event_log_requires_snapshot: '建议：重新获取快照后使用返回的事件游标恢复订阅。',
  graph_capability_unavailable: '这是当前预期状态，保持 unavailable。',
  maintenance_plan_read_only: '计划信息只在命令输出中记录；最近 apply 时间会保存在数据库健康记录中。',
};
const componentOrder = [
  'api', 'database', 'schema', 'integrity_check', 'mqtt', 'traffic',
  'event_log', 'incident', 'mobile', 'discovery', 'graph',
];

function formatTime(value?: string | null) {
  if (!value) return '暂无记录';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? '时间未知' : date.toLocaleString();
}
function formatBytes(value?: number | null) {
  if (value == null || !Number.isFinite(value)) return '不可用';
  if (value < 1024) return value + ' B';
  const units = ['KiB', 'MiB', 'GiB', 'TiB'];
  let size = value;
  let unit = -1;
  do {
    size /= 1024;
    unit += 1;
  } while (size >= 1024 && unit < units.length - 1);
  return size.toFixed(1) + ' ' + units[unit];
}

function ComponentCard({ name, component }: { name: string; component?: SystemHealthComponent }) {
  const reportedStatus = component?.status as string | undefined;
  const status: SystemHealthStatus = reportedStatus && reportedStatus in statusLabels
    ? reportedStatus as SystemHealthStatus
    : 'unavailable';
  const reason = component?.reason_code;
  const title = componentLabels[name] ?? name;
  const message = reason
    ? (reasonMessages[reason] ?? reasonMessages.component_degraded)
    : '组件已报告当前状态。';

  return (
    <Card className={'health-component-card status-' + status} size="small">
      <header className="health-card-header">
        <h2>{title}</h2>
        <Tag className={'health-status-tag status-' + status}>{statusLabels[status]}</Tag>
      </header>
      {name === 'database' && (
        <p className="health-detail">
          文件{component?.exists === true ? '存在' : component?.exists === false ? '不存在' : '未知'} ·
          读取{component?.readable ? '可用' : '不可用'} ·
          写入{component?.writable === true ? '可用' : component?.writable === false ? '不可用' : '未知'}
        </p>
      )}
      {name === 'schema' && component?.version != null && (
        <p className="health-detail">
          Schema v{component.version} · 迁移{component.migration_complete ? '完整' : '不完整'} ·
          checksum{component.migration_checksums_valid === true ? '通过' : component.migration_checksums_valid === false ? '异常' : '未知'} ·
          旧版结构{component.legacy_schema_ready ? '可用' : '不可用'}
        </p>
      )}
      {name === 'integrity_check' && (
        <p className="health-detail">检查结果：{component?.result === 'ok' ? '通过' : component?.result === 'failed' ? '失败' : '未知'}</p>
      )}
      {name === 'traffic' && (
        <p className="health-detail">流量聚合：{statusLabels[component?.aggregation_status ?? status]}</p>
      )}
      {name === 'event_log' && component?.retained_events != null && (
        <p className="health-detail">
          保留 {component.retained_events} 条 · 游标 {component.oldest_event_id ?? '—'}–{component.latest_cursor ?? '—'}
        </p>
      )}
      <p className="health-reason">{message}</p>
      {reason && reasonAdvice[reason] && <p className="health-advice">{reasonAdvice[reason]}</p>}
      <time className="health-updated">更新时间：{formatTime(component?.updated_at)}</time>
    </Card>
  );
}

export default function SystemHealthPage() {
  const [health, setHealth] = useState<SystemHealthResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async (signal?: AbortSignal) => {
    setLoading(true);
    setError(null);
    try {
      setHealth(await getSystemHealth(signal));
    } catch (failure) {
      if (signal?.aborted) return;
      setError(failure instanceof Error ? failure.message : '系统健康信息暂时不可用。');
    } finally {
      if (!signal?.aborted) setLoading(false);
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    void refresh(controller.signal);
    return () => controller.abort();
  }, [refresh]);

  return (
    <main className="system-health-page">
      <section className="system-health-header">
        <div>
          <p className="system-health-eyebrow">OPERATIONS / READ ONLY</p>
          <h1>系统健康</h1>
          <p>查看运行组件、数据库完整性和最近维护记录。页面只读取健康 API，不执行维护操作。</p>
        </div>
        <Button icon={<ReloadOutlined />} onClick={() => void refresh()} loading={loading}>刷新状态</Button>
      </section>

      {error && (
        <Alert
          type="error"
          showIcon
          message="健康信息不可用"
          description={error}
          action={<Button size="small" onClick={() => void refresh()}>重试</Button>}
        />
      )}

      {loading && !health ? (
        <div className="system-health-loading"><Spin /> 正在读取组件状态…</div>
      ) : (
        <>
          <section className="system-health-summary">
            <span>检查时间：{formatTime(health?.observed_at)}</span>
            <span>
              最近 plan：{formatTime(health?.maintenance.last_plan_at)}
              {health?.maintenance.last_plan_reason_code && ' · ' + (reasonMessages[health.maintenance.last_plan_reason_code] ?? reasonMessages.component_degraded)}
            </span>
            <span>
              最近 apply：{formatTime(health?.maintenance.last_apply_at ?? health?.maintenance.last_successful_at)}
              {health?.maintenance.reason_code && ' · ' + (reasonMessages[health.maintenance.reason_code] ?? reasonMessages.component_degraded)}
            </span>
            <span>自动维护：{health?.automatic_maintenance ? '已启用' : '已关闭'}</span>
          </section>
          <section className="health-component-grid">
            {componentOrder.map((name) => (
              <ComponentCard key={name} name={name} component={health?.components[name]} />
            ))}
          </section>
          <Card className="health-capacity-card" size="small" title="数据库容量">
            <div className="health-capacity-grid">
              <div><span>数据库文件</span><strong>{formatBytes(health?.capacity.database_file_bytes)}</strong></div>
              <div><span>页面总量</span><strong>{health?.capacity.page_count ?? '不可用'}</strong></div>
              <div><span>空闲页面</span><strong>{health?.capacity.free_pages ?? '不可用'}</strong></div>
              <div><span>空闲页面空间</span><strong>{formatBytes(health?.capacity.free_bytes)}</strong></div>
              <div><span>磁盘可用空间</span><strong>{formatBytes(health?.capacity.disk_free_bytes)}</strong></div>
            </div>
          </Card>
          <p className="system-health-footnote">未知状态和 unavailable 都按异常显示；维护命令仅供授权运维人员在备份后显式执行。</p>
        </>
      )}
    </main>
  );
}
