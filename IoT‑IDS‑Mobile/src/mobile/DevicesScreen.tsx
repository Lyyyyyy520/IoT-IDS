import React, { useMemo, useState } from 'react';
import { Modal, ScrollView, Text, TextInput, TouchableOpacity, View } from 'react-native';
import { useMobile } from './MobileContext';
import type { MobileDevice } from './api';
import { ui } from './ui';

const labels = { all: '全部', online: '在线', stale: '延迟', offline: '离线', unknown: '未知' };
type Filter = keyof typeof labels;
export default function DevicesScreen() {
  const auth = useMobile();
  const [search, setSearch] = useState('');
  const [filter, setFilter] = useState<Filter>('all');
  const [selected, setSelected] = useState<MobileDevice | null>(null);
  const devices = useMemo(() => (auth.overview?.devices ?? []).filter(d =>
    (filter === 'all' || d.connection_status === filter) &&
    `${d.display_name} ${d.device_type} ${d.area_id ?? ''}`.toLocaleLowerCase().includes(search.toLocaleLowerCase())),
  [auth.overview, filter, search]);
  return <View style={ui.page}>
    <ScrollView contentContainerStyle={ui.content}>
      <Text style={ui.title}>本人设备</Text>
      {auth.stale && <Text accessibilityRole="alert" style={ui.warning}>连接中断，设备信息可能过期</Text>}
      <TextInput accessibilityLabel="搜索设备" style={ui.input} placeholder="搜索名称、类型或区域" value={search} onChangeText={setSearch} />
      <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 8 }}>
        {(Object.keys(labels) as Filter[]).map(key => <TouchableOpacity key={key} accessibilityRole="button"
          accessibilityState={{ selected: filter === key }} style={ui.secondaryButton} onPress={() => setFilter(key)}>
          <Text style={ui.secondaryText}>{labels[key]}</Text>
        </TouchableOpacity>)}
      </View>
      {!auth.overview ? <Text style={ui.muted}>尚未获取授权设备数据</Text> :
        devices.length === 0 ? <Text style={ui.muted}>{auth.overview.devices.length ? '没有符合条件的设备' : '管理员尚未授权设备'}</Text> :
          devices.map(d => <TouchableOpacity accessibilityRole="button" key={d.device_id} style={ui.card} onPress={() => setSelected(d)}>
            <Text style={ui.cardTitle}>{d.display_name}</Text>
            <Text style={ui.body}>{d.device_type} · {d.area_id ?? '未分配区域'}</Text>
            <Text style={ui.muted}>连接：{labels[d.connection_status]}　运行：{d.operation_mode}{d.retired ? ' · 已退役' : ''}</Text>
            <Text style={ui.muted}>最后更新：{d.last_updated_at ? new Date(d.last_updated_at).toLocaleString() : '未知'}</Text>
          </TouchableOpacity>)}
    </ScrollView>
    <Modal visible={selected !== null} transparent animationType="fade" onRequestClose={() => setSelected(null)}>
      <View style={[ui.page, { justifyContent: 'center', padding: 20 }]}>
        <View style={ui.card}>
          <Text style={ui.cardTitle}>{selected?.display_name}</Text>
          <Text style={ui.body}>连接：{selected && labels[selected.connection_status]}</Text>
          <Text style={ui.body}>运行模式：{selected?.operation_mode}{selected?.retired ? ' · 已退役' : ''}</Text>
          <Text style={ui.muted}>详细信息将在后续版本提供</Text>
          <TouchableOpacity accessibilityRole="button" style={ui.secondaryButton} onPress={() => setSelected(null)}>
            <Text style={ui.secondaryText}>关闭</Text>
          </TouchableOpacity>
        </View>
      </View>
    </Modal>
  </View>;
}
