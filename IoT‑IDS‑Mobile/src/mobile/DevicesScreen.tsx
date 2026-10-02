import React, { useMemo, useState } from 'react';
import { ScrollView, Text, TextInput, TouchableOpacity, View } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { useNavigation } from '@react-navigation/native';
import { useMobile } from './MobileContext';
import type { MobileDevice, MobileNotice } from './api';
import MobileTopologyPanel from './MobileTopologyPanel';
import { design, palette, ui } from './ui';

const labels = { all: '全部', online: '在线', stale: '延迟', offline: '离线', unknown: '未知' };
type Filter = keyof typeof labels;
type AlertFilter = 'all' | 'alerted' | 'no-active-alert';
const alertLabels: Record<AlertFilter, string> = { all: '全部提醒', alerted: '有活动提醒', 'no-active-alert': '无活动提醒' };
const activeNotice = (notice: MobileNotice) => ['open', 'acknowledged', 'recovering'].includes(notice.status);

function iconFor(device: MobileDevice): React.ComponentProps<typeof Ionicons>['name'] {
  const name = `${device.display_name} ${device.device_type}`.toLocaleLowerCase();
  if (/camera|摄像/.test(name)) return 'videocam-outline';
  if (/lock|门禁|门锁/.test(name)) return 'lock-closed-outline';
  if (/sensor|传感|温湿/.test(name)) return 'thermometer-outline';
  if (/gateway|router|网关|路由/.test(name)) return 'wifi-outline';
  return 'hardware-chip-outline';
}

const statusColors = {
  online: { bg: palette.greenSoft, fg: palette.green },
  stale: { bg: palette.amberSoft, fg: palette.amber },
  offline: { bg: palette.coralSoft, fg: palette.coral },
  unknown: { bg: '#F0EFEB', fg: palette.muted },
};

