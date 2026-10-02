import React, { useCallback, useRef, useState } from 'react';
import { ActivityIndicator, AppState, ScrollView, Text, TextInput, TouchableOpacity, View } from 'react-native';
import * as Crypto from 'expo-crypto';
import { useFocusEffect, useNavigation, useRoute } from '@react-navigation/native';
import { mobileApi, MobileApiError, type HelpCategory, type MobileHelpRequest } from './api';
import { messageFor, useMobile } from './MobileContext';
import { palette, ui } from './ui';

const categoryLabels: Record<HelpCategory, string> = {
  device_issue: '设备使用问题', security_question: '提醒相关问题', service_problem: '服务问题', other: '其他',
};
const helpStatusLabels = { open: '已提交', in_progress: '管理员处理中', waiting_for_user: '等待你补充信息', closed: '已完成' };
export const newIdempotencyKey = (): string => {
  const nativeUuid = Crypto.randomUUID();
  if (typeof nativeUuid === 'string' && /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(nativeUuid)) return nativeUuid;
  const bytes = Crypto.getRandomBytes(16);
  if (!(bytes instanceof Uint8Array) || bytes.length !== 16) throw new Error('secure_random_unavailable');
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = Array.from(bytes, value => value.toString(16).padStart(2, '0')).join('');
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
};

export function SubmitHelpScreen() {
  const auth = useMobile();
  const navigation = useNavigation<any>();
  const route = useRoute<any>();
  const [category, setCategory] = useState<HelpCategory>('other');
  const [incidentId, setIncidentId] = useState<string | undefined>(route.params?.incidentId);
  const [deviceId, setDeviceId] = useState<string | undefined>(route.params?.deviceId);
  const [message, setMessage] = useState('');
  const [key, setKey] = useState(newIdempotencyKey);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [requestId, setRequestId] = useState<string | null>(null);
  const mounted = useRef(true);
  React.useEffect(() => () => { mounted.current = false; }, []);
  const setField = (change: () => void) => { change(); setKey(newIdempotencyKey()); setError(null); };
  const submit = async () => {
    if (busy) return;
    const clean = message.trim();
    if (!clean || clean.length > 1000 || /[\u0000-\u0008\u000B\u000C\u000E-\u001F]/.test(clean)) {
      setError('请填写 1–1000 个字符，且不要使用控制字符。'); return;
    }
    setBusy(true); setError(null); setRequestId(null);
    try {
      const result = await auth.requestAuthorized((server, token) => mobileApi.createHelpRequest(server, token, {
        category, ...(incidentId ? { incident_id: incidentId } : {}), ...(deviceId ? { device_id: deviceId } : {}), user_message: clean,
      }, key));
      if (!mounted.current) return;
      setMessage(''); setKey(newIdempotencyKey());
      navigation.replace('求助详情', { helpRequestId: result.help_request_id });
    } catch (cause) {
      if (!mounted.current) return;
      setError(messageFor(cause)); setRequestId(cause instanceof MobileApiError ? cause.requestId ?? null : null);
      if (cause instanceof MobileApiError && cause.status === 404 && (incidentId || deviceId)) {
        setIncidentId(undefined); setDeviceId(undefined); setKey(newIdempotencyKey());
        setError('关联的提醒或设备已不在当前授权范围内，已清除关联。请确认内容后重试。');
      }
      // The key is intentionally retained when the form body is unchanged so a manual retry is idempotent.
    } finally { if (mounted.current) setBusy(false); }
  };

  const devices = auth.overview?.devices ?? [];
  const notices = auth.notices ?? [];
  return <ScrollView style={ui.page} contentContainerStyle={ui.content}>
    <Text style={ui.title}>联系管理员</Text>
    <Text style={ui.subtitle}>请勿填写密码、配对码或令牌。此表单不支持附件，也不会在本机离线保存正文。</Text>
    <Text style={ui.label}>问题类型</Text>
    <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 8 }}>
      {(Object.keys(categoryLabels) as HelpCategory[]).map(value => <TouchableOpacity key={value} accessibilityRole="button"
        accessibilityState={{ selected: category === value }} style={ui.secondaryButton}
        onPress={() => setField(() => setCategory(value))}><Text style={ui.secondaryText}>{categoryLabels[value]}</Text></TouchableOpacity>)}
    </View>
    <View style={ui.card}>
      <Text style={ui.label}>关联提醒（可选）</Text>
      {incidentId && <Text style={ui.body}>已关联：{notices.find(item => item.incident_id === incidentId)?.user_title ?? '已选提醒'}</Text>}
      <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 8 }}>
        {notices.filter(item => item.status !== 'false_positive').map(item => <TouchableOpacity key={item.incident_id} accessibilityRole="button"
          accessibilityState={{ selected: incidentId === item.incident_id }} style={ui.secondaryButton}
          onPress={() => setField(() => setIncidentId(incidentId === item.incident_id ? undefined : item.incident_id))}>
          <Text style={ui.secondaryText}>{item.user_title}</Text>
        </TouchableOpacity>)}
      </View>
      <Text style={ui.label}>关联设备（可选）</Text>
      {deviceId && <Text style={ui.body}>已关联：{devices.find(item => item.device_id === deviceId)?.display_name ?? '已选设备'}</Text>}
      <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 8 }}>
        {devices.map(item => <TouchableOpacity key={item.device_id} accessibilityRole="button"
          accessibilityState={{ selected: deviceId === item.device_id }} style={ui.secondaryButton}
          onPress={() => setField(() => setDeviceId(deviceId === item.device_id ? undefined : item.device_id))}>
          <Text style={ui.secondaryText}>{item.display_name}</Text>
        </TouchableOpacity>)}
      </View>
    </View>
    <Text style={ui.label}>求助内容</Text>
    <TextInput accessibilityLabel="求助内容" multiline maxLength={1000} value={message}
      onChangeText={value => { if (value !== message) setKey(newIdempotencyKey()); setMessage(value); setError(null); }}
      placeholder="请描述需要管理员协助的事项" style={[ui.input, { minHeight: 130, textAlignVertical: 'top' }]} />
    <Text style={ui.muted}>{message.length}/1000</Text>
    {error && <Text accessibilityRole="alert" style={ui.warning}>{error}{requestId ? ` 请求编号：${requestId}` : ''}</Text>}
    <TouchableOpacity accessibilityRole="button" disabled={busy} style={[ui.button, busy && { opacity: 0.6 }]} onPress={() => void submit()}>
      <Text style={ui.buttonText}>{busy ? '正在提交…' : '提交求助'}</Text>
    </TouchableOpacity>
  </ScrollView>;
}

