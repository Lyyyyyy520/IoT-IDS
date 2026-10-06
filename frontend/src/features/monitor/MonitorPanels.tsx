import { useMemo } from 'react';
import ReactEChartsCore from 'echarts-for-react/lib/core';
import * as echarts from 'echarts/core';
import { GraphChart } from 'echarts/charts';
import { TooltipComponent } from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';
import type {
  ConnectionStatus,
  MonitorCapability,
  MonitorDevice,
  MonitorGraphSnapshot,
  MonitorIncidentData,
  MonitorRealtimeEvent,
  SystemComponentHealth,
} from '../../api/v3Monitor';

echarts.use([GraphChart, TooltipComponent, CanvasRenderer]);

export type DeviceFilter = 'all' | ConnectionStatus;

const STATUS_LABELS: Record<ConnectionStatus, string> = {
  online: '在线',
  stale: '延迟',
  offline: '离线',
  unknown: '未知',
};

const MODE_LABELS: Record<MonitorDevice['operation_mode'], string> = {
  active: '运行中',
  maintenance: '维护中',
  disabled: '已停用',
};

const READINESS_LABELS: Record<SystemComponentHealth['readiness'], string> = {
  warming_up: '预热中',
  ready: '就绪',
  degraded: '降级',
};

const CAPABILITY_REASONS: Record<string, string> = {
  incident_store_not_migrated: '事件存储尚未完成迁移',
  recorded_incident_workflow_available: '事件工作流已就绪',
  no_observed_device_relationships: '暂无观测到的设备间关系',
};

function formatTime(value: string | null): string {
  if (!value) return '尚无记录';
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  }).format(new Date(value));
}

export function statusLabel(status: ConnectionStatus) {
  return STATUS_LABELS[status];
}

export function DeviceListPanel({
  devices,
  selectedId,
  onSelect,
  search,
  onSearch,
  filter,
  onFilter,
  typeFilter,
  onTypeFilter,
  riskFilter,
  onRiskFilter,
  deviceTypes,
  onManageDevice,
}: {
  devices: MonitorDevice[];
  selectedId: string | null;
  onSelect: (deviceId: string) => void;
  search: string;
  onSearch: (value: string) => void;
  filter: DeviceFilter;
  onFilter: (value: DeviceFilter) => void;
  typeFilter: string;
  onTypeFilter: (value: string) => void;
  riskFilter: string;
  onRiskFilter: (value: string) => void;
  deviceTypes: string[];
  onManageDevice: (deviceId: string) => void;
}) {
  const filters: DeviceFilter[] = ['all', 'online', 'stale', 'offline', 'unknown'];
  return (
    <section className="monitor-panel device-list-panel" aria-labelledby="device-list-title">
      <div className="panel-heading">
        <div>
          <p className="eyebrow">DEVICE INVENTORY</p>
          <h2 id="device-list-title">设备实况</h2>
        </div>
        <span className="panel-count">{devices.length}</span>
      </div>
      <label className="monitor-search">
        <span className="sr-only">搜索设备</span>
        <input
          value={search}
          onChange={(event) => onSearch(event.target.value)}
          placeholder="搜索名称、类型、区域或 IP"
          type="search"
        />
      </label>
      <div className="device-filters" aria-label="设备状态筛选">
        {filters.map((value) => (
          <button
            type="button"
            className={filter === value ? 'is-active' : ''}
            aria-pressed={filter === value}
            onClick={() => onFilter(value)}
            key={value}
          >
            {value === 'all' ? '全部' : STATUS_LABELS[value]}
          </button>
        ))}
      </div>
      <div className="device-filter-selects">
        <label className="monitor-filter-select">
          <span className="sr-only">按类型筛选</span>
          <select value={typeFilter} onChange={(event) => onTypeFilter(event.target.value)}>
            <option value="all">全部类型</option>
            {deviceTypes.map((type) => <option value={type} key={type}>{type}</option>)}
          </select>
        </label>
        <label className="monitor-filter-select">
          <span className="sr-only">按告警状态筛选</span>
          <select value={riskFilter} onChange={(event) => onRiskFilter(event.target.value)}>
            <option value="all">全部告警状态</option>
            <option value="undetected">未检测</option>
            <option value="normal">正常</option>
            <option value="alerted">有告警</option>
          </select>
        </label>
      </div>
      <div className="device-list" role="list">
        {devices.length === 0 ? (
          <div className="panel-empty compact">
            <strong>没有匹配的真实设备</strong>
            <span>当前筛选条件下没有数据库记录。</span>
          </div>
        ) : (
          devices.map((device) => (
            <div role="listitem" className="device-row-wrap" key={device.device_id}>
              <button
                type="button"
                className={`device-row ${selectedId === device.device_id ? 'is-selected' : ''}`}
                onClick={() => onSelect(device.device_id)}
              >
                <span className={`status-dot status-${device.connection_status}`} aria-hidden="true" />
                <span className="device-row-main">
                  <span className="device-name">{device.display_name}</span>
                  <span className="device-meta">
                    {device.device_type} · {device.area_id ?? '未分区'}
                  </span>
                  <span className="device-ip">{device.ip_address ?? '尚无 IP'}</span>
                </span>
                <span className="device-row-side">
                  <span className={`status-text status-${device.connection_status}`}>
                    {STATUS_LABELS[device.connection_status]}
                  </span>
                  <span>{MODE_LABELS[device.operation_mode]}</span>
                </span>
              </button>
              <button
                type="button"
                className="device-manage-link"
                onClick={() => onManageDevice(device.device_id)}
                aria-label={`查看或管理 ${device.display_name}`}
              >
                查看/管理设备
              </button>
            </div>
          ))
        )}
      </div>
    </section>
  );
}

