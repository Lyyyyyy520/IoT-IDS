import React, { useEffect, useRef } from 'react';
import { RefreshControl, ScrollView, Text, TouchableOpacity, View } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { useNavigation } from '@react-navigation/native';
import { useMobile } from './MobileContext';
import type { MobileDevice, MobileNotice } from './api';
import { design, palette, ui } from './ui';

const statusText = { online: '在线', stale: '延迟', offline: '离线', unknown: '未知' };
const statusColors = {
  online: { bg: palette.greenSoft, fg: palette.green },
  stale: { bg: palette.amberSoft, fg: palette.amber },
  offline: { bg: palette.coralSoft, fg: palette.coral },
  unknown: { bg: '#F0EFEB', fg: palette.muted },
};

function deviceIcon(device: MobileDevice): React.ComponentProps<typeof Ionicons>['name'] {
  const name = `${device.display_name} ${device.device_type}`.toLocaleLowerCase();
  if (/camera|摄像/.test(name)) return 'videocam-outline';
  if (/lock|门禁|门锁/.test(name)) return 'lock-closed-outline';
  if (/sensor|传感|温湿/.test(name)) return 'thermometer-outline';
  if (/gateway|router|网关|路由/.test(name)) return 'wifi-outline';
  return 'hardware-chip-outline';
}

function greeting() {
  const hour = new Date().getHours();
  if (hour < 5) return '夜深了';
  if (hour < 11) return '早上好';
  if (hour < 18) return '下午好';
  return '晚上好';
}

