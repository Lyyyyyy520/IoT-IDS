import React, { useEffect, useMemo, useRef, useState } from 'react';
import { ActivityIndicator, RefreshControl, ScrollView, Text, TouchableOpacity, View } from 'react-native';
import { useIsFocused, useNavigation, useRoute } from '@react-navigation/native';
import { mobileApi, MobileApiError, type MobileNotice, type NoticeStatus } from './api';
import { messageFor, useMobile } from './MobileContext';
import SupportContactCard from './SupportContactCard';
import { palette, ui } from './ui';

type NoticeFilter = 'all' | 'unread' | 'unacknowledged' | 'processing' | 'resolved';
const filterLabels: Record<NoticeFilter, string> = { all: '全部', unread: '未读', unacknowledged: '未知晓', processing: '处理中', resolved: '已解决' };
const severityLabels: Record<MobileNotice['severity'], string> = { info: '提醒', low: '较低', medium: '一般', high: '较高', critical: '紧急' };
const severityIcons: Record<MobileNotice['severity'], string> = { info: 'ℹ', low: '○', medium: '!', high: '⚠', critical: '‼' };
const statusLabels: Record<NoticeStatus, string> = {
  open: '等待管理员处理', acknowledged: '管理员正在核查', recovering: '处理中，正在恢复',
  resolved: '已完成处理', false_positive: '提醒已结束',
};
const processing = (status: NoticeStatus) => status === 'open' || status === 'acknowledged' || status === 'recovering';

function Severity({ notice }: { notice: MobileNotice }) {
  return <Text style={[ui.label, { color: notice.severity === 'critical' || notice.severity === 'high' ? palette.coral : palette.amber }]}>
    {severityIcons[notice.severity]} {severityLabels[notice.severity]}
  </Text>;
}

export default function NoticeListScreen() {
  const auth = useMobile();
  const navigation = useNavigation<any>();
  const [filter, setFilter] = useState<NoticeFilter>('all');
  const focused = useIsFocused();
  const syncRef = useRef(auth.syncNotices);
  syncRef.current = auth.syncNotices;
  useEffect(() => { if (focused) void syncRef.current(false); }, [focused]);
  const notices = useMemo(() => (auth.notices ?? []).filter(item => {
    if (filter === 'unread') return !item.read;
    if (filter === 'unacknowledged') return !item.acknowledged;
    if (filter === 'processing') return processing(item.status);
    if (filter === 'resolved') return item.status === 'resolved' || item.status === 'false_positive';
    return true;
  }), [auth.notices, filter]);
  const capability = auth.overview?.security_capability;
  return <ScrollView style={ui.page} contentContainerStyle={ui.content}
    refreshControl={<RefreshControl refreshing={auth.noticesBusy} onRefresh={() => void auth.syncNotices(true)} />}>
    <Text style={ui.title}>安全提醒</Text>
    <Text style={ui.subtitle}>这里只显示管理员发布给您的提醒和公开处理进度。</Text>
    {auth.noticesStale && <Text accessibilityRole="alert" style={ui.warning}>提醒数据可能已过期。{auth.noticesError ?? ''}</Text>}
    {auth.lastNoticesSynced && <Text style={ui.muted}>最近同步：{new Date(auth.lastNoticesSynced).toLocaleString()}</Text>}
    {capability?.available === false ? <View style={ui.card}>
      <Text style={ui.cardTitle}>提醒功能暂不可用</Text><Text style={ui.muted}>安全事件功能尚未接入或暂时不可用。</Text>
    </View> : auth.notices === null ? <View style={ui.card}>
      {auth.noticesBusy && <ActivityIndicator color={palette.green} />}
      <Text accessibilityRole="alert" style={auth.noticesError ? ui.warning : ui.muted}>{auth.noticesError ?? '正在获取提醒…'}</Text>
      {auth.noticesError && <TouchableOpacity accessibilityRole="button" style={ui.secondaryButton} onPress={() => void auth.syncNotices(true)}><Text style={ui.secondaryText}>重试</Text></TouchableOpacity>}
    </View> : <>
      <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 8 }}>
        {(Object.keys(filterLabels) as NoticeFilter[]).map(key => <TouchableOpacity key={key} accessibilityRole="button"
          accessibilityState={{ selected: filter === key }} style={ui.secondaryButton} onPress={() => setFilter(key)}>
          <Text style={ui.secondaryText}>{filterLabels[key]}</Text>
        </TouchableOpacity>)}
      </View>
      {notices.length === 0 ? <View style={ui.card}><Text style={ui.muted}>当前没有已记录提醒。</Text></View> : notices.map(notice =>
        <TouchableOpacity key={notice.incident_id} accessibilityRole="button" style={ui.card}
          accessibilityLabel={`查看提醒：${notice.user_title}`} onPress={() => navigation.navigate('提醒详情', { incidentId: notice.incident_id })}>
          <Severity notice={notice} />
          <Text style={ui.cardTitle}>{notice.user_title}</Text>
          <Text style={ui.body} numberOfLines={3}>{notice.user_summary}</Text>
          <Text style={ui.muted}>相关设备：{notice.affected_devices.map(device => device.display_name).join('、') || '未指定'}</Text>
          <Text style={ui.muted}>状态：{statusLabels[notice.status]} · {notice.read ? '已读' : '未读'} · {notice.acknowledged ? '我已知晓' : '尚未标记已知晓'}</Text>
          <Text style={ui.muted}>管理员进度：{notice.public_progress}</Text>
          <Text style={ui.muted}>更新时间：{new Date(notice.updated_at).toLocaleString()}</Text>
        </TouchableOpacity>)}
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
  return <ScrollView style={ui.page} contentContainerStyle={ui.content}>
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
