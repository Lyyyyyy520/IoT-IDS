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
  MonitorGraphNode,
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

const RISK_VARIABLES: Record<number, string> = {
  0: '--risk-low',
  1: '--risk-medium',
  2: '--risk-high',
  3: '--risk-critical',
};
const GRAPH_SYMBOLS = ['circle', 'rect', 'roundRect', 'diamond', 'triangle', 'pin'] as const;
const GRAPH_SYMBOL_CLASSES: Record<string, string> = {
  circle: 'circle', rect: 'rect', roundRect: 'round-rect', diamond: 'diamond',
  triangle: 'triangle', pin: 'pin',
};
const RELATION_LABELS = {
  observed_traffic: '观测到的通信',
  configured_linkage: '配置声明的联动',
  unknown: '关系来源未标注',
} as const;

function themeColor(variable: string): string {
  if (typeof document === 'undefined' || typeof window === 'undefined') return 'currentColor';
  return window.getComputedStyle(document.documentElement).getPropertyValue(variable).trim() || 'currentColor';
}

export function graphTypeSymbols(nodes: MonitorGraphNode[]): Record<string, string> {
  const types = [...new Set(nodes.map((node) => node.type))].sort((a, b) => a.localeCompare(b, 'zh-CN'));
  return Object.fromEntries(types.map((type, index) => [type, GRAPH_SYMBOLS[index % GRAPH_SYMBOLS.length]]));
}

