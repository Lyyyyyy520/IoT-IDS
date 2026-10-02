import React, { useEffect, useMemo, useRef, useState } from 'react';
import { ActivityIndicator, RefreshControl, ScrollView, Text, TextInput, TouchableOpacity, View } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { useIsFocused, useNavigation, useRoute } from '@react-navigation/native';
import { mobileApi, MobileApiError, type MobileNotice, type MobileNoticeReplay, type NoticeStatus } from './api';
import { messageFor, useMobile } from './MobileContext';
import SupportContactCard from './SupportContactCard';
import { design, palette, ui } from './ui';

type NoticeFilter = 'all' | 'unread' | 'unacknowledged' | 'processing' | 'open' | 'acknowledged' | 'recovering' | 'resolved' | 'false_positive';
type DateFilter = 'all' | '24h' | '7d' | '30d';
type SeverityFilter = 'all' | MobileNotice['severity'];
const filterLabels: Record<NoticeFilter, string> = { all: '全部', unread: '未读', unacknowledged: '未知晓', processing: '处理中', open: '等待处理', acknowledged: '管理员已确认', recovering: '正在恢复', resolved: '已解决', false_positive: '误报' };
const dateLabels: Record<DateFilter, string> = { all: '所有时间', '24h': '24 小时', '7d': '7 天', '30d': '30 天' };
const severityLabels: Record<MobileNotice['severity'], string> = { info: '提醒', low: '较低', medium: '一般', high: '较高', critical: '紧急' };
const severityIcons: Record<MobileNotice['severity'], string> = { info: 'ℹ', low: '○', medium: '!', high: '⚠', critical: '‼' };
const statusLabels: Record<NoticeStatus, string> = {
  open: '等待管理员处理', acknowledged: '管理员正在核查', recovering: '处理中，正在恢复',
  resolved: '已完成处理', false_positive: '提醒已结束',
};
const processing = (status: NoticeStatus) => status === 'open' || status === 'acknowledged' || status === 'recovering';
const REPLAY_RANGES = [{ value: '1h', label: '1 小时' }, { value: '24h', label: '24 小时' }, { value: '7d', label: '7 天' }] as const;
type ReplayRange = typeof REPLAY_RANGES[number]['value'];

function Severity({ notice }: { notice: MobileNotice }) {
  return <Text style={[ui.label, { color: notice.severity === 'critical' || notice.severity === 'high' ? palette.coral : palette.amber }]}>
    {severityIcons[notice.severity]} {severityLabels[notice.severity]}
  </Text>;
}