export default function DevicesScreen() {
  const auth = useMobile();
  const navigation = useNavigation<any>();
  const [search, setSearch] = useState('');
  const [filter, setFilter] = useState<Filter>('all');
  const [typeFilter, setTypeFilter] = useState('all');
  const [alertFilter, setAlertFilter] = useState<AlertFilter>('all');
  const [view, setView] = useState<'list' | 'topology'>('list');
  const allDevices = auth.overview?.devices ?? [];
  const deviceTypes = useMemo(() => [...new Set(allDevices.map(item => item.device_type))].sort(), [allDevices]);
  const alertedIds = useMemo(() => new Set((auth.notices ?? []).filter(activeNotice)
    .flatMap(notice => notice.affected_devices.map(device => device.device_id))), [auth.notices]);
  const alertAvailable = auth.overview?.security_capability.available === true && auth.notices !== null;
  const devices = useMemo(() => (auth.overview?.devices ?? []).filter(d =>
    (filter === 'all' || d.connection_status === filter) &&
    (typeFilter === 'all' || d.device_type === typeFilter) &&
    (alertFilter === 'all' || (alertAvailable && (alertFilter === 'alerted' ? alertedIds.has(d.device_id) : !alertedIds.has(d.device_id)))) &&
    `${d.display_name} ${d.device_id} ${d.device_type} ${d.area_id ?? ''}`.toLocaleLowerCase().includes(search.toLocaleLowerCase())),
  [auth.overview, filter, typeFilter, alertFilter, alertAvailable, alertedIds, search]);
  return <View style={ui.page}>
    <ScrollView contentContainerStyle={design.pageContent}>
      <View style={design.sectionRow}>
        <View style={{ gap: 3 }}>
          <Text style={design.sectionHeading}>我的设备</Text>
          <Text style={ui.muted}>已授权 {allDevices.length} 台设备</Text>
        </View>
        <View style={[design.statusPill, { backgroundColor: palette.greenSoft }]}>
          <Ionicons name="shield-checkmark" size={14} color={palette.green} />
          <Text style={[design.statusText, { color: palette.green }]}>实时守护</Text>
        </View>
      </View>
      {auth.stale && <Text accessibilityRole="alert" style={ui.warning}>连接中断，设备信息可能过期</Text>}
      <View style={{ position: 'relative', justifyContent: 'center' }}>
        <Ionicons name="search-outline" size={19} color={palette.muted} style={{ position: 'absolute', left: 14, zIndex: 1 }} />
        <TextInput accessibilityLabel="搜索设备" style={[ui.input, { paddingLeft: 42, borderRadius: 15, backgroundColor: '#FFFFFF' }]}
          placeholder="搜索名称、设备 ID、类型或区域" placeholderTextColor="#A4A8A4" value={search} onChangeText={setSearch} />
      </View>
      <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={design.filterList}>
        {([{ key: 'list', label: '设备列表' }, { key: 'topology', label: '关系视图' }] as const).map(item => {
          const active = view === item.key;
          return <TouchableOpacity key={item.key} accessibilityRole="button" accessibilityState={{ selected: active }}
            style={[design.filterChip, active && design.filterChipActive]} onPress={() => setView(item.key)}>
            <Text style={[design.filterText, active && design.filterTextActive]}>{item.label}</Text>
          </TouchableOpacity>;
        })}
      </ScrollView>
      <Text style={ui.label}>设备类型</Text>
      <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={design.filterList}>
        {['all', ...deviceTypes].map(value => {
          const active = typeFilter === value;
          return <TouchableOpacity key={value} accessibilityRole="button" accessibilityState={{ selected: active }}
            style={[design.filterChip, active && design.filterChipActive]} onPress={() => setTypeFilter(value)}>
            <Text style={[design.filterText, active && design.filterTextActive]}>{value === 'all' ? '全部类型' : value}</Text>
          </TouchableOpacity>;
        })}
      </ScrollView>
      <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={design.filterList}>
        {(Object.keys(labels) as Filter[]).map(key => {
          const count = key === 'all' ? allDevices.length : allDevices.filter(d => d.connection_status === key).length;
          const active = filter === key;
          return <TouchableOpacity key={key} accessibilityRole="button" accessibilityState={{ selected: active }}
            style={[design.filterChip, active && design.filterChipActive]} onPress={() => setFilter(key)}>
            <Text style={[design.filterText, active && design.filterTextActive]}>{labels[key]}</Text>
            <Text style={[design.filterText, { color: active ? palette.green : palette.muted }]}>{count}</Text>
          </TouchableOpacity>;
        })}
      </ScrollView>
      <Text style={ui.label}>活动提醒</Text>
      <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={design.filterList}>
        {(Object.keys(alertLabels) as AlertFilter[]).map(key => {
          const disabled = key !== 'all' && !alertAvailable;
          const active = alertFilter === key;
          return <TouchableOpacity key={key} accessibilityRole="button"
            accessibilityState={{ selected: active, disabled }} style={[design.filterChip, active && design.filterChipActive, disabled && { opacity: 0.5 }]}
            onPress={() => { if (!disabled) setAlertFilter(key); }}>
            <Text style={[design.filterText, active && design.filterTextActive]}>{alertLabels[key]}</Text>
          </TouchableOpacity>;
        })}
      </ScrollView>
      {!alertAvailable && <Text style={ui.muted}>提醒数据暂不可用；不会把它解释为没有告警。</Text>}
      {!auth.overview ? <View style={ui.card}><Text style={ui.muted}>尚未获取授权设备数据</Text></View> :
        devices.length === 0 ? <View style={[ui.card, design.emptyState]}>
          <Ionicons name="search-outline" size={30} color={palette.muted} />
          <Text style={ui.muted}>{alertFilter !== 'all' && !alertAvailable ? '提醒数据暂不可用，无法应用此筛选。' : allDevices.length ? '没有符合条件的设备' : '管理员尚未授权设备'}</Text>
        </View> : view === 'topology' ? <MobileTopologyPanel devices={devices} /> :
        devices.map(device => {
          const colors = statusColors[device.connection_status];
          return <TouchableOpacity accessibilityRole="button" key={device.device_id}
            accessibilityLabel={`查看设备：${device.display_name}`} onPress={() => navigation.navigate('设备详情', { deviceId: device.device_id })}
            style={[design.detailSection, { flexDirection: 'row', alignItems: 'center', padding: 14 }]}>
            <View style={[design.deviceArt, { width: 58, height: 58, borderRadius: 18 }]}>
              <Ionicons name={iconFor(device)} size={29} color="#4D5552" />
            </View>
            <View style={{ flex: 1, gap: 6 }}>
              <Text style={design.deviceTileTitle}>{device.display_name}</Text>
              <Text style={design.deviceTileMeta}>{device.device_type} · {device.area_id ?? '未分配区域'}</Text>
              <View style={[design.statusPill, { backgroundColor: colors.bg }]}>
                <View style={{ width: 7, height: 7, borderRadius: 4, backgroundColor: colors.fg }} />
                <Text style={[design.statusText, { color: colors.fg }]}>{labels[device.connection_status]}</Text>
              </View>
            </View>
            <Ionicons name="chevron-forward" size={20} color="#929894" />
          </TouchableOpacity>;
        })}
    </ScrollView>
  </View>;
}
