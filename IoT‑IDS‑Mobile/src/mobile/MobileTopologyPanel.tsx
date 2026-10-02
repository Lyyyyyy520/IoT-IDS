import React, { useEffect, useMemo, useRef, useState } from 'react';
import { ActivityIndicator, ScrollView, Text, TouchableOpacity, View, useWindowDimensions } from 'react-native';
import { useIsFocused, useNavigation } from '@react-navigation/native';
import { mobileApi, type MobileDevice, type MobileTopology, type MobileTopologyWindow } from './api';
import { messageFor, useMobile } from './MobileContext';
import { palette, ui } from './ui';

const ranges: Array<{ value: MobileTopologyWindow; label: string }> = [
  { value: '1h', label: '1 小时' }, { value: '24h', label: '24 小时' }, { value: '7d', label: '7 天' },
];
const status: Record<MobileDevice['connection_status'], { label: string; color: string }> = {
  online: { label: '在线', color: palette.green }, stale: { label: '延迟', color: palette.amber },
  offline: { label: '离线', color: palette.coral }, unknown: { label: '未知', color: palette.muted },
};
const severity: Record<string, string> = { critical: '紧急', high: '较高', medium: '一般', low: '较低', info: '提醒' };

export default function MobileTopologyPanel({ devices }: { devices: MobileDevice[] }) {
  const auth = useMobile();
  const navigation = useNavigation<any>();
  const focused = useIsFocused();
  const { width: screenWidth } = useWindowDimensions();
  const [range, setRange] = useState<MobileTopologyWindow>('24h');
  const [zoom, setZoom] = useState(1);
  const [topology, setTopology] = useState<MobileTopology | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [stale, setStale] = useState(false);
  const [reload, setReload] = useState(0);
  const currentTopology = useRef<MobileTopology | null>(null);

  useEffect(() => {
    if (!focused) return;
    let disposed = false;
    let inFlight: AbortController | null = null;
    let loading = false;
    const load = async () => {
      if (loading) return;
      loading = true;
      inFlight = new AbortController();
      setBusy(true);
      try {
        const value = await auth.requestAuthorized((server, token) => mobileApi.topology(server, token, range, inFlight!.signal));
        if (!disposed && !inFlight.signal.aborted) {
          currentTopology.current = value; setTopology(value); setError(null); setStale(false);
        }
      } catch (cause) {
        if (!disposed && !inFlight.signal.aborted) {
          setError(messageFor(cause)); setStale(currentTopology.current !== null);
        }
      } finally {
        if (!disposed && !inFlight.signal.aborted) setBusy(false);
        inFlight = null; loading = false;
      }
    };
    void load();
    const timer = setInterval(() => void load(), 30_000);
    return () => { disposed = true; clearInterval(timer); inFlight?.abort(); };
  }, [auth.requestAuthorized, focused, range, reload]);

  const allowed = useMemo(() => new Set(devices.map(device => device.device_id)), [devices]);
  const nodes = useMemo(() => (topology?.nodes ?? []).filter(item => allowed.has(item.device_id)), [allowed, topology]);
  const visibleIds = useMemo(() => new Set(nodes.map(node => node.device_id)), [nodes]);
  const edges = useMemo(() => (topology?.edges ?? []).filter(edge =>
    visibleIds.has(edge.source_device_id) && visibleIds.has(edge.target_device_id)), [topology, visibleIds]);
  const baseWidth = Math.max(244, screenWidth - 80);
  const columns = 2;
  const cellWidth = baseWidth / columns;
  const cellHeight = 116;
  const rowCount = Math.ceil(nodes.length / columns);
  const baseHeight = Math.max(80, rowCount * cellHeight + 24);
  const positions = new Map(nodes.map((node, index) => [node.device_id, {
    x: cellWidth * (index % columns) + cellWidth / 2,
    y: Math.floor(index / columns) * cellHeight + cellHeight / 2,
  }]));

  return <View style={ui.card}>
    <Text style={ui.cardTitle}>设备关系</Text>
    <Text style={ui.muted}>节点来自您获授权的设备；连线只表示所选时段内记录到的设备间发送关系。</Text>
    <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 8 }}>
      {ranges.map(item => <TouchableOpacity key={item.value} accessibilityRole="button"
        accessibilityState={{ selected: range === item.value }} style={ui.secondaryButton} onPress={() => setRange(item.value)}>
        <Text style={ui.secondaryText}>{item.label}</Text>
      </TouchableOpacity>)}
    </View>
    {error && <Text accessibilityRole="alert" style={ui.warning}>{stale ? '关系数据刷新失败，显示上次读取结果。' : '关系数据读取失败。'} {error}</Text>}
    {error && <TouchableOpacity accessibilityRole="button" style={ui.secondaryButton} onPress={() => setReload(value => value + 1)}>
      <Text style={ui.secondaryText}>重试读取关系</Text>
    </TouchableOpacity>}
    {!topology && busy && <ActivityIndicator accessibilityLabel="正在读取设备关系" color={palette.green} />}
    {!topology && !busy && <TouchableOpacity accessibilityRole="button" style={ui.secondaryButton} onPress={() => setReload(value => value + 1)}>
      <Text style={ui.secondaryText}>重试读取</Text>
    </TouchableOpacity>}
    {topology && <>
      <Text style={ui.muted}>设备类型：{[...new Set(nodes.map(node => node.device_type))].join('、') || '无'}。颜色图例：</Text>
      <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 10 }}>
        {(Object.entries(status) as Array<[MobileDevice['connection_status'], typeof status[MobileDevice['connection_status']]]>).map(([key, item]) =>
          <Text key={key} style={{ color: item.color, fontSize: 14 }}>● {item.label}</Text>)}
        <Text style={{ color: palette.coral, fontSize: 14 }}>● 高/紧急提醒</Text>
        <Text style={{ color: palette.amber, fontSize: 14 }}>● 其他级别提醒</Text>
      </View>
      {!topology.availability.available && <Text style={ui.warning}>服务端设备通信关系尚未可用；当前只显示授权设备节点。</Text>}
      {topology.availability.available && edges.length === 0 && <Text style={ui.muted}>所选时段内没有可显示的授权设备间通信关系。不会推断连线。</Text>}
      {!topology.alert_capability.available && <Text style={ui.muted}>提醒标记暂不可用，不能据此判断没有告警。</Text>}
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
        <Text style={ui.label}>缩放</Text>
        <TouchableOpacity accessibilityRole="button" accessibilityLabel="缩小关系图" style={ui.secondaryButton}
          onPress={() => setZoom(value => Math.max(0.75, Number((value - 0.15).toFixed(2))))}><Text style={ui.secondaryText}>−</Text></TouchableOpacity>
        <Text style={ui.body}>{Math.round(zoom * 100)}%</Text>
        <TouchableOpacity accessibilityRole="button" accessibilityLabel="放大关系图" style={ui.secondaryButton}
          onPress={() => setZoom(value => Math.min(1.6, Number((value + 0.15).toFixed(2))))}><Text style={ui.secondaryText}>＋</Text></TouchableOpacity>
      </View>
      {nodes.length === 0 ? <Text style={ui.muted}>{devices.length ? '当前筛选下没有节点。' : '管理员尚未授权设备。'}</Text> :
        <ScrollView horizontal nestedScrollEnabled showsHorizontalScrollIndicator accessibilityLabel="设备关系图，可横向平移">
          <View style={{ width: baseWidth * zoom, height: baseHeight * zoom }}>
            <View style={{ position: 'absolute', left: 0, top: 0, width: baseWidth, height: baseHeight,
              transform: [{ translateX: -baseWidth * (zoom - 1) / 2 }, { translateY: -baseHeight * (zoom - 1) / 2 }, { scale: zoom }] }}>
              {edges.map((edge, index) => {
                const from = positions.get(edge.source_device_id), to = positions.get(edge.target_device_id);
                if (!from || !to) return null;
                const dx = to.x - from.x, dy = to.y - from.y, length = Math.sqrt(dx * dx + dy * dy);
                const angle = Math.atan2(dy, dx);
                const arrowX = from.x + dx * 0.68, arrowY = from.y + dy * 0.68;
                return <React.Fragment key={`${edge.source_device_id}:${edge.target_device_id}`}>
                  <View accessibilityLabel={`${edge.source_device_id} 至 ${edge.target_device_id}，${edge.protocols.join('、') || '协议未记录'}，${edge.flows} 条流`}
                    accessible accessibilityRole="image" style={{ position: 'absolute', left: (from.x + to.x - length) / 2, top: (from.y + to.y) / 2 - 1,
                    width: length, height: 2, backgroundColor: palette.green, opacity: 0.55,
                    transform: [{ rotateZ: `${angle}rad` }], zIndex: 0 }} />
                  <View pointerEvents="none" style={{ position: 'absolute', left: arrowX - 4, top: arrowY - 5,
                    width: 0, height: 0, borderTopWidth: 5, borderBottomWidth: 5, borderLeftWidth: 8,
                    borderTopColor: 'transparent', borderBottomColor: 'transparent', borderLeftColor: palette.green,
                    transform: [{ rotateZ: `${angle}rad` }], zIndex: 1 }} />
                </React.Fragment>;
              })}
              {nodes.map((node, index) => {
                const marker = status[node.connection_status];
                const alerted = node.active_alert_count !== null && node.active_alert_count > 0;
                const alertColor = node.highest_alert_severity === 'critical' || node.highest_alert_severity === 'high'
                  ? palette.coral : palette.amber;
                return <TouchableOpacity key={node.device_id} accessibilityRole="button"
                  accessibilityLabel={`查看关系节点：${node.display_name}，${node.device_id}，${marker.label}${alerted ? '，有提醒' : ''}`}
                  onPress={() => navigation.navigate('设备详情', { deviceId: node.device_id })}
                  style={{ position: 'absolute', left: cellWidth * (index % columns) + 8,
                    top: Math.floor(index / columns) * cellHeight + 12, width: cellWidth - 16, minHeight: 88,
                    padding: 10, gap: 3, borderRadius: 12, borderWidth: 2, borderColor: marker.color,
                    backgroundColor: palette.card, zIndex: 1 }}>
                  <Text numberOfLines={2} style={{ color: palette.text, fontSize: 16, fontWeight: '700' }}>{node.display_name}</Text>
                  <Text numberOfLines={1} style={ui.muted}>{node.device_type} · {node.device_id}</Text>
                  <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 6 }}>
                    <Text style={{ color: marker.color, fontSize: 14 }}>● {marker.label}</Text>
                    {alerted && <Text style={{ color: alertColor, fontSize: 14 }}>▲ {severity[node.highest_alert_severity ?? 'info']}提醒</Text>}
                  </View>
                </TouchableOpacity>;
              })}
            </View>
          </View>
        </ScrollView>}
      <Text style={ui.muted}>关系来源：{topology.availability.source ?? '未配置'} · 查询 {new Date(topology.generated_at).toLocaleString()} · 可横向拖动查看，使用缩放按钮调整图面。</Text>
      {busy && <ActivityIndicator accessibilityLabel="正在刷新设备关系" color={palette.green} />}
    </>}
  </View>;
}