export default function NoticeListScreen() {
  const auth = useMobile();
  const navigation = useNavigation<any>();
  const [filter, setFilter] = useState<NoticeFilter>('all');
  const [severityFilter, setSeverityFilter] = useState<SeverityFilter>('all');
  const [dateFilter, setDateFilter] = useState<DateFilter>('all');
  const [search, setSearch] = useState('');
  const [ackBusy, setAckBusy] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const focused = useIsFocused();
  const syncRef = useRef(auth.syncNotices);
  syncRef.current = auth.syncNotices;
  useEffect(() => { if (focused) void syncRef.current(false); }, [focused]);
  const notices = useMemo(() => (auth.notices ?? []).filter(item => {
    if (filter === 'unread') return !item.read;
    if (filter === 'unacknowledged') return !item.acknowledged;
    if (filter === 'processing') return processing(item.status);
    if (filter === 'resolved' || filter === 'false_positive' || filter === 'open' || filter === 'acknowledged' || filter === 'recovering') return item.status === filter;
    return true;
  }).filter(item => {
    if (severityFilter !== 'all' && item.severity !== severityFilter) return false;
    if (dateFilter !== 'all' && Date.parse(item.first_seen_at) < Date.now() - ({ '24h': 86_400_000, '7d': 604_800_000, '30d': 2_592_000_000 }[dateFilter])) return false;
    if (search) {
      const content = `${item.user_title} ${item.user_summary} ${item.affected_devices.map(device => `${device.display_name} ${device.device_id}`).join(' ')}`;
      if (!content.toLocaleLowerCase().includes(search.toLocaleLowerCase())) return false;
    }
    return true;
  }), [auth.notices, filter, severityFilter, dateFilter, search]);
  const capability = auth.overview?.security_capability;
  const acknowledge = async (incidentId: string) => {
    if (ackBusy) return;
    setAckBusy(incidentId); setActionError(null);
    try {
      const value = await auth.requestAuthorized((server, token) => mobileApi.acknowledgeNotice(server, token, incidentId));
      auth.applyNotice(value);
    } catch (error) { setActionError(messageFor(error)); }
    finally { setAckBusy(null); }
  };
  const openNotice = (incidentId: string) => navigation.navigate('提醒详情', { incidentId });
  return <ScrollView style={ui.page} contentContainerStyle={design.pageContent}
    refreshControl={<RefreshControl refreshing={auth.noticesBusy} onRefresh={() => void auth.syncNotices(true)} />}>
    <Text style={ui.subtitle}>这里只显示管理员发布给您的安全提醒和公开处理进度。</Text>
    {auth.noticesStale && <Text accessibilityRole="alert" style={ui.warning}>提醒数据可能已过期。{auth.noticesError ?? ''}</Text>}
    {auth.lastNoticesSynced && <Text style={ui.muted}>最近同步：{new Date(auth.lastNoticesSynced).toLocaleString()}</Text>}
    {actionError && <Text accessibilityRole="alert" style={ui.warning}>{actionError}</Text>}
    {capability?.available === false ? <View style={ui.card}>
      <Text style={ui.cardTitle}>提醒功能暂不可用</Text><Text style={ui.muted}>安全事件功能尚未接入或暂时不可用。</Text>
    </View> : auth.notices === null ? <View style={ui.card}>
      {auth.noticesBusy && <ActivityIndicator color={palette.green} />}
      <Text accessibilityRole="alert" style={auth.noticesError ? ui.warning : ui.muted}>{auth.noticesError ?? '正在获取提醒…'}</Text>
      {auth.noticesError && <TouchableOpacity accessibilityRole="button" style={ui.secondaryButton} onPress={() => void auth.syncNotices(true)}><Text style={ui.secondaryText}>重试</Text></TouchableOpacity>}
    </View> : <>
      <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={design.filterList}>
        {(Object.keys(filterLabels) as NoticeFilter[]).map(key => {
          const allNotices = auth.notices ?? [];
          const count = key === 'all' ? allNotices.length : allNotices.filter(item => {
            if (key === 'unread') return !item.read;
            if (key === 'unacknowledged') return !item.acknowledged;
            if (key === 'processing') return processing(item.status);
            if (key === 'resolved') return item.status === 'resolved' || item.status === 'false_positive';
            return item.status === key;
          }).length;
          const active = filter === key;
          return <TouchableOpacity key={key} accessibilityRole="button" accessibilityState={{ selected: active }}
            style={[design.filterChip, active && design.filterChipActive]} onPress={() => setFilter(key)}>
            <Text style={[design.filterText, active && design.filterTextActive]}>{filterLabels[key]}</Text>
            <Text style={[design.filterText, { color: active ? palette.green : palette.muted }]}>{count}</Text>
          </TouchableOpacity>;
        })}
      </ScrollView>
      <TextInput accessibilityLabel="搜索提醒设备或内容" style={ui.input} placeholder="搜索设备名称、ID 或提醒内容"
        value={search} onChangeText={setSearch} />
      <Text style={ui.label}>严重程度</Text>
      <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={design.filterList}>
        {(['all', 'critical', 'high', 'medium', 'low', 'info'] as SeverityFilter[]).map(key => {
          const active = severityFilter === key;
          return <TouchableOpacity key={key} accessibilityRole="button" accessibilityState={{ selected: active }}
            style={[design.filterChip, active && design.filterChipActive]} onPress={() => setSeverityFilter(key)}>
            <Text style={[design.filterText, active && design.filterTextActive]}>{key === 'all' ? '全部级别' : severityLabels[key]}</Text>
          </TouchableOpacity>;
        })}
      </ScrollView>
      <Text style={ui.label}>发生时间</Text>
      <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={design.filterList}>
        {(Object.keys(dateLabels) as DateFilter[]).map(key => {
          const active = dateFilter === key;
          return <TouchableOpacity key={key} accessibilityRole="button" accessibilityState={{ selected: active }}
            style={[design.filterChip, active && design.filterChipActive]} onPress={() => setDateFilter(key)}>
            <Text style={[design.filterText, active && design.filterTextActive]}>{dateLabels[key]}</Text>
          </TouchableOpacity>;
        })}
      </ScrollView>
      {notices.length === 0 ? <View style={[design.detailSection, design.emptyState]}>
        <View style={[design.summaryIcon, { backgroundColor: palette.greenSoft }]}>
          <Ionicons name="shield-checkmark" size={25} color={palette.green} />
        </View>
        <Text style={ui.cardTitle}>当前没有已记录提醒</Text>
        <Text style={ui.muted}>有新的提醒时会在这里显示处理进度。</Text>
      </View> : notices.map(notice => {
        const urgent = notice.severity === 'critical' || notice.severity === 'high';
        const finished = notice.status === 'resolved' || notice.status === 'false_positive';
        return <View key={notice.incident_id} style={[design.noticeCard, urgent && !finished && design.noticeCritical]}>
          <TouchableOpacity accessibilityRole="button" accessibilityLabel={`查看提醒：${notice.user_title}`}
            onPress={() => openNotice(notice.incident_id)} style={design.noticeHeader}>
            <View style={[design.noticeIcon, { backgroundColor: finished ? palette.greenSoft : urgent ? '#F8D9D9' : palette.amberSoft }]}>
              <Ionicons name={finished ? 'checkmark-circle' : urgent ? 'warning' : 'notifications'}
                size={25} color={finished ? palette.green : urgent ? palette.coral : palette.amber} />
            </View>
            <View style={design.noticeCopy}>
              <Text style={design.noticeTitle}>{notice.user_title}</Text>
              <Text style={design.noticeDate}>{new Date(notice.updated_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</Text>
            </View>
            <Ionicons name="chevron-forward" size={19} color="#8D9390" />
          </TouchableOpacity>
          <Text style={design.noticeSummary} numberOfLines={3}>{notice.user_summary}</Text>
          <Text style={design.noticeMeta}>{statusLabels[notice.status]} · {notice.affected_devices.map(device => device.display_name).join('、') || '未指定设备'}</Text>
          <Text style={design.noticeMeta}>管理员进度：{notice.public_progress}</Text>
          <View style={design.noticeActions}>
            {!notice.acknowledged && !finished && <TouchableOpacity accessibilityRole="button" disabled={ackBusy !== null}
              style={design.noticeAction} onPress={() => void acknowledge(notice.incident_id)}>
              <Text style={design.noticeActionText}>{ackBusy === notice.incident_id ? '提交中…' : '我已知晓'}</Text>
            </TouchableOpacity>}
            <TouchableOpacity accessibilityRole="button" style={[design.noticeAction, design.noticeActionPrimary]}
              onPress={() => navigation.navigate('提交求助', { incidentId: notice.incident_id })}>
              <Text style={design.noticeActionTextLight}>联系管理员</Text>
            </TouchableOpacity>
          </View>
        </View>;
      })}
    </>}
    <SupportContactCard />
  </ScrollView>;
}

export function NoticeDetailScreen() {
  const auth = useMobile();
  const navigation = useNavigation<any>();
  const route = useRoute<any>();
  const incidentId = String(route.params?.incidentId ?? '');
  const [notice, setNotice] = useState<MobileNotice | null>(null);
  const [busy, setBusy] = useState(true);
  const [ackBusy, setAckBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [requestId, setRequestId] = useState<string | null>(null);
  const [replayRange, setReplayRange] = useState<ReplayRange>('24h');
  const [replay, setReplay] = useState<MobileNoticeReplay | null>(null);
  const [replayBusy, setReplayBusy] = useState(false);
  const [replayError, setReplayError] = useState<string | null>(null);

  const loadAndRead = async (signal: AbortSignal, onDetail: (value: MobileNotice) => void): Promise<MobileNotice> => {
    const detail = await auth.requestAuthorized((server, token) => mobileApi.notice(server, token, incidentId, signal));
    if (signal.aborted) return detail;
    onDetail(detail);
    auth.applyNotice(detail);
    const read = await auth.requestAuthorized((server, token) => mobileApi.markNoticeRead(server, token, incidentId, signal));
    if (!signal.aborted) auth.applyNotice(read);
    return read;
  };

  useEffect(() => {
    const abort = new AbortController(); let mounted = true;
    setBusy(true); setError(null);
    (async () => {
      try {
        const read = await loadAndRead(abort.signal, setNotice);
        if (!mounted || abort.signal.aborted) return;
        setNotice(read);
      } catch (cause) {
        if (!mounted || abort.signal.aborted) return;
        setError(messageFor(cause));
        setRequestId(cause instanceof MobileApiError ? cause.requestId ?? null : null);
        if (cause instanceof MobileApiError && [403, 404].includes(cause.status)) {
          void auth.syncNotices(true);
          navigation.goBack();
        }
      } finally { if (mounted && !abort.signal.aborted) setBusy(false); }
    })();
    return () => { mounted = false; abort.abort(); };
  }, [incidentId]);

  useEffect(() => {
    if (!notice || notice.incident_id !== incidentId) return;
    const abort = new AbortController(); let mounted = true;
    const duration = replayRange === '1h' ? 3_600_000 : replayRange === '24h' ? 86_400_000 : 604_800_000;
    const eventAt = Date.parse(notice.first_seen_at);
    const from = new Date(Math.max(0, eventAt - duration / 2)).toISOString();
    const to = new Date(Math.min(Date.now(), eventAt + duration / 2)).toISOString();
    setReplay(null); setReplayError(null); setReplayBusy(true);
    void auth.requestAuthorized((server, token) => mobileApi.noticeReplay(server, token, incidentId, { from, to }, abort.signal))
      .then(value => { if (mounted && !abort.signal.aborted) setReplay(value); })
      .catch(cause => { if (mounted && !abort.signal.aborted) setReplayError(messageFor(cause)); })
      .finally(() => { if (mounted && !abort.signal.aborted) setReplayBusy(false); });
    return () => { mounted = false; abort.abort(); };
  }, [auth.requestAuthorized, incidentId, notice?.first_seen_at, replayRange]);

  const retryRead = async () => {
    if (busy) return;
    const abort = new AbortController(); setBusy(true); setError(null);
    try { setNotice(await loadAndRead(abort.signal, setNotice)); }
    catch (cause) {
      setError(messageFor(cause)); setRequestId(cause instanceof MobileApiError ? cause.requestId ?? null : null);
      if (cause instanceof MobileApiError && [403, 404].includes(cause.status)) { void auth.syncNotices(true); navigation.goBack(); }
    } finally { setBusy(false); }
  };

  const acknowledge = async () => {
    if (!notice || ackBusy) return;
    const abort = new AbortController(); setAckBusy(true); setError(null);
    try {
      const value = await auth.requestAuthorized((server, token) => mobileApi.acknowledgeNotice(server, token, incidentId, abort.signal));
      setNotice(value); auth.applyNotice(value);
    } catch (cause) {
      setError(messageFor(cause)); setRequestId(cause instanceof MobileApiError ? cause.requestId ?? null : null);
    } finally { setAckBusy(false); }
  };

  const deviceId = notice?.affected_devices.length === 1 ? notice.affected_devices[0].device_id : undefined;
  const replayDeviceNames = new Map((replay?.devices ?? []).map(item => [item.device_id, item.display_name]));
  const replayItems = replay ? [
    ...replay.incident_timeline.map(item => ({ at: item.occurred_at, label: '提醒进度', text: `${statusLabels[item.resulting_status]} · ${item.public_progress}` })),
    ...replay.devices.flatMap(device => device.observations.flatMap(point => {
      const values = [point.temperature_c === null ? null : `温度 ${point.temperature_c} °C`, point.humidity_percent === null ? null : `湿度 ${point.humidity_percent} %RH`].filter(Boolean);
      return values.length ? [{ at: point.received_at, label: device.display_name, text: values.join(' · ') }] : [];
    })),
    ...replay.connection_status_changes.map(item => ({ at: item.occurred_at, label: replayDeviceNames.get(item.device_id) ?? item.device_id, text: `连接状态 ${item.from_status} → ${item.to_status}` })),
    ...replay.relationships.map(item => ({ at: item.bucket_start, label: '已观测通信关系', text: `${replayDeviceNames.get(item.source_device_id) ?? item.source_device_id} → ${replayDeviceNames.get(item.target_device_id) ?? item.target_device_id} · ${item.protocol} · ${item.flows} 条流` })),
    ...replay.gaps.map(item => ({ at: item.from, label: '数据缺口', text: `${replayDeviceNames.get(item.device_id) ?? item.device_id} · ${new Date(item.from).toLocaleString()} 至 ${new Date(item.to).toLocaleString()} · ${item.reason === 'no_observations_in_window' ? '时段内无设备观测' : item.reason === 'observation_gap_over_stale_threshold' ? '观测间隔超过数据新鲜阈值' : '没有留存观测'}` })),
  ].sort((a, b) => Date.parse(a.at) - Date.parse(b.at)) : [];
  return <ScrollView style={ui.page} contentContainerStyle={design.pageContent}>
    {busy && !notice && <ActivityIndicator color={palette.green} />}
    {error && <Text accessibilityRole="alert" style={ui.warning}>{error}{requestId ? ` 请求编号：${requestId}` : ''}</Text>}
    {error && <TouchableOpacity accessibilityRole="button" style={ui.secondaryButton} onPress={() => void retryRead()}>
      <Text style={ui.secondaryText}>重试获取提醒</Text>
    </TouchableOpacity>}
    {notice && <>
      <Text style={ui.title}>{notice.user_title}</Text>
      <Severity notice={notice} />
      <View style={ui.card}>
        <Text style={ui.body}>{notice.user_summary}</Text>
        <Text style={ui.label}>相关设备</Text>
        {notice.affected_devices.length === 0 ? <Text style={ui.muted}>未指定</Text> : notice.affected_devices.map(item =>
          <TouchableOpacity key={item.device_id} accessibilityRole="button"
            accessibilityLabel={`查看受影响设备：${item.display_name}`}
            style={ui.secondaryButton} onPress={() => navigation.navigate('设备详情', { deviceId: item.device_id })}>
            <Text style={ui.secondaryText}>{item.display_name}（{item.device_type}）</Text>
          </TouchableOpacity>)}
        <Text style={ui.muted}>发现时间：{new Date(notice.first_seen_at).toLocaleString()}</Text>
        <Text style={ui.muted}>最近更新：{new Date(notice.updated_at).toLocaleString()}</Text>
        <Text style={ui.label}>处理状态：{statusLabels[notice.status]}</Text>
        <Text style={ui.body}>管理员进度：{notice.public_progress}</Text>
        <Text style={ui.muted}>{notice.read ? '已读' : '已打开并记录阅读'} · {notice.acknowledged ? '你已标记为已知晓' : '尚未标记已知晓'}</Text>
        {notice.resolved_at && <Text style={ui.muted}>完成时间：{new Date(notice.resolved_at).toLocaleString()}</Text>}
      </View>
      <View style={design.detailSection}>
        <Text style={design.detailSectionTitle}>事件时间轴</Text>
        <Text style={ui.muted}>只显示留存的授权设备状态、传感器读数、通信关系、公开提醒进度和缺失区间。</Text>
        <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={design.filterList}>
          {REPLAY_RANGES.map(item => {
            const active = replayRange === item.value;
            return <TouchableOpacity key={item.value} accessibilityRole="button" accessibilityState={{ selected: active }}
              style={[design.filterChip, active && design.filterChipActive]} onPress={() => setReplayRange(item.value)}>
              <Text style={[design.filterText, active && design.filterTextActive]}>{item.label}</Text>
            </TouchableOpacity>;
          })}
        </ScrollView>
        {replayBusy && <ActivityIndicator accessibilityLabel="正在读取事件时间轴" color={palette.green} />}
        {replayError && <Text accessibilityRole="alert" style={ui.warning}>时间轴读取失败：{replayError}</Text>}
        {replay && <>
          <Text style={ui.muted}>时间范围：{new Date(replay.window.from).toLocaleString()} 至 {new Date(replay.window.to).toLocaleString()}</Text>
          {!replay.traffic_available && <Text style={ui.muted}>设备通信关系历史暂不可用。</Text>}
          {!replay.capabilities.connection_status_history && <Text style={ui.muted}>连接状态历史未配置。</Text>}
          {!replay.capabilities.camera_media && <Text style={ui.muted}>摄像头画面未留存，时间轴不包含影像。</Text>}
          {replay.truncated && <Text style={ui.warning}>事件较多，时间轴结果已截断。</Text>}
          {replayItems.length === 0 ? <Text style={ui.muted}>所选时段没有可回放的留存记录。</Text> : replayItems.map((item, index) =>
            <View key={`${item.at}:${item.label}:${index}`} style={{ borderLeftWidth: 2, borderColor: palette.border, paddingLeft: 12, gap: 3 }}>
              <Text style={ui.label}>{item.label}</Text><Text style={ui.body}>{item.text}</Text>
              <Text style={ui.muted}>{new Date(item.at).toLocaleString()}</Text>
            </View>)}
        </>}
      </View>
      {!notice.acknowledged && <TouchableOpacity accessibilityRole="button" disabled={ackBusy} style={[ui.button, ackBusy && { opacity: 0.6 }]} onPress={() => void acknowledge()}>
        <Text style={ui.buttonText}>{ackBusy ? '正在提交…' : '我已知晓'}</Text>
      </TouchableOpacity>}
      {notice.acknowledged && <Text style={ui.muted}>“我已知晓”仅表示你已阅读，不代表事件已经解决。</Text>}
      <TouchableOpacity accessibilityRole="button" style={ui.secondaryButton} onPress={() => navigation.navigate('提交求助', { incidentId, deviceId })}>
        <Text style={ui.secondaryText}>就此提醒联系管理员</Text>
      </TouchableOpacity>
      <SupportContactCard />
    </>}
  </ScrollView>;
}