function capabilityReason(capability: MonitorCapability): string {
  if (!capability.reason) return '后端未提供原因';
  return CAPABILITY_REASONS[capability.reason] ?? capability.reason;
}

const RISK_COLORS: Record<number, string> = {
  0: '#00CC66',  // 正常/绿（Low，规范色标）
  1: '#FFCC00',  // 低危/黄（Medium）
  2: '#FF8800',  // 中危/橙（High）
  3: '#FF4444',  // 高危/红（Critical）
};
const UNKNOWN_COLOR = '#8897a0';

function graphOption(graph: MonitorGraphSnapshot): Record<string, unknown> {
  const nodes = graph.nodes.map((node) => ({
    id: node.id,
    name: node.label,
    symbolSize: 18,
    itemStyle: {
      color: node.risk_level != null ? RISK_COLORS[node.risk_level] ?? UNKNOWN_COLOR : UNKNOWN_COLOR,
      borderColor: node.status === 'online' ? '#34c97b' : '#63717b',
      borderWidth: 2,
    },
    label: { show: true, position: 'bottom', fontSize: 10, color: '#7893a3' },
    device_type: node.type,
    status: node.status,
    risk_name: node.risk_name,
    ip: node.ip,
  }));
  const links = graph.edges.map((edge) => ({
    source: edge.source,
    target: edge.target,
    lineStyle: {
      width: Math.min(4, Math.max(1, Math.ceil(Math.log2(edge.packets + 1)))),
      curveness: 0.08,
    },
  }));
  return {
    animation: false,
    tooltip: {
      formatter: (params: unknown) => {
        const p = params as {
          dataType?: string;
          data?: {
            name?: string;
            device_type?: string;
            status?: string;
            risk_name?: string | null;
            ip?: string | null;
            source?: string;
            target?: string;
          };
        };
        if (p.dataType === 'node') {
          const d = p.data ?? {};
          return [
            d.name,
            d.device_type ? `类型：${d.device_type}` : '',
            d.status ? `状态：${d.status}` : '',
            `风险：${d.risk_name ?? '未检测'}`,
            d.ip ? `IP：${d.ip}` : '',
          ].filter(Boolean).join('<br/>');
        }
        if (p.dataType === 'edge') {
          return `${p.data?.source ?? ''} ↔ ${p.data?.target ?? ''}`;
        }
        return '';
      },
    },
    series: [{
      type: 'graph',
      layout: 'force',
      data: nodes,
      links,
      roam: true,
      draggable: true,
      force: { repulsion: 140, edgeLength: 90, gravity: 0.1 },
      emphasis: { focus: 'adjacency', lineStyle: { width: 3 } },
      lineStyle: { color: '#3b6275' },
    }],
  };
}

