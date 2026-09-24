import React, { useEffect, useRef, useState } from 'react';
import { ActivityIndicator, Alert, ScrollView, Text, TouchableOpacity, View } from 'react-native';
import { useIsFocused, useNavigation, useRoute } from '@react-navigation/native';
import { useMobile, messageFor } from './MobileContext';
import { MobileApiError, mobileApi, type MobileDeviceDetail, type MobileDeviceTraffic, type MobileTrafficWindow } from './api';
import { nativeAppLifecycle, type AppLifecycleAdapter } from './appLifecycle';
import { palette, ui } from './ui';

const WINDOWS: { value: MobileTrafficWindow; label: string }[] = [
  { value: '15m', label: '15 分钟' }, { value: '1h', label: '1 小时' }, { value: '24h', label: '24 小时' },
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

function Trend({ traffic }: { traffic: MobileDeviceTraffic }) {
  if (traffic.trend.length === 0) return <Text style={ui.muted}>当前范围没有可绘制的趋势点。</Text>;
  const points = traffic.trend;
  const maximum = Math.max(1, ...points.flatMap(point => [point.uploaded_bytes, point.downloaded_bytes]));
  const expected = traffic.trend_resolution_seconds;
  let previous = 0;
  return <View style={{ gap: 10 }}>
    <ScrollView horizontal accessibilityLabel="流量趋势图，支持横向滚动" contentContainerStyle={{ alignItems: 'flex-end', minHeight: 112, paddingVertical: 8 }}>
      {points.map((point, index) => {
        const epoch = Date.parse(point.bucket_start) / 1000;
        const gapBuckets = index > 0 ? Math.max(0, Math.round((epoch - previous) / expected) - 1) : 0;
        previous = epoch;
        const spacerWidth = Math.min(gapBuckets, 4) * 36;
        const upHeight = point.uploaded_bytes === 0 ? 0 : Math.max(2, point.uploaded_bytes / maximum * 62);
        const downHeight = point.downloaded_bytes === 0 ? 0 : Math.max(2, point.downloaded_bytes / maximum * 62);
        return <React.Fragment key={point.bucket_start}>
          {spacerWidth > 0 && <View accessibilityLabel="此处存在数据缺口" style={{ width: spacerWidth, height: 72, borderBottomWidth: 1, borderStyle: 'dashed', borderColor: palette.border }} />}
          <View accessibilityLabel={`${time(point.bucket_start)}，上传 ${bytes(point.uploaded_bytes)}，下载 ${bytes(point.downloaded_bytes)}`}
            style={{ width: 36, minHeight: 84, alignItems: 'center', justifyContent: 'flex-end', marginHorizontal: 2 }}>
            <View style={{ height: 66, flexDirection: 'row', alignItems: 'flex-end', gap: 3 }}>
              <View style={{ width: 10, height: upHeight, backgroundColor: '#7B806F', borderRadius: 4 }} />
              <View style={{ width: 10, height: downHeight, backgroundColor: '#B0A792', borderRadius: 4 }} />
            </View>
            <Text style={{ color: palette.muted, fontSize: 10 }} numberOfLines={1}>{new Date(point.bucket_start).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</Text>
          </View>
        </React.Fragment>;
      })}
    </ScrollView>
    <Text style={ui.muted}>每条柱代表实际聚合样本；空白间隔表示没有对应数据，不按零流量补齐。</Text>
    {points.length === 1 && <Text style={ui.body}>单个样本：上传 {bytes(points[0].uploaded_bytes)}，下载 {bytes(points[0].downloaded_bytes)}。</Text>}
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
  const [trafficData, setTraffic] = useState<MobileDeviceTraffic | null>(null);
  const [window, setWindow] = useState<MobileTrafficWindow>('15m');
  const [deviceBusy, setDeviceBusy] = useState(false);
  const [trafficBusy, setTrafficBusy] = useState(false);
  const [deviceStale, setDeviceStale] = useState(false);
  const [trafficStale, setTrafficStale] = useState(false);
  const [deviceError, setDeviceError] = useState<string | null>(null);
  const [trafficError, setTrafficError] = useState<string | null>(null);
  const detailAbort = useRef<AbortController | null>(null);
  const trafficAbort = useRef<AbortController | null>(null);
  const scopeLossHandled = useRef(false);

  useEffect(() => {
    setForeground(isForeground(lifecycle.currentState()));
    return lifecycle.subscribe(state => setForeground(state === 'active'));
  }, [lifecycle]);

  useEffect(() => {
    setDevice(null); setTraffic(null); setDeviceError(null); setTrafficError(null);
    setDeviceStale(false); setTrafficStale(false); scopeLossHandled.current = false;
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

  useEffect(() => () => { detailAbort.current?.abort(); trafficAbort.current?.abort(); }, []);

  // Hide an old response during the render before device/window effects run.
  const device = deviceData?.device_id === deviceId ? deviceData : null;
  const traffic = trafficData?.device_id === deviceId && trafficData.window === window ? trafficData : null;
  const capability = device?.security_capability;
  return <ScrollView style={ui.page} contentContainerStyle={ui.content}>
    {deviceError && <Text accessibilityRole="alert" style={ui.warning}>设备信息可能已过期：{deviceError}</Text>}
    {trafficError && <Text accessibilityRole="alert" style={ui.warning}>流量信息可能已过期：{trafficError}</Text>}
    {deviceStale && device && <Text accessibilityRole="alert" style={ui.warning}>设备数据连接中断，显示的是本次打开后取得的数据。</Text>}
    {trafficStale && traffic && <Text accessibilityRole="alert" style={ui.warning}>流量刷新失败，以下为最近一次真实数据。</Text>}
    {!device && deviceBusy && <ActivityIndicator accessibilityLabel="正在读取设备详情" color={palette.green} />}
    {!device && !deviceBusy && <Text style={ui.muted}>{deviceError ?? (deviceId ? '正在读取授权设备详情…' : '设备编号无效。')}</Text>}
    {device && <>
      <Text style={ui.title}>{device.display_name}</Text>
      <Text style={ui.subtitle}>{device.device_type} · {device.area_id ?? '未分配区域'}</Text>
      <View style={ui.card}>
        <Text style={ui.cardTitle}>设备状态</Text>
        <Text style={ui.body}>连接状态：{device.status_text.connection}</Text>
        <Text style={ui.body}>运行模式：{device.status_text.operation}</Text>
        {device.retired && <Text style={ui.warning}>设备已退役；以下流量仅供查看历史数据。</Text>}
        <Text style={ui.muted}>最近更新时间：{device.last_updated_at ? time(device.last_updated_at) : '未知'}</Text>
        <Text style={ui.muted}>最近收到状态：{device.last_seen_at ? time(device.last_seen_at) : '尚无状态记录'}</Text>
        <Text style={ui.muted}>{device.availability_text}</Text>
        <TouchableOpacity accessibilityRole="button" style={ui.secondaryButton}
          onPress={() => navigation.navigate('提交求助', { deviceId: device.device_id })}>
          <Text style={ui.secondaryText}>就此设备联系管理员</Text>
        </TouchableOpacity>
      </View>
      <View style={ui.card}>
        <Text style={ui.cardTitle}>相关安全提醒</Text>
        {!capability?.available ? <Text style={ui.muted}>安全提醒功能暂不可用，不能据此判断设备是否安全。</Text> :
          capability.active_notice_count === 0 ? <Text style={ui.muted}>当前没有该设备已记录的活动提醒。</Text> :
            capability.recent_notices.map(notice => <TouchableOpacity key={notice.incident_id} accessibilityRole="button"
              style={ui.secondaryButton} onPress={() => navigation.navigate('提醒详情', { incidentId: notice.incident_id })}>
              <Text style={ui.secondaryText}>{notice.user_title} · {notice.severity} · {notice.read ? '已读' : '未读'} · {notice.acknowledged ? '我已知晓' : '尚未标记已知晓'}</Text>
            </TouchableOpacity>)}
        {capability?.available && capability.active_notice_count !== null && capability.active_notice_count > capability.recent_notices.length &&
          <Text style={ui.muted}>另有 {capability.active_notice_count - capability.recent_notices.length} 条活动提醒。</Text>}
        <Text style={ui.muted}>没有已记录提醒不代表设备安全。</Text>
      </View>
    </>}

    <View style={ui.card}>
      <Text style={ui.cardTitle}>简化流量</Text>
      {device && !device.traffic_capability.available && <Text style={ui.muted}>流量服务暂不可用。</Text>}
      <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 8 }}>
        {WINDOWS.map(item => <TouchableOpacity key={item.value} accessibilityRole="button" accessibilityState={{ selected: window === item.value }}
          style={ui.secondaryButton} onPress={() => setWindow(item.value)}><Text style={ui.secondaryText}>{item.label}</Text></TouchableOpacity>)}
      </View>
      {!traffic && trafficBusy && <ActivityIndicator accessibilityLabel="正在读取流量摘要" color={palette.green} />}
      {!traffic && !trafficBusy && <Text style={ui.muted}>{trafficError ?? '正在读取流量摘要…'}</Text>}
      {traffic && <>
        {traffic.is_historical && <Text style={ui.warning}>这是已退役设备的历史流量。</Text>}
        <Text style={ui.muted}>数据新鲜度：{traffic.freshness.status === 'fresh' ? '较新' : traffic.freshness.status === 'stale' ? '最近样本已过期' : '尚无历史样本'}</Text>
        <Text style={ui.muted}>最近样本：{traffic.freshness.latest_sample_at ? time(traffic.freshness.latest_sample_at) : '未知'}</Text>
        {traffic.availability.status === 'no_samples' ? <>
          <Text style={ui.body}>此时间范围尚未收到流量样本。</Text>
          {traffic.current_rate.status === 'warming_up' && <Text style={ui.muted}>实时窗口正在积累数据，暂不显示速率。</Text>}
        </> : <>
          <Text style={ui.label}>当前速率 · {traffic.current_rate.label}</Text>
          {traffic.current_rate.status === 'warming_up' ? <Text style={ui.muted}>正在积累数据，当前速率暂不可用。</Text> : <>
            <Text style={ui.body}>上传 {rate(traffic.current_rate.uploaded_bytes_per_second!)} · 下载 {rate(traffic.current_rate.downloaded_bytes_per_second!)}</Text>
            <Text style={ui.muted}>上传 {traffic.current_rate.uploaded_packets_per_second!.toFixed(2)} 包/秒 · 下载 {traffic.current_rate.downloaded_packets_per_second!.toFixed(2)} 包/秒</Text>
          </>}
          {traffic.summary && <Text style={ui.body}>所选时段：上传 {bytes(traffic.summary.uploaded_bytes)} / {traffic.summary.uploaded_packets} 包；下载 {bytes(traffic.summary.downloaded_bytes)} / {traffic.summary.downloaded_packets} 包</Text>}
        </>}
        <Text style={ui.label}>实际聚合趋势</Text>
        <Trend traffic={traffic} />
        <Text style={ui.label}>协议大类</Text>
        {traffic.protocols.length === 0 ? <Text style={ui.muted}>此时间范围没有协议样本。</Text> : traffic.protocols.map(item => <View key={item.category} style={{ gap: 4 }}>
          <Text style={ui.body}>{item.label} · {item.share_percent}% · {bytes(item.bytes)}</Text>
          <View accessibilityLabel={`${item.label} 占 ${item.share_percent}%`} style={{ height: 8, borderRadius: 4, backgroundColor: palette.border }}>
            <View style={{ width: `${item.share_percent}%`, height: 8, borderRadius: 4, backgroundColor: '#7B806F' }} />
          </View>
        </View>)}
        <Text style={ui.muted}>{traffic.data_quality.message}</Text>
        <Text style={ui.muted}>最近查询：{time(traffic.generated_at)}</Text>
      </>}
    </View>
  </ScrollView>;
}