export default function HomeScreen() {
  const auth = useMobile();
  const navigation = useNavigation<any>();
  const syncRef = useRef(auth.syncNotices);
  syncRef.current = auth.syncNotices;
  useEffect(() => {
    if (auth.overview?.security_capability.available) void syncRef.current(true);
  }, [auth.overview?.security_capability.available]);
  const devices = auth.overview?.devices ?? [];
  const recent = (auth.notices ?? auth.overview?.security_capability.recent_notices ?? []).slice(0, 1);
  const unread = auth.notices ? auth.notices.filter(item => !item.read && item.status !== 'false_positive').length :
    auth.overview?.security_capability.unread_count ?? 0;
  const activeNotices = auth.notices
    ? auth.notices.filter(item => item.status !== 'resolved' && item.status !== 'false_positive').length
    : auth.overview?.security_capability.unacknowledged_count ?? unread;
  const counts = {
    online: devices.filter(d => d.connection_status === 'online').length,
    stale: devices.filter(d => d.connection_status === 'stale').length,
    offline: devices.filter(d => d.connection_status === 'offline').length,
    unknown: devices.filter(d => d.connection_status === 'unknown').length,
  };
  const openNotice = (notice: MobileNotice) => navigation.navigate('提醒详情', { incidentId: notice.incident_id });
  const visibleDevices = devices.slice(0, 4);

  return <ScrollView style={ui.page} contentContainerStyle={design.pageContent}
    refreshControl={<RefreshControl refreshing={auth.busy} onRefresh={() => void auth.sync()} />}>
    <View style={design.homeHero}>
      <View style={design.heroCopy}>
        <Text style={design.eyebrow}>智慧家庭安全</Text>
        <Text style={design.heroTitle}>{greeting()}，{auth.overview?.user.username ?? auth.session?.user.username ?? '邻居'}</Text>
        <Text style={design.heroSubtitle}>家人平安，就是最好的安心</Text>
      </View>
      <View style={design.heroArt}>
        <View style={design.heroArtCircle} />
        <Ionicons name="home" size={59} color="#A78961" />
        <View style={{ position: 'absolute', bottom: 12, right: 12, backgroundColor: palette.green,
          borderRadius: 12, padding: 5 }}>
          <Ionicons name="shield-checkmark" size={18} color="#FFFFFF" />
        </View>
      </View>
    </View>

    {auth.config?.insecureLan && <Text style={ui.warning}>HTTP 开发模式：连接未加密</Text>}
    {auth.stale && <Text accessibilityRole="alert" style={ui.warning}>连接中断，以下数据可能已过期。{auth.error}</Text>}

    <View style={design.summaryRow}>
      <TouchableOpacity accessibilityRole="button" style={[design.summaryCard, design.summaryGreen]}
        onPress={() => navigation.navigate('本人设备')}>
        <View style={[design.summaryIcon, { backgroundColor: '#D5EBDD' }]}>
          <Ionicons name="shield-checkmark" size={25} color={palette.green} />
        </View>
        <View style={{ flex: 1 }}>
          <Text style={[design.summaryNumber, { color: palette.green }]}>{counts.online}</Text>
          <Text style={design.summaryLabel}>台设备在线</Text>
        </View>
        <Ionicons name="chevron-forward" size={16} color={palette.green} />
      </TouchableOpacity>
      <TouchableOpacity accessibilityRole="button" style={[design.summaryCard, design.summaryRed]}
        onPress={() => navigation.navigate('安全提醒')}>
        <View style={[design.summaryIcon, { backgroundColor: '#F8D9D9' }]}>
          <Ionicons name="notifications" size={23} color={activeNotices > 0 ? palette.coral : palette.muted} />
        </View>
        <View style={{ flex: 1 }}>
          <Text style={[design.summaryNumber, { color: activeNotices > 0 ? palette.coral : palette.muted }]}>{activeNotices}</Text>
          <Text style={design.summaryLabel}>条安全提醒</Text>
        </View>
        <Ionicons name="chevron-forward" size={16} color={palette.coral} />
      </TouchableOpacity>
    </View>

    <View style={design.sectionRow}>
      <Text style={design.sectionHeading}>我的设备</Text>
      <TouchableOpacity accessibilityRole="button" onPress={() => navigation.navigate('本人设备')}>
        <Text style={design.sectionLink}>查看全部　›</Text>
      </TouchableOpacity>
    </View>
    {!auth.overview ? <View style={ui.card}><Text style={ui.muted}>尚未获取授权设备数据</Text></View> : devices.length === 0 ?
      <View style={ui.card}><Text style={ui.muted}>管理员尚未授权设备</Text></View> :
      <View style={design.deviceGrid}>
        {visibleDevices.map(device => {
          const colors = statusColors[device.connection_status];
          return <TouchableOpacity key={device.device_id} accessibilityRole="button"
            accessibilityLabel={`查看设备：${device.display_name}`} style={design.deviceTile}
            onPress={() => navigation.navigate('设备详情', { deviceId: device.device_id })}>
            <View style={design.deviceTileTop}>
              <View style={design.deviceArt}><Ionicons name={deviceIcon(device)} size={26} color="#4D5552" /></View>
              <View style={[design.statusPill, { backgroundColor: colors.bg }]}>
                <View style={{ width: 7, height: 7, borderRadius: 4, backgroundColor: colors.fg }} />
                <Text style={[design.statusText, { color: colors.fg }]}>{statusText[device.connection_status]}</Text>
              </View>
            </View>
            <Text numberOfLines={1} style={design.deviceTileTitle}>{device.display_name}</Text>
            <Text numberOfLines={1} style={design.deviceTileMeta}>{statusText[device.connection_status]} · {device.area_id ?? device.device_type}</Text>
            <Text numberOfLines={1} style={design.deviceTileMeta}>运行模式：{device.operation_mode}{device.retired ? ' · 已退役' : ''}</Text>
          </TouchableOpacity>;
        })}
      </View>}

    <View style={design.featureBanner}>
      <View style={{ gap: 5 }}>
        <Text style={design.featureTitle}>智能守护{'\n'}让家更安心</Text>
        <Text style={design.featureSubtitle}>{counts.online} 台在线 · {counts.stale + counts.offline + counts.unknown} 台需要关注</Text>
      </View>
      <View style={{ width: 82, height: 68, alignItems: 'center', justifyContent: 'center' }}>
        <Ionicons name="leaf" size={39} color="#6A8A61" />
        <Ionicons name="sparkles" size={17} color="#BC9557" style={{ position: 'absolute', right: 5, top: 1 }} />
      </View>
    </View>

    <View style={design.sectionRow}>
      <Text style={design.sectionHeading}>安全提醒</Text>
      <TouchableOpacity accessibilityRole="button" onPress={() => navigation.navigate('安全提醒')}>
        <Text style={design.sectionLink}>查看全部　›</Text>
      </TouchableOpacity>
    </View>
    {recent.length > 0 ? recent.map(notice => <TouchableOpacity key={notice.incident_id}
      accessibilityRole="button" style={[design.noticeCard, notice.severity === 'critical' || notice.severity === 'high' ? design.noticeCritical : null]}
      onPress={() => openNotice(notice)}>
      <Text style={design.noticeTitle}>{notice.user_title}</Text>
      <Text style={design.noticeSummary} numberOfLines={2}>{notice.user_summary ?? notice.public_progress}</Text>
      <Text style={design.noticeMeta}>查看处理进度　›</Text>
    </TouchableOpacity>) : <View style={[ui.card, { paddingVertical: 14 }]}>
      <Text style={ui.muted}>{auth.overview?.security_capability.available === false ? '安全事件功能尚未接入' : (unread === 0 ? '当前没有未读提醒。' : '暂无提醒数据')}</Text>
    </View>}
  </ScrollView>;
}