export function HelpListScreen() {
  const auth = useMobile();
  const navigation = useNavigation<any>();
  const [items, setItems] = useState<MobileHelpRequest[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useFocusEffect(useCallback(() => {
    let mounted = true; let abort: AbortController | null = null;
    const load = async () => {
      if (abort) abort.abort();
      abort = new AbortController(); setBusy(true);
      try {
        const result = await auth.requestAuthorized((server, token) => mobileApi.helpRequests(server, token, abort!.signal));
        if (mounted && !abort.signal.aborted) { setItems(result.items); setError(null); }
      } catch (cause) { if (mounted && !abort?.signal.aborted) setError(messageFor(cause)); }
      finally { if (mounted && !abort?.signal.aborted) setBusy(false); }
    };
    void load();
    const appState = AppState.addEventListener('change', state => { if (state === 'active') void load(); else abort?.abort(); });
    const timer = setInterval(() => { if (AppState.currentState === 'active') void load(); }, 30_000);
    return () => { mounted = false; abort?.abort(); clearInterval(timer); appState.remove(); };
  }, [auth.requestAuthorized]));
  return <ScrollView style={ui.page} contentContainerStyle={ui.content}>
    <Text style={ui.title}>我的求助</Text>
    {busy && !items && <ActivityIndicator color={palette.green} />}
    {error && <Text accessibilityRole="alert" style={ui.warning}>{error}</Text>}
    {items?.length === 0 && <Text style={ui.muted}>你还没有提交求助。</Text>}
    {items?.map(item => <TouchableOpacity key={item.help_request_id} accessibilityRole="button" style={ui.card}
      onPress={() => navigation.navigate('求助详情', { helpRequestId: item.help_request_id })}>
      <Text style={ui.cardTitle}>{categoryLabels[item.category]} · {helpStatusLabels[item.status]}</Text>
      {(item.device_id && auth.overview?.devices.some(device => device.device_id === item.device_id) || item.incident_id && auth.notices?.some(notice => notice.incident_id === item.incident_id)) &&
        <Text style={ui.muted}>关联：{item.device_id ? auth.overview?.devices.find(device => device.device_id === item.device_id)?.display_name : null}{item.incident_id ? ` · ${auth.notices?.find(notice => notice.incident_id === item.incident_id)?.user_title ?? ''}` : ''}</Text>}
      <Text style={ui.body} numberOfLines={2}>{item.user_message}</Text>
      {item.public_response && <Text style={ui.muted} numberOfLines={2}>管理员回复：{item.public_response}</Text>}
      <Text style={ui.muted}>更新时间：{new Date(item.updated_at).toLocaleString()}</Text>
    </TouchableOpacity>)}
  </ScrollView>;
}

export function HelpDetailScreen() {
  const auth = useMobile(); const route = useRoute<any>();
  const id = String(route.params?.helpRequestId ?? '');
  const [item, setItem] = useState<MobileHelpRequest | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(true);
  const flight = useRef(false);
  useFocusEffect(useCallback(() => {
    let mounted = true; let abort: AbortController | null = null;
    const load = async () => {
      if (flight.current) return;
      flight.current = true; abort = new AbortController(); setBusy(true);
      try {
        const value = await auth.requestAuthorized((server, token) => mobileApi.helpRequest(server, token, id, abort!.signal));
        if (mounted && !abort.signal.aborted) { setItem(value); setError(null); }
      } catch (cause) { if (mounted && !abort?.signal.aborted) setError(messageFor(cause)); }
      finally { flight.current = false; if (mounted && !abort?.signal.aborted) setBusy(false); }
    };
    void load();
    const appState = AppState.addEventListener('change', state => { if (state === 'active') void load(); else abort?.abort(); });
    const timer = setInterval(() => { if (AppState.currentState === 'active') void load(); }, 30_000);
    return () => { mounted = false; abort?.abort(); clearInterval(timer); appState.remove(); };
  }, [id, auth.requestAuthorized]));
  return <ScrollView style={ui.page} contentContainerStyle={ui.content}>
    {busy && <ActivityIndicator color={palette.green} />}
    {error && <Text accessibilityRole="alert" style={ui.warning}>{error}</Text>}
    {item && <>
      <Text style={ui.title}>求助详情</Text>
      <View style={ui.card}>
        <Text style={ui.cardTitle}>{categoryLabels[item.category]}</Text>
        <Text style={ui.label}>{helpStatusLabels[item.status]}</Text>
        {(item.device_id && auth.overview?.devices.some(device => device.device_id === item.device_id) || item.incident_id && auth.notices?.some(notice => notice.incident_id === item.incident_id)) &&
          <Text style={ui.muted}>关联：{item.device_id ? auth.overview?.devices.find(device => device.device_id === item.device_id)?.display_name : null}{item.incident_id ? ` · ${auth.notices?.find(notice => notice.incident_id === item.incident_id)?.user_title ?? ''}` : ''}</Text>}
        <Text style={ui.body}>{item.user_message}</Text>
        <Text style={ui.muted}>提交时间：{new Date(item.created_at).toLocaleString()}</Text>
        <Text style={ui.muted}>更新时间：{new Date(item.updated_at).toLocaleString()}</Text>
        {item.public_response && <Text style={ui.body}>管理员回复：{item.public_response}</Text>}
        {item.closed_at && <Text style={ui.muted}>完成时间：{new Date(item.closed_at).toLocaleString()}</Text>}
      </View>
    </>}
  </ScrollView>;
}