export function buildGraphOption(
  graph: MonitorGraphSnapshot,
  highlightedDeviceIds: ReadonlySet<string> = new Set(),
): Record<string, unknown> {
  const hasHighlight = highlightedDeviceIds.size > 0;
  const typeSymbols = graphTypeSymbols(graph.nodes);
  const names = new Map(graph.nodes.map((node) => [node.id, node.label]));
  const nodes = graph.nodes.map((node) => ({
    id: node.id,
    name: node.label,
    symbol: typeSymbols[node.type] ?? 'circle',
    symbolSize: highlightedDeviceIds.has(node.id) ? 28 : 22,
    itemStyle: {
      color: node.risk_level != null && RISK_VARIABLES[node.risk_level]
        ? themeColor(RISK_VARIABLES[node.risk_level])
        : themeColor('--text-secondary'),
      borderColor: highlightedDeviceIds.has(node.id)
        ? themeColor('--accent-blue')
        : themeColor(node.status === 'online' ? '--risk-low' : node.status === 'stale' ? '--risk-medium' : node.status === 'offline' ? '--risk-critical' : '--text-secondary'),
      borderWidth: highlightedDeviceIds.has(node.id) ? 5 : 2,
      opacity: hasHighlight && !highlightedDeviceIds.has(node.id) ? 0.24 : 1,
      shadowBlur: highlightedDeviceIds.has(node.id) ? 12 : 0,
      shadowColor: themeColor('--accent-blue'),
    },
    label: {
      show: true,
      position: 'bottom',
      fontSize: 14,
      color: themeColor('--text-primary'),
      fontWeight: highlightedDeviceIds.has(node.id) ? 700 : 400,
    },
    device_type: node.type,
    status: node.status,
    risk_name: node.risk_name,
    ip: node.ip,
  }));
  const links = graph.edges.map((edge) => {
    const touchesHighlightedDevice = highlightedDeviceIds.has(edge.source)
      || highlightedDeviceIds.has(edge.target);
    const edgeColor = edge.relation_type === 'observed_traffic'
      ? '--accent-cyan'
      : edge.relation_type === 'configured_linkage'
        ? '--accent-purple'
        : '--text-secondary';
    return {
      source: edge.source,
      target: edge.target,
      source_label: names.get(edge.source) ?? edge.source,
      target_label: names.get(edge.target) ?? edge.target,
      relation_type: edge.relation_type,
      lineStyle: {
        color: hasHighlight && touchesHighlightedDevice ? themeColor('--accent-blue') : themeColor(edgeColor),
        type: edge.relation_type === 'configured_linkage'
          ? 'dashed'
          : edge.relation_type === 'unknown' ? 'dotted' : 'solid',
        width: hasHighlight && touchesHighlightedDevice
          ? 4
          : Math.min(4, Math.max(1, Math.ceil(Math.log2(edge.packets + 1)))),
        opacity: hasHighlight
          ? touchesHighlightedDevice ? 1 : 0.16
          : 0.9,
        curveness: 0.08,
      },
    };
  });
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
            source_label?: string;
            target_label?: string;
            relation_type?: keyof typeof RELATION_LABELS;
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
          const data = p.data ?? {};
          const relationship = data.relation_type
            ? RELATION_LABELS[data.relation_type]
            : RELATION_LABELS.unknown;
          return `${data.source_label ?? data.source ?? ''} ↔ ${data.target_label ?? data.target ?? ''}<br/>${relationship}`;
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
  highlightedDeviceIds,
  highlightLabel,
  onClearHighlight,
}: {
  capability: MonitorCapability;
  graph?: MonitorGraphSnapshot | null;
  onSelect?: (deviceId: string) => void;
  visibleIds?: Set<string> | null;
  highlightedDeviceIds?: ReadonlySet<string>;
  highlightLabel?: string | null;
  onClearHighlight?: () => void;
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
  const allTypes = graphTypeSymbols(graph?.nodes ?? []);
  const visibleHighlightedCount = filteredGraph?.nodes.filter(
    (node) => highlightedDeviceIds?.has(node.id),
  ).length ?? 0;
  const renderable = capability.available && filteredGraph != null && filteredGraph.nodes.length > 0;
  return (
    <section className="monitor-panel graph-panel" aria-labelledby="graph-title">
      <div className="panel-heading graph-heading">
        <div>
          <p className="eyebrow">DEVICE RELATION VIEW · 近 24 小时</p>
          <h2 id="graph-title">设备通信与配置关系</h2>
        </div>
        <span className={`capability-badge ${capability.available ? 'available' : 'unavailable'}`}>
          {capability.available ? '能力可用' : '能力不可用'}
        </span>
      </div>
      {renderable ? (
        <>
          <div className="graph-legend" aria-label="设备关系图例">
            <div className="graph-legend-group" aria-label="设备类型">
              <strong>设备类型</strong>
              {Object.entries(allTypes).map(([type, symbol]) => (
                <span className="graph-legend-item" key={type}>
                  <i className={`graph-type-symbol graph-type-${GRAPH_SYMBOL_CLASSES[symbol] ?? 'circle'}`} aria-hidden="true" />
                  {type}
                </span>
              ))}
            </div>
            <div className="graph-legend-group" aria-label="关系来源">
              <strong>关系来源</strong>
              <span className="graph-legend-item"><i className="graph-line graph-line-observed" aria-hidden="true" />{RELATION_LABELS.observed_traffic}</span>
              <span className="graph-legend-item"><i className="graph-line graph-line-configured" aria-hidden="true" />{RELATION_LABELS.configured_linkage}</span>
              {filteredGraph.edges.some((edge) => edge.relation_type === 'unknown') && (
                <span className="graph-legend-item"><i className="graph-line graph-line-unknown" aria-hidden="true" />{RELATION_LABELS.unknown}</span>
              )}
            </div>
            <div className="graph-legend-group" aria-label="风险颜色">
              <strong>节点填色</strong>
              <span className="graph-legend-item"><i className="graph-risk-swatch risk-normal" aria-hidden="true" />正常</span>
              <span className="graph-legend-item"><i className="graph-risk-swatch risk-low" aria-hidden="true" />低危</span>
              <span className="graph-legend-item"><i className="graph-risk-swatch risk-medium" aria-hidden="true" />中危</span>
              <span className="graph-legend-item"><i className="graph-risk-swatch risk-high" aria-hidden="true" />高危</span>
              <span className="graph-legend-item"><i className="graph-risk-swatch risk-unknown" aria-hidden="true" />未检测</span>
            </div>
          </div>
          {highlightLabel !== undefined && highlightLabel !== null && (
            <div className="graph-highlight-banner" role="status">
              <span>
                事件定位：{highlightLabel} · {visibleHighlightedCount}/{highlightedDeviceIds?.size ?? 0} 台关联设备在当前筛选中可见
                {highlightedDeviceIds?.size === 0 ? '；该事件没有关联设备，未执行节点定位。' : ''}
              </span>
              {onClearHighlight && <button type="button" onClick={onClearHighlight}>清除定位</button>}
            </div>
          )}
          <div className="graph-canvas" style={{ flex: 1, minHeight: 0 }} data-testid="graph-capability">
            <ReactEChartsCore
              echarts={echarts}
              option={buildGraphOption(filteredGraph, highlightedDeviceIds)}
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
        </>
      ) : (
        <div className="graph-empty" data-testid="graph-capability">
          <div className="graph-empty-mark" aria-hidden="true">◇</div>
          <strong>
            {capability.available ? '暂无设备节点' : 'GNN 图暂不可用'}
          </strong>
          {!capability.available && <p>{capabilityReason(capability)}</p>}
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
  selectedIncidentId,
  onSelectIncident,
  onViewIncident,
}: {
  events: MonitorRealtimeEvent[];
  incident: MonitorCapability;
  incidents?: MonitorIncidentData | null;
  selectedIncidentId: string | null;
  onSelectIncident: (incidentId: string) => void;
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
            </div>
          ) : summaries.map((item) => (
            <article className="monitor-incident-row" key={item.incident_id}>
              <button
                type="button"
                className="monitor-incident-focus"
                aria-pressed={selectedIncidentId === item.incident_id}
                aria-label={`${selectedIncidentId === item.incident_id ? '解除' : '在拓扑中定位'}事件 ${item.admin_title}`}
                onClick={() => onSelectIncident(item.incident_id)}
              >
                <span className={`monitor-incident-severity severity-${item.severity}`}>{item.severity}</span>
                <span><strong>{item.admin_title}</strong><small>{item.status} · {formatTime(item.updated_at)}</small></span>
                <span aria-hidden="true">{selectedIncidentId === item.incident_id ? '⌖' : '＋'}</span>
              </button>
              <button
                type="button"
                className="monitor-incident-view"
                onClick={() => onViewIncident(item.incident_id)}
                aria-label={`查看事件 ${item.admin_title}`}
              >
                处置详情
              </button>
            </article>
          ))}
        </div>
      )}
      <h3 className="incremental-heading">本次连接的状态增量</h3>
      <div className="recent-event-list">
        {events.length === 0 ? (
          <div className="panel-empty compact">
            <strong>本次连接尚未收到增量状态事件</strong>
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
            <div className="behavior-coverage-grid">
              <div>
                <strong>设备遥测覆盖</strong>
                <span>{device.received_at
                  ? `最近接收 ${formatTime(device.received_at)} · ${device.sources.length ? device.sources.join('、') : '来源未标注'}`
                  : '尚无后端接收的设备观测'}</span>
              </div>
              <div>
                <strong>GNN 能力状态</strong>
                <span>监视快照未提供运行或配置状态</span>
              </div>
            </div>
            {device.risk_level === null ? (
              <p className="behavior-note">{device.received_at
                ? '暂无该设备的 GNN 风险结论。已接收遥测不等于已完成风险检测；无风险结果不能解释为正常。当前响应也无法区分检测数据不足与检测能力未配置。'
                : '设备遥测覆盖不足：尚无后端接收的设备观测。当前响应未提供 GNN 运行/配置或模型输入覆盖状态，无法判断是否应有风险结论；缺少结论不等于正常。'}</p>
            ) : (
              <>
                <div className="behavior-evidence-grid">
                  <div><strong>触发指标</strong><span>设备图 GNN 分类</span></div>
                  <div><strong>观测值</strong><span>{device.risk_name ?? '未命名'} · 置信度 {device.risk_confidence !== null ? `${(device.risk_confidence * 100).toFixed(0)}%` : '未知'}</span></div>
                  <div><strong>参考基线</strong><span>尚未接入</span></div>
                </div>
                <p className="behavior-observation-time">检测时间：{formatTime(device.risk_detected_at)}</p>
              </>
            )}
            <div className="behavior-baseline-gap">
              <strong>通信基线尚未接入</strong>
              <p>当前检测只输出风险类别与置信度，尚未计算频率、协议/端口、连接对象的参考基线，因此不展示偏离结论。</p>
            </div>
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
