import React, { useEffect, useRef, useState } from 'react';
import { ActivityIndicator, Alert, ScrollView, Text, TouchableOpacity, View } from 'react-native';
import { useIsFocused, useNavigation, useRoute } from '@react-navigation/native';
import { Ionicons } from '@expo/vector-icons';
import { useMobile, messageFor } from './MobileContext';
import { MobileApiError, mobileApi, type MobileDeviceDetail, type MobileDeviceHistory, type MobileDeviceTraffic,
  type MobileHistoryWindow, type MobileTrafficWindow } from './api';
import { nativeAppLifecycle, type AppLifecycleAdapter } from './appLifecycle';
import { design, palette, ui } from './ui';
import MobileSensorCharts from './MobileSensorCharts';

const WINDOWS: { value: MobileTrafficWindow; label: string }[] = [
  { value: '15m', label: '15 分钟' }, { value: '1h', label: '1 小时' }, { value: '24h', label: '24 小时' },
];
const HISTORY_WINDOWS: { value: MobileHistoryWindow; label: string }[] = [
  { value: '1h', label: '1 小时' }, { value: '24h', label: '24 小时' }, { value: '7d', label: '7 天' },
];

const bytes = (value: number): string => {
  if (value < 1024) return `${value.toFixed(0)} B`;
  const units = ['KB', 'MB', 'GB', 'TB'];
  let size = value / 1024, index = 0;
  while (size >= 1024 && index < units.length - 1) { size /= 1024; index += 1; }
  return `${size.toFixed(size < 10 ? 1 : 0)} ${units[index]}`;
};
const rate = (value: number): string => `${bytes(value)}/秒`;
const time = (value: string): string => new Date(value).toLocaleString();
const isForeground = (state: ReturnType<AppLifecycleAdapter['currentState']>): boolean => state !== 'background' && state !== 'inactive';
const connectionLabels = { online: '在线', stale: '延迟', offline: '离线', unknown: '未知' };
const connectionColors = {
  online: { bg: palette.greenSoft, fg: palette.green },
  stale: { bg: palette.amberSoft, fg: palette.amber },
  offline: { bg: palette.coralSoft, fg: palette.coral },
  unknown: { bg: '#F0EFEB', fg: palette.muted },
};
function deviceIcon(value: string): React.ComponentProps<typeof Ionicons>['name'] {
  const name = value.toLocaleLowerCase();
  if (/camera|摄像/.test(name)) return 'videocam-outline';
  if (/lock|门禁|门锁/.test(name)) return 'lock-closed-outline';
  if (/sensor|传感|温湿/.test(name)) return 'thermometer-outline';
  if (/gateway|router|网关|路由/.test(name)) return 'wifi-outline';
  return 'hardware-chip-outline';
}

