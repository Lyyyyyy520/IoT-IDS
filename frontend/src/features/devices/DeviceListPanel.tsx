import type { DeviceListItem } from '../../api/v3Devices';
import type { DeviceFilters } from './useDeviceWorkspace';
import {
  CONNECTION_LABELS,
  IMPORTANCE_LABELS,
  MODE_LABELS,
  SOURCE_LABELS,
  formatDeviceTime,
} from './deviceUi';

interface Props {
  filters: DeviceFilters;
  onFilters: (patch: Partial<DeviceFilters>) => void;
  onClearFilters: () => void;
  items: DeviceListItem[];
  total: number;
  selectedId: string | null;
  onSelect: (deviceId: string) => void;
  loading: boolean;
  loadingMore: boolean;
  hasMore: boolean;
  onLoadMore: () => void;
  onRefresh: () => void;
  lastUpdatedAt: string | null;
  error: string | null;
}

export default function DeviceListPanel({
  filters,
  onFilters,
  onClearFilters,
  items,
  total,
  selectedId,
  onSelect,
  loading,
  loadingMore,
  hasMore,
  onLoadMore,
  onRefresh,
  lastUpdatedAt,
  error,
}: Props) {
  return (
    <section className="devices-panel device-inventory" aria-labelledby="device-inventory-title">
      <div className="devices-panel-heading">
        <div>
          <p className="devices-eyebrow">V3 DEVICE INVENTORY</p>
          <h2 id="device-inventory-title">设备档案</h2>
        </div>
        <span className="devices-total">{total}</span>
      </div>

      <div className="device-filter-stack" aria-label="设备筛选">
        <label className="device-filter-wide">
          <span>搜索名称或 device_id</span>
          <input
            type="search"
            value={filters.search}
            onChange={(event) => onFilters({ search: event.target.value })}
            placeholder="输入后自动搜索"
          />
        </label>
        <label>
          <span>连接状态</span>
          <select
            value={filters.connectionStatus}
            onChange={(event) => onFilters({
              connectionStatus: event.target.value as DeviceFilters['connectionStatus'],
            })}
          >
            <option value="all">全部</option>
            <option value="online">在线</option>
            <option value="stale">延迟</option>
            <option value="offline">离线</option>
            <option value="unknown">未知</option>
          </select>
        </label>
        <label>
          <span>运行模式</span>
          <select
            value={filters.operationMode}
            onChange={(event) => onFilters({
              operationMode: event.target.value as DeviceFilters['operationMode'],
            })}
          >
            <option value="all">全部</option>
            <option value="active">运行中</option>
            <option value="maintenance">维护中</option>
            <option value="disabled">已停用</option>
          </select>
        </label>
        <label>
          <span>生命周期</span>
          <select
            value={filters.lifecycle}
            onChange={(event) => onFilters({
              lifecycle: event.target.value as DeviceFilters['lifecycle'],
            })}
          >
            <option value="all">全部</option>
            <option value="active">使用中</option>
            <option value="retired">已退役</option>
          </select>
        </label>
        <label className="device-filter-wide">
          <span>区域 ID</span>
          <input
            value={filters.areaId}
            onChange={(event) => onFilters({ areaId: event.target.value })}
            placeholder="精确区域筛选"
          />
        </label>
        <div className="device-filter-actions device-filter-wide">
          <button type="button" className="devices-button ghost" onClick={onClearFilters}>清空筛选</button>
          <button type="button" className="devices-button ghost" onClick={onRefresh} disabled={loading}>
            {loading ? '刷新中…' : '手动刷新'}
          </button>
        </div>
      </div>

      <div className="device-list-meta">
        <span>已加载 {items.length} / {total}</span>
        <span>最后更新 {formatDeviceTime(lastUpdatedAt)}</span>
      </div>

      {error && <div className="devices-inline-error" role="alert">{error}</div>}

      <div className="v3-device-list" role="list" aria-busy={loading}>
        {loading && items.length === 0 ? (
          <div className="devices-empty" aria-live="polite">正在读取真实设备列表…</div>
        ) : items.length === 0 && !error ? (
          <div className="devices-empty">
            <strong>没有匹配的真实设备</strong>
            <span>当前筛选条件未返回设备，页面不会填充示例数据。</span>
          </div>
        ) : items.map((device) => (
          <button
            type="button"
            role="listitem"
            key={device.device_id}
            className={`v3-device-card ${selectedId === device.device_id ? 'is-selected' : ''}`}
            onClick={() => onSelect(device.device_id)}
          >
            <span className={`device-status-mark status-${device.connection_status}`} aria-hidden="true" />
            <span className="v3-device-card-main">
              <span className="v3-device-name">{device.display_name}</span>
              <span className="v3-device-id">{device.device_id}</span>
              <span className="v3-device-meta">
                {device.device_type} · {device.area_id ?? '未分区'} · {SOURCE_LABELS[device.profile_source]}
              </span>
              <span className="v3-device-ip">{device.ip_address ?? '尚无 IP'}</span>
            </span>
            <span className="v3-device-card-side">
              <span className={`status-${device.connection_status}`}>{CONNECTION_LABELS[device.connection_status]}</span>
              <span>{MODE_LABELS[device.operation_mode]}</span>
              <span>{IMPORTANCE_LABELS[device.importance]} · v{device.profile_version}</span>
              {device.lifecycle_status === 'retired' && <span className="lifecycle-retired">已退役</span>}
            </span>
          </button>
        ))}
      </div>

      {hasMore && (
        <button
          type="button"
          className="devices-load-more"
          onClick={onLoadMore}
          disabled={loadingMore}
        >
          {loadingMore ? '加载中…' : '加载更多'}
        </button>
      )}
    </section>
  );
}