export function CapabilityPanel({
  capability,
  graph,
  onSelect,
  visibleIds,
}: {
  capability: MonitorCapability;
  graph?: MonitorGraphSnapshot | null;
  onSelect?: (deviceId: string) => void;
  visibleIds?: Set<string> | null;
}) {
  const filteredGraph = useMemo(() => {
    if (!graph || !visibleIds) return graph;
    const kept = new Set(graph.nodes.filter((node) => visibleIds.has(node.id)).map((node) => node.id));
    return {
      ...graph,
      nodes: graph.nodes.filter((node) => visibleIds.has(node.id)),
      edges: graph.edges.filter((edge) => kept.has(edge.source) && kept.has(edge.target)),
    };
  }, [graph, visibleIds]);
  const renderable = capability.available && filteredGraph != null && filteredGraph.nodes.length > 0;
  return (
    <section className="monitor-panel graph-panel" aria-labelledby="graph-title">
      <div className="panel-heading graph-heading">
        <div>
          <p className="eyebrow">GNN RELATION VIEW</p>
          <h2 id="graph-title">设备关系主视图</h2>
        </div>
        <span className={`capability-badge ${capability.available ? 'available' : 'unavailable'}`}>
          {capability.available ? '能力可用' : '能力不可用'}
        </span>
      </div>
      {renderable ? (
        <div className="graph-canvas" style={{ flex: 1, minHeight: 0 }} data-testid="graph-capability">
          <ReactEChartsCore
            echarts={echarts}
            option={graphOption(filteredGraph)}
            notMerge
            lazyUpdate
            onEvents={onSelect ? {
              click: (params: unknown) => {
                const p = params as { dataType?: string; data?: { id?: string } };
                if (p.dataType === 'node' && p.data?.id) onSelect(p.data.id);
              },
            } : undefined}
            style={{ width: '100%', height: '100%', minHeight: 320 }}
            opts={{ renderer: 'canvas' }}
          />
        </div>
      ) : (
        <div className="graph-empty" data-testid="graph-capability">
          <div className="graph-empty-mark" aria-hidden="true">◇</div>
          <strong>
            {capability.available ? '暂无设备节点' : 'GNN 图暂不可用'}
          </strong>
          <p>
            {capability.available
              ? '当前没有可渲染的设备节点与边，页面不会生成替代拓扑。'
              : capabilityReason(capability)}
          </p>
          <span>未绘制静态节点、随机连线或装饰性拓扑</span>
        </div>
      )}
    </section>
  );
}

function eventTitle(event: MonitorRealtimeEvent): string {
  if (event.event_type === 'device.connection_changed') {
    return `${event.device_id}：${STATUS_LABELS[event.payload.from]} → ${STATUS_LABELS[event.payload.to]}`;
  }
  if (event.event_type === 'device.telemetry_updated') {
    return `${event.device_id}：收到新遥测`;
  }
  if (event.event_type === 'device.inventory_changed') {
    return `${event.payload.device_id}：设备清单 ${event.payload.action}`;
  }
  if (event.event_type === 'device.discovered') {
    return '发现了待人工核验设备';
  }
  return `${event.payload.component_id}：${READINESS_LABELS[event.payload.readiness]}`;
}

export function RecentEventsPanel({
  events,
  incident,
  incidents,
  onViewIncident,
}: {
  events: MonitorRealtimeEvent[];
  incident: MonitorCapability;
  incidents?: MonitorIncidentData | null;
  onViewIncident: (incidentId: string) => void;
}) {
  const summaries = [...(incidents?.active ?? []), ...(incidents?.recent ?? [])]
    .filter((item, index, all) => all.findIndex((candidate) => candidate.incident_id === item.incident_id) === index)
    .slice(0, 5);
  return (
    <section className="monitor-panel recent-panel" aria-labelledby="recent-title">
      <div className="panel-heading recent-heading">
        <div>
          <p className="eyebrow">PERSISTED EVENT FEED</p>
          <h2 id="recent-title">最近状态事件</h2>
        </div>
        <span className={`capability-badge ${incident.available ? 'available' : 'unavailable'}`}>
          事件处置：{incident.available ? '可用' : '不可用'}
        </span>
      </div>
      {!incident.available && (
        <div className="capability-note" data-testid="incident-capability">
          {capabilityReason(incident)}；此区域仅展示本次连接收到的设备与组件状态事件，
          不能代表“没有攻击”。
        </div>
      )}
      {incident.available && (
        <div className="monitor-incident-summary" data-testid="monitor-incident-summary">
          {summaries.length === 0 ? (
            <div className="panel-empty compact">
              <strong>当前没有已记录事件</strong>
              <span>这不是“系统安全”或“无攻击”的检测结论。</span>
            </div>
          ) : summaries.map((item) => (
            <button type="button" key={item.incident_id} onClick={() => onViewIncident(item.incident_id)}>
              <span className={`monitor-incident-severity severity-${item.severity}`}>{item.severity}</span>
              <span><strong>{item.admin_title}</strong><small>{item.status} · {formatTime(item.updated_at)}</small></span>
              <span aria-hidden="true">→</span>
            </button>
          ))}
        </div>
      )}
      <h3 className="incremental-heading">本次连接的状态增量</h3>
      <div className="recent-event-list">
        {events.length === 0 ? (
          <div className="panel-empty compact">
            <strong>本次连接尚未收到增量状态事件</strong>
            <span>这不是安全结论；事件处置能力以上方后端状态为准。</span>
          </div>
        ) : (
          events.map((event) => (
            <div className="recent-event" key={event.event_id}>
              <span className="event-id">#{event.event_id}</span>
              <span className="event-copy">
                <strong>{eventTitle(event)}</strong>
                <span>{formatTime(event.occurred_at)} · 状态版本 {event.state_version}</span>
              </span>
            </div>
          ))
        )}
      </div>
    </section>
  );
}