function Trend({ traffic }: { traffic: MobileDeviceTraffic }) {
  const [chartWidth, setChartWidth] = useState(0);
  if (traffic.trend.length === 0) return <Text style={ui.muted}>当前范围没有可绘制的趋势点。</Text>;
  const points = traffic.trend;
  const times = points.map(point => Date.parse(point.bucket_start));
  const startTime = times[0];
  const endTime = times[times.length - 1];
  const timeSpan = Math.max(1, endTime - startTime);
  const gapIndexes = points.slice(1).flatMap((point, index) => {
    const previousTime = times[index];
    const currentTime = times[index + 1];
    const expectedStepMs = Math.max(1, traffic.trend_resolution_seconds) * 1000;
    return currentTime - previousTime > expectedStepMs * 1.5 ? [index + 1] : [];
  });
  const maximum = Math.max(1, ...points.flatMap(point => [point.uploaded_bytes, point.downloaded_bytes]));
  const chartHeight = 112;
  const plotWidth = Math.max(0, chartWidth - 24);
  const xAt = (index: number) => 12 + (points.length === 1 ? plotWidth / 2 : ((times[index] - startTime) / timeSpan) * plotWidth);
  const yAt = (value: number) => 8 + (1 - value / maximum) * 76;
  const series = [
    { key: 'upload', label: '上传', color: palette.green, values: points.map(point => point.uploaded_bytes) },
    { key: 'download', label: '下载', color: palette.amber, values: points.map(point => point.downloaded_bytes) },
  ];
  return <View style={{ gap: 5 }}>
    <View accessibilityLabel="设备流量趋势图" onLayout={event => setChartWidth(event.nativeEvent.layout.width)}
      style={{ height: chartHeight, position: 'relative', overflow: 'hidden' }}>
      {[0, 1, 2, 3].map(line => <View key={line} style={{ position: 'absolute', left: 0, right: 0,
        top: 9 + line * 25, borderTopWidth: 1, borderStyle: 'dashed', borderColor: '#EAE8E2' }} />)}
      {chartWidth > 0 && gapIndexes.map(index => {
        const x = (xAt(index - 1) + xAt(index)) / 2;
        return <View key={`gap-${index}`} accessible accessibilityLabel="此处存在数据缺口" style={{ position: 'absolute',
          left: x, top: 5, bottom: 5, borderLeftWidth: 2, borderStyle: 'dashed', borderColor: palette.muted, zIndex: 3 }} />;
      })}
      {chartWidth > 0 && series.map(item => <React.Fragment key={item.key}>
        {item.values.slice(1).map((value, index) => {
          if (gapIndexes.includes(index + 1)) return null;
          const x1 = xAt(index), y1 = yAt(item.values[index]);
          const x2 = xAt(index + 1), y2 = yAt(value);
          const length = Math.sqrt((x2 - x1) ** 2 + (y2 - y1) ** 2);
          const angle = Math.atan2(y2 - y1, x2 - x1);
          return <View key={`${item.key}-line-${index}`} style={{ position: 'absolute', left: (x1 + x2 - length) / 2,
            top: (y1 + y2) / 2, width: length, height: 2, borderRadius: 2, backgroundColor: item.color,
            transform: [{ rotate: `${angle}rad` }], zIndex: 1 }} />;
        })}
        {item.values.map((value, index) => <View key={`${item.key}-dot-${index}`} accessibilityLabel={`${item.label} ${bytes(value)}`}
          style={{ position: 'absolute', left: xAt(index) - 3, top: yAt(value) - 3, width: 7, height: 7,
            borderRadius: 4, backgroundColor: item.color, borderWidth: 1, borderColor: '#FFFFFF', zIndex: 2 }} />)}
      </React.Fragment>)}
    </View>
    <View style={{ flexDirection: 'row', justifyContent: 'space-between' }}>
      {points.filter((_, index) => index === 0 || index === points.length - 1 || (points.length > 4 && index === Math.floor(points.length / 2)))
        .map(point => <Text key={point.bucket_start} style={{ flex: 1, textAlign: 'center', color: palette.muted, fontSize: 14, lineHeight: 20 }}>
          {new Date(point.bucket_start).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
        </Text>)}
    </View>
    {gapIndexes.length > 0 && <Text style={ui.muted}>空白间隔表示没有对应数据样本，趋势线不会跨越缺口连接。</Text>}
    {points.length === 1 && <Text style={ui.muted}>单个样本：上传 {bytes(points[0].uploaded_bytes)}，下载 {bytes(points[0].downloaded_bytes)}。</Text>}
  </View>;
}

export default function MobileDeviceDetailScreen({ lifecycle = nativeAppLifecycle }: { lifecycle?: AppLifecycleAdapter } = {}) {
  const auth = useMobile();
  const navigation = useNavigation<any>();
  const route = useRoute<any>();
  const focused = useIsFocused();
  const deviceId = String(route.params?.deviceId ?? '');
  // React Native may report null before its first native AppState update; preserve the
  // foreground default in that initialization window, while explicit inactive/background pause work.
  const [foreground, setForeground] = useState(isForeground(lifecycle.currentState()));
  const [deviceData, setDevice] = useState<MobileDeviceDetail | null>(null);
  const [historyData, setHistory] = useState<MobileDeviceHistory | null>(null);
  const [trafficData, setTraffic] = useState<MobileDeviceTraffic | null>(null);
  const [window, setWindow] = useState<MobileTrafficWindow>('15m');
  const [historyWindow, setHistoryWindow] = useState<MobileHistoryWindow>('24h');
  const [deviceBusy, setDeviceBusy] = useState(false);
  const [historyBusy, setHistoryBusy] = useState(false);
  const [trafficBusy, setTrafficBusy] = useState(false);
  const [deviceStale, setDeviceStale] = useState(false);
  const [historyStale, setHistoryStale] = useState(false);
  const [trafficStale, setTrafficStale] = useState(false);
  const [deviceError, setDeviceError] = useState<string | null>(null);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const [trafficError, setTrafficError] = useState<string | null>(null);
  const detailAbort = useRef<AbortController | null>(null);
  const historyAbort = useRef<AbortController | null>(null);
  const trafficAbort = useRef<AbortController | null>(null);
  const scopeLossHandled = useRef(false);

  useEffect(() => {
    setForeground(isForeground(lifecycle.currentState()));
    return lifecycle.subscribe(state => setForeground(state === 'active'));
  }, [lifecycle]);

  useEffect(() => {
    setDevice(null); setHistory(null); setTraffic(null); setDeviceError(null); setHistoryError(null); setTrafficError(null);
    setDeviceStale(false); setHistoryStale(false); setTrafficStale(false); scopeLossHandled.current = false;
  }, [deviceId]);

  useEffect(() => {
    if (!focused || !foreground || !deviceId) return;
    let disposed = false;
    let busy = false;
    const load = async () => {
      if (busy) return;
      busy = true;
      const abort = new AbortController(); detailAbort.current = abort;
      setDeviceBusy(true);
      try {
        const value = await auth.requestAuthorized((server, token) => mobileApi.deviceDetail(server, token, deviceId, abort.signal));
        if (!disposed && !abort.signal.aborted && value.device_id === deviceId) {
          setDevice(value); setDeviceStale(false); setDeviceError(null);
        }
      } catch (error) {
        if (disposed || abort.signal.aborted) return;
        if (error instanceof MobileApiError && error.status === 404) {
          if (!scopeLossHandled.current) {
            scopeLossHandled.current = true; setDevice(null); setTraffic(null);
            void auth.sync();
            Alert.alert('授权范围已变化', '该设备已不在当前授权范围内，详情已清除。');
            navigation.goBack();
          }
          return;
        }
        setDeviceStale(true); setDeviceError(messageFor(error));
      } finally {
        if (!disposed && !abort.signal.aborted) setDeviceBusy(false);
        if (detailAbort.current === abort) detailAbort.current = null;
        busy = false;
      }
    };
    void load();
    const timer = setInterval(() => void load(), 30_000);
    return () => { disposed = true; clearInterval(timer); detailAbort.current?.abort(); };
  }, [auth.requestAuthorized, auth.sync, deviceId, focused, foreground, navigation]);

  useEffect(() => {
    if (!focused || !foreground || !deviceId) return;
    let disposed = false;
    let busy = false;
    const load = async () => {
      if (busy) return;
      busy = true;
      const abort = new AbortController(); historyAbort.current = abort;
      setHistoryBusy(true);
      try {
        const value = await auth.requestAuthorized((server, token) => mobileApi.deviceHistory(server, token, deviceId, historyWindow, abort.signal));
        if (!disposed && !abort.signal.aborted && value.device_id === deviceId && value.window === historyWindow) {
          setHistory(value); setHistoryStale(false); setHistoryError(null);
        }
      } catch (error) {
        if (disposed || abort.signal.aborted) return;
        if (error instanceof MobileApiError && error.status === 404) {
          if (!scopeLossHandled.current) {
            scopeLossHandled.current = true; setDevice(null); setHistory(null); setTraffic(null);
            void auth.sync();
            Alert.alert('授权范围已变化', '该设备已不在当前授权范围内，详情已清除。');
            navigation.goBack();
          }
          return;
        }
        setHistoryStale(true); setHistoryError(messageFor(error));
      } finally {
        if (!disposed && !abort.signal.aborted) setHistoryBusy(false);
        if (historyAbort.current === abort) historyAbort.current = null;
        busy = false;
      }
    };
    void load();
    const timer = setInterval(() => void load(), 30_000);
    return () => { disposed = true; clearInterval(timer); historyAbort.current?.abort(); };
  }, [auth.requestAuthorized, auth.sync, deviceId, focused, foreground, historyWindow, navigation]);

  useEffect(() => {
    if (!focused || !foreground || !deviceId) return;
    let disposed = false;
    let busy = false;
    const load = async () => {
      if (busy) return;
      busy = true;
      const abort = new AbortController(); trafficAbort.current = abort;
      setTrafficBusy(true);
      try {
        const value = await auth.requestAuthorized((server, token) => mobileApi.deviceTraffic(server, token, deviceId, window, abort.signal));
        if (!disposed && !abort.signal.aborted && value.device_id === deviceId && value.window === window) {
          setTraffic(value); setTrafficStale(false); setTrafficError(null);
        }
      } catch (error) {
        if (disposed || abort.signal.aborted) return;
        if (error instanceof MobileApiError && error.status === 404) {
          if (!scopeLossHandled.current) {
            scopeLossHandled.current = true; setDevice(null); setTraffic(null);
            void auth.sync();
            Alert.alert('授权范围已变化', '该设备已不在当前授权范围内，详情已清除。');
            navigation.goBack();
          }
          return;
        }
        setTrafficStale(true); setTrafficError(messageFor(error));
      } finally {
        if (!disposed && !abort.signal.aborted) setTrafficBusy(false);
        if (trafficAbort.current === abort) trafficAbort.current = null;
        busy = false;
      }
    };
    void load();
    const timer = setInterval(() => void load(), 8_000);
    return () => { disposed = true; clearInterval(timer); trafficAbort.current?.abort(); };
  }, [auth.requestAuthorized, auth.sync, deviceId, focused, foreground, navigation, window]);

  useEffect(() => () => { detailAbort.current?.abort(); historyAbort.current?.abort(); trafficAbort.current?.abort(); }, []);

  // Hide an old response during the render before device/window effects run.
  const device = deviceData?.device_id === deviceId ? deviceData : null;
  const history = historyData?.device_id === deviceId && historyData.window === historyWindow ? historyData : null;
  const traffic = trafficData?.device_id === deviceId && trafficData.window === window ? trafficData : null;
  const capability = device?.security_capability;
  const healthLabel = capability?.available === false ? '安全分析不可用' :
    (capability?.active_notice_count ?? 0) > 0 ? '需要关注' : device?.connection_status === 'online' ? '未发现活动提醒' : '检查状态';
  const healthColor = capability?.available === false ? palette.muted :
    (capability?.active_notice_count ?? 0) > 0 || device?.connection_status !== 'online' ? palette.amber : palette.green;
  useEffect(() => {
    navigation.setOptions({ title: device?.display_name ?? '设备详情' });
  }, [device?.display_name, navigation]);
  return <ScrollView style={ui.page} contentContainerStyle={design.pageContent}>
    {deviceError && <Text accessibilityRole="alert" style={ui.warning}>设备信息可能已过期：{deviceError}</Text>}
    {historyError && <Text accessibilityRole="alert" style={ui.warning}>{historyStale ? '历史数据刷新失败，显示上次读取结果。' : '历史数据读取失败。'} {historyError}</Text>}
    {trafficError && <Text accessibilityRole="alert" style={ui.warning}>流量信息可能已过期：{trafficError}</Text>}
    {deviceStale && device && <Text accessibilityRole="alert" style={ui.warning}>设备数据连接中断，显示的是本次打开后取得的数据。</Text>}
    {trafficStale && traffic && <Text accessibilityRole="alert" style={ui.warning}>流量刷新失败，以下为最近一次真实数据。</Text>}
    {!device && deviceBusy && <ActivityIndicator accessibilityLabel="正在读取设备详情" color={palette.green} />}
    {!device && !deviceBusy && <Text style={ui.muted}>{deviceError ?? (deviceId ? '正在读取授权设备详情…' : '设备编号无效。')}</Text>}
    {device && <>
      <View style={design.detailHero}>
        <View style={{ flex: 1, gap: 8 }}>
          <Text style={design.eyebrow}>{device.area_id ?? device.device_type}</Text>
          <Text style={design.heroTitle} numberOfLines={2}>{device.display_name}</Text>
          <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 7 }}>
            <View style={[design.statusPill, { backgroundColor: connectionColors[device.connection_status].bg }]}>
              <View style={{ width: 7, height: 7, borderRadius: 4, backgroundColor: connectionColors[device.connection_status].fg }} />
              <Text style={[design.statusText, { color: connectionColors[device.connection_status].fg }]}>
                {connectionLabels[device.connection_status]}
              </Text>
            </View>
            <View style={[design.statusPill, { backgroundColor: healthColor === palette.green ? palette.greenSoft : healthColor === palette.amber ? palette.amberSoft : '#F0EFEB' }]}>
              <Ionicons name={healthColor === palette.green ? 'checkmark-circle' : 'alert-circle'} size={13} color={healthColor} />
              <Text style={[design.statusText, { color: healthColor }]}>{healthLabel}</Text>
            </View>
          </View>
        </View>
        <View style={design.detailArt}>
          <Ionicons name={deviceIcon(`${device.display_name} ${device.device_type}`)} size={53} color="#4E5552" />
        </View>
      </View>

      {device.device_type === 'camera' && <View style={design.detailSection}>
        <Text style={design.detailSectionTitle}>摄像头画面</Text>
        <Text style={ui.warning}>未配置画面接口 · 当前无画面</Text>
        <Text style={ui.muted}>项目没有可供 APP 使用的视频流或快照 API，不显示占位画面。</Text>
        <Text style={ui.muted}>设备连接状态：{connectionLabels[device.connection_status]} · 最近收到遥测：{history?.latest ? time(history.latest.received_at) : '无留存记录'}</Text>
      </View>}

      <View style={design.detailSection}>
        <Text style={design.detailSectionTitle}>{device.device_type === 'sensor' ? '温湿度与历史趋势' : '设备上报与状态变化'}</Text>
        {!history && historyBusy && <ActivityIndicator accessibilityLabel="正在读取设备历史" color={palette.green} />}
        {!history && !historyBusy && <Text style={ui.muted}>{historyError ?? '尚无设备历史记录。'}</Text>}
        {history && <>
          {device.device_type === 'sensor' && <>
            <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 10 }}>
              {([{ key: 'temp', title: '温度', unit: '°C' }, { key: 'humidity', title: '湿度', unit: '%RH' }] as const).map(metric => {
                const value = history.latest?.telemetry[metric.key];
                const valid = typeof value === 'number' && Number.isFinite(value);
                return <View key={metric.key} style={{ flexGrow: 1, flexBasis: 130, minWidth: 130, padding: 14,
                  gap: 5, borderRadius: 16, borderWidth: 1, borderColor: palette.border }}>
                  <Text style={ui.label}>{metric.title}</Text>
                  <Text style={{ color: palette.text, fontSize: 34, lineHeight: 40, fontWeight: '800' }}>{valid ? Number(value).toFixed(1) : '—'}</Text>
                  <Text style={ui.label}>{metric.unit}</Text>
                  <Text style={ui.muted}>{valid && history.latest ? `观测 ${time(history.latest.observed_at)}\n接收 ${time(history.latest.received_at)}` : '没有该项有效读数'}</Text>
                </View>;
              })}
            </View>
            <Text style={ui.muted}>历史来自已留存设备观测；最近收到 {history.latest ? time(history.latest.received_at) : '无记录'}。</Text>
            <Text style={ui.label}>趋势时间范围</Text>
            <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 8 }}>
              {HISTORY_WINDOWS.map(item => {
                const active = historyWindow === item.value;
                return <TouchableOpacity key={item.value} accessibilityRole="button" accessibilityState={{ selected: active }}
                  style={[design.filterChip, active && design.filterChipActive]} onPress={() => setHistoryWindow(item.value)}>
                  <Text style={[design.filterText, active && design.filterTextActive]}>{item.label}</Text>
                </TouchableOpacity>;
              })}
            </View>
            {history.availability.available ? <MobileSensorCharts history={history} /> :
              <Text style={ui.muted}>{history.availability.reason === 'no_state_observations' ? '所选时段没有设备历史观测。' : '留存观测中没有可用的传感器数值。'}</Text>}
          </>}
          {device.device_type !== 'sensor' && history.latest && Object.entries(history.latest.telemetry).map(([key, value]) =>
            <Text key={key} style={ui.body}>{key}：{value === null ? '空值' : typeof value === 'boolean' ? (value ? '是' : '否') : String(value)}</Text>)}
          {history.series.slice(-5).reverse().map(point => <Text key={point.observation_id} style={design.noticeMeta}>
            {time(point.received_at)} · {Object.entries(point.telemetry).map(([key, value]) => `${key} ${String(value)}`).join(' · ') || '已收到状态记录'}
          </Text>)}
          {history.connection_history_available ? history.connection_status_changes.length > 0 ? history.connection_status_changes.slice(-5).reverse().map(change =>
            <Text key={change.event_id} style={ui.body}>{time(change.occurred_at)} · 连接 {change.from_status} → {change.to_status}（{change.source === 'timeout' ? '超时检测' : '上报刷新'}）</Text>) :
            <Text style={ui.muted}>所选时段没有留存的连接状态变化。</Text> :
            <Text style={ui.muted}>{history.connection_history_note}</Text>}
        </>}
      </View>

      <View style={design.detailSection}>
        <View style={design.sectionRow}>
          <Text style={design.detailSectionTitle}>当前流量</Text>
          <Text style={ui.muted}>(近 {WINDOWS.find(item => item.value === window)?.label ?? '15 分钟'})</Text>
        </View>
        <View style={{ flexDirection: 'row', gap: 7 }}>
          {WINDOWS.map(item => {
            const active = window === item.value;
            return <TouchableOpacity key={item.value} accessibilityRole="button" accessibilityState={{ selected: active }}
              style={[design.filterChip, active && design.filterChipActive, { paddingHorizontal: 12, paddingVertical: 7 }]}
              onPress={() => setWindow(item.value)}>
              <Text style={[design.filterText, active && design.filterTextActive]}>{item.label}</Text>
            </TouchableOpacity>;
          })}
        </View>
        {!device.traffic_capability.available && <Text style={ui.muted}>流量服务暂不可用。</Text>}
        {!traffic && trafficBusy && <ActivityIndicator accessibilityLabel="正在读取流量摘要" color={palette.green} />}
        {!traffic && !trafficBusy && <Text style={ui.muted}>{trafficError ?? '正在读取流量摘要…'}</Text>}
        {traffic && <>
          {traffic.is_historical && <Text style={ui.warning}>这是已退役设备的历史流量。</Text>}
          {traffic.availability.status === 'no_samples' ? <>
            <Text style={ui.body}>此时间范围尚未收到流量样本。</Text>
            {traffic.current_rate.status === 'warming_up' && <Text style={ui.muted}>实时窗口正在积累数据，暂不显示速率。</Text>}
          </> : <>
            <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 8 }}>
              <Text style={design.detailSectionTitle}>{traffic.current_rate.status === 'available' ? rate(traffic.current_rate.downloaded_bytes_per_second!) : '正在采集'}</Text>
              <View style={design.trafficLegend}>
                <View style={design.legendItem}><View style={{ width: 8, height: 8, borderRadius: 4, backgroundColor: palette.green }} /><Text style={design.legendText}>上传</Text></View>
                <View style={design.legendItem}><View style={{ width: 8, height: 8, borderRadius: 4, backgroundColor: palette.amber }} /><Text style={design.legendText}>下载</Text></View>
              </View>
            </View>
            {traffic.current_rate.status === 'warming_up' && <Text style={ui.muted}>正在积累数据，当前速率暂不可用。</Text>}
            {traffic.current_rate.status === 'available' && <Text style={ui.muted}>上传 {rate(traffic.current_rate.uploaded_bytes_per_second!)} · 下载 {rate(traffic.current_rate.downloaded_bytes_per_second!)}</Text>}
            <Trend traffic={traffic} />
          </>}
          {traffic.summary && <Text style={ui.body}>所选时段：上传 {bytes(traffic.summary.uploaded_bytes)} / 下载 {bytes(traffic.summary.downloaded_bytes)}</Text>}
          {traffic.protocols.length > 0 && <View style={{ gap: 8 }}>
            <Text style={ui.label}>协议流量</Text>
            {traffic.protocols.map(item => <View key={item.category} style={{ gap: 4 }}>
              <Text style={design.noticeMeta}>{item.label} · {item.share_percent}%</Text>
              <View accessibilityLabel={`${item.label} 占 ${item.share_percent}%`} style={{ height: 6, borderRadius: 3, backgroundColor: palette.border }}>
                <View style={{ width: `${item.share_percent}%`, height: 6, borderRadius: 3, backgroundColor: palette.green }} />
              </View>
            </View>)}
          </View>}
          <Text style={design.noticeMeta}>{traffic.freshness.latest_sample_at ? `最近样本：${time(traffic.freshness.latest_sample_at)}` : traffic.data_quality.message}</Text>
        </>}
      </View>

      <View style={design.detailSection}>
        <Text style={design.detailSectionTitle}>最近活动</Text>
        <View style={design.detailRow}>
          <View style={[design.detailRowIcon, { backgroundColor: '#E8F3EC' }]}><Ionicons name="pulse-outline" size={19} color={palette.green} /></View>
          <View style={design.detailRowText}><Text style={design.detailRowTitle}>最近连接</Text>
            <Text style={design.detailRowSubtitle}>{device.last_seen_at ? time(device.last_seen_at) : '尚无状态记录'}</Text></View>
          <Ionicons name="chevron-forward" size={18} color="#8D9390" />
        </View>
        <View style={design.detailRow}>
          <View style={[design.detailRowIcon, { backgroundColor: '#F0EFEB' }]}><Ionicons name="settings-outline" size={19} color="#606966" /></View>
          <View style={design.detailRowText}><Text style={design.detailRowTitle}>设备运行状态</Text>
            <Text style={design.detailRowSubtitle}>连接状态：{device.status_text.connection}</Text>
            <Text style={design.detailRowSubtitle}>运行模式：{device.status_text.operation}</Text></View>
          <Ionicons name="chevron-forward" size={18} color="#8D9390" />
        </View>
        <TouchableOpacity accessibilityRole="button" style={[design.detailRow, { borderBottomWidth: 0 }]}
          onPress={() => navigation.navigate('主界面', { screen: '安全提醒' })}>
          <View style={[design.detailRowIcon, { backgroundColor: palette.coralSoft }]}><Ionicons name="warning" size={19} color={palette.coral} /></View>
          <View style={design.detailRowText}><Text style={design.detailRowTitle}>安全提醒</Text>
            <Text style={design.detailRowSubtitle}>{capability?.active_notice_count ?? 0} 条活动提醒</Text></View>
          <Ionicons name="chevron-forward" size={18} color="#8D9390" />
        </TouchableOpacity>
      </View>

      <View style={design.detailSection}>
        <View style={design.sectionRow}>
          <Text style={design.detailSectionTitle}>相关提醒</Text>
          {capability?.active_notice_count !== null && capability?.active_notice_count !== undefined &&
            <Text style={design.noticeMeta}>{capability.active_notice_count} 条</Text>}
        </View>
        {!capability?.available ? <Text style={ui.muted}>安全提醒功能暂不可用，不能据此判断设备是否安全。</Text> :
          capability.active_notice_count === 0 ? <Text style={ui.muted}>当前没有该设备已记录的活动提醒。</Text> :
            capability.recent_notices.map(notice => <TouchableOpacity key={notice.incident_id} accessibilityRole="button"
              style={design.detailRow} onPress={() => navigation.navigate('提醒详情', { incidentId: notice.incident_id })}>
              <View style={[design.detailRowIcon, { backgroundColor: palette.coralSoft }]}><Ionicons name="alert-circle" size={19} color={palette.coral} /></View>
              <View style={design.detailRowText}><Text style={design.detailRowTitle}>{notice.user_title}</Text>
                <Text style={design.detailRowSubtitle}>{notice.severity} · {notice.read ? '已读' : '未读'}</Text></View>
              <Ionicons name="chevron-forward" size={18} color="#8D9390" />
            </TouchableOpacity>)}
        {capability?.available && capability.active_notice_count !== null && capability.active_notice_count > capability.recent_notices.length &&
          <Text style={ui.muted}>另有 {capability.active_notice_count - capability.recent_notices.length} 条活动提醒。</Text>}
        <Text style={ui.muted}>没有已记录提醒不代表设备安全。</Text>
      </View>

      <TouchableOpacity accessibilityRole="button" style={design.ctaPurple}
        onPress={() => navigation.navigate('提交求助', { deviceId: device.device_id })}>
        <Text style={design.ctaPurpleText}>联系管理员 · 查看处理进度</Text>
      </TouchableOpacity>
      <Text style={[design.noticeMeta, { textAlign: 'center' }]}>详细分析由管理员在 Web 端处理</Text>
    </>}
  </ScrollView>;
}
