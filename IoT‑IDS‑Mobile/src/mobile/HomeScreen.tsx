import React from 'react';
import { RefreshControl, ScrollView, Text, View } from 'react-native';
import { useMobile } from './MobileContext';
import { palette, ui } from './ui';

const statusText = { online: '在线', stale: '延迟', offline: '离线', unknown: '未知' };
export default function HomeScreen() {
  const auth = useMobile();
  const devices = auth.overview?.devices ?? [];
  const counts = {
    online: devices.filter(d => d.connection_status === 'online').length,
    stale: devices.filter(d => d.connection_status === 'stale').length,
    offline: devices.filter(d => d.connection_status === 'offline').length,
    unknown: devices.filter(d => d.connection_status === 'unknown').length,
  };
  return <ScrollView style={ui.page} contentContainerStyle={ui.content}
    refreshControl={<RefreshControl refreshing={auth.busy} onRefresh={() => void auth.sync()} />}>
    <Text style={ui.title}>你好，{auth.overview?.user.username ?? auth.session?.user.username ?? '住户'}</Text>
    <Text style={ui.subtitle}>这里只显示管理员授权给您的设备。</Text>
    {auth.config?.insecureLan && <Text style={ui.warning}>HTTP 开发模式：连接未加密</Text>}
    {auth.stale && <Text accessibilityRole="alert" style={ui.warning}>连接中断，以下数据可能已过期。{auth.error}</Text>}
    {auth.requestId && <Text style={ui.muted}>请求编号：{auth.requestId}</Text>}
    <View style={ui.card}>
      <Text style={ui.cardTitle}>本人设备 · {devices.length} 台</Text>
      <Text style={ui.body}>在线 {counts.online}　延迟 {counts.stale}　离线 {counts.offline}　未知 {counts.unknown}</Text>
      <Text style={ui.muted}>最近同步：{auth.lastSynced ? new Date(auth.lastSynced).toLocaleString() : '尚未同步'}</Text>
    </View>
    <View style={ui.card}>
      <Text style={ui.cardTitle}>安全提醒</Text>
      <Text style={ui.muted}>{auth.overview?.security_capability.available === false ? '安全事件功能尚未接入' : '安全提醒功能尚未接入'}</Text>
    </View>
    <Text style={ui.cardTitle}>设备简表</Text>
    {!auth.overview ? <Text style={ui.muted}>尚未获取授权设备数据</Text> : devices.length === 0 ?
      <Text style={ui.muted}>管理员尚未授权设备</Text> : devices.map(device =>
      <View key={device.device_id} style={ui.card}>
        <Text style={ui.cardTitle}>{device.display_name}</Text>
        <Text style={[ui.body, { color: device.connection_status === 'online' ? palette.green : palette.text }]}>
          连接：{statusText[device.connection_status]}
        </Text>
        <Text style={ui.muted}>运行模式：{device.operation_mode}{device.retired ? ' · 已退役' : ''}</Text>
      </View>)}
  </ScrollView>;
}