export function DeviceDetailsPanel({ device }: { device: MonitorDevice | null }) {
  return (
    <section className="monitor-panel details-panel" aria-labelledby="details-title">
      <div className="panel-heading">
        <div>
          <p className="eyebrow">SELECTED DEVICE</p>
          <h2 id="details-title">设备状态摘要</h2>
        </div>
      </div>
      {!device ? (
        <div className="panel-empty compact">
          <strong>尚未选择设备</strong>
          <span>从左侧真实设备列表中选择一项。</span>
        </div>
      ) : (
        <div className="detail-content">
          <div className="selected-device-heading">
            <span className={`status-dot status-${device.connection_status}`} aria-hidden="true" />
            <div>
              <strong>{device.display_name}</strong>
              <span>{device.device_id}</span>
            </div>
          </div>
          <dl className="detail-grid">
            <div><dt>连接</dt><dd className={`status-${device.connection_status}`}>{STATUS_LABELS[device.connection_status]}</dd></div>
            <div><dt>风险等级</dt><dd className={device.risk_level !== null ? `risk-l${device.risk_level}` : ''}>{device.risk_level !== null ? `${device.risk_name ?? '未命名'}${device.risk_confidence !== null ? ` ${(device.risk_confidence * 100).toFixed(0)}%` : ''}` : '未检测'}</dd></div>
            <div><dt>运行模式</dt><dd>{MODE_LABELS[device.operation_mode]}</dd></div>
            <div><dt>类型</dt><dd>{device.device_type}</dd></div>
            <div><dt>区域</dt><dd>{device.area_id ?? '未分区'}</dd></div>
            <div><dt>当前 IP</dt><dd>{device.ip_address ?? '尚无 IP'}</dd></div>
            <div><dt>状态版本</dt><dd>{device.state_version}</dd></div>
            <div><dt>设备观测时间</dt><dd>{formatTime(device.observed_at)}</dd></div>
            <div><dt>后端接收时间</dt><dd>{formatTime(device.received_at)}</dd></div>
            <div className="detail-wide"><dt>数据来源</dt><dd>{device.sources.length ? device.sources.join('、') : '尚无来源'}</dd></div>
          </dl>
          <div className="behavior-panel">
            <h3 className="behavior-title">行为异常提示</h3>
            {device.risk_level === null ? (
              <p className="behavior-note">该设备尚未产生 GNN 检测结论；在出现风险前不展示异常，也不会伪造分析结果。</p>
            ) : (
              <>
                <div className="behavior-evidence-grid">
                  <div><strong>触发指标</strong><span>设备图 GNN 分类</span></div>
                  <div><strong>观测值</strong><span>{device.risk_name ?? '未命名'} · 置信度 {device.risk_confidence !== null ? `${(device.risk_confidence * 100).toFixed(0)}%` : '未知'}</span></div>
                  <div><strong>参考基线</strong><span>尚未接入</span></div>
                </div>
                <p className="behavior-observation-time">检测时间：{formatTime(device.risk_detected_at)}</p>
                <div className="behavior-baseline-gap">
                  <strong>通信基线尚未接入</strong>
                  <p>当前检测只输出风险类别与置信度，尚未计算频率、协议/端口、连接对象的参考基线，因此不展示偏离结论。</p>
                </div>
              </>
            )}
          </div>
        </div>
      )}
    </section>
  );
}

export function SystemHealthPanel({ components }: { components: SystemComponentHealth[] }) {
  return (
    <section className="monitor-panel health-panel" aria-labelledby="health-title">
      <div className="panel-heading">
        <div>
          <p className="eyebrow">SERVICE READINESS</p>
          <h2 id="health-title">系统组件健康</h2>
        </div>
      </div>
      <div className="component-list">
        {components.length === 0 ? (
          <div className="panel-empty compact">
            <strong>尚未收到组件状态</strong>
            <span>系统不会把缺少记录解释为全部健康。</span>
          </div>
        ) : (
          components.map((component) => (
            <article className="component-row" key={component.component_id}>
              <div>
                <strong>{component.component_id}</strong>
                <span>{component.reason ?? '后端未报告异常原因'}</span>
              </div>
              <div className="component-side">
                <span className={`readiness readiness-${component.readiness}`}>
                  {READINESS_LABELS[component.readiness]}
                </span>
                <time dateTime={component.updated_at}>{formatTime(component.updated_at)}</time>
              </div>
            </article>
          ))
        )}
      </div>
    </section>
  );
}
