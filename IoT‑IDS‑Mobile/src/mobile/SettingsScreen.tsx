import React, { useEffect, useRef } from 'react';
import { Alert, ScrollView, Text, TouchableOpacity, View } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { useNavigation } from '@react-navigation/native';
import { useMobile } from './MobileContext';
import SupportContactCard from './SupportContactCard';
import { design, palette, ui } from './ui';

export default function SettingsScreen() {
  const auth = useMobile();
  const navigation = useNavigation<any>();
  const contactSyncRef = useRef(auth.syncSupportContact);
  contactSyncRef.current = auth.syncSupportContact;
  useEffect(() => { void contactSyncRef.current(); }, []);
  return <ScrollView style={ui.page} contentContainerStyle={design.pageContent}>
    <View style={design.detailHero}>
      <View style={{ flex: 1, gap: 7 }}>
        <Text style={design.eyebrow}>个人中心</Text>
        <Text style={design.heroTitle}>{auth.overview?.user.username ?? auth.session?.user.username ?? '住户'}</Text>
        <Text style={design.heroSubtitle}>仅查看管理员授权给您的设备和提醒</Text>
      </View>
      <View style={[design.detailArt, { width: 86, height: 86, borderRadius: 43 }]}>
        <Ionicons name="person" size={40} color={palette.green} />
      </View>
    </View>
    <View style={design.detailSection}>
      <Text style={design.detailSectionTitle}>服务器连接</Text>
      <Text style={ui.body}>{auth.config?.baseUrl ?? '尚未配置'}</Text>
      {auth.config?.insecureLan && <Text style={ui.warning}>HTTP 开发模式：连接未加密</Text>}
      <Text style={ui.muted}>移动端使用独立授权，不使用 Web 管理员登录。</Text>
    </View>
    <TouchableOpacity accessibilityRole="button" style={design.ctaPurple} onPress={() => navigation.navigate('提交求助', {})}>
      <Text style={design.ctaPurpleText}>联系管理员 · 提交求助</Text>
    </TouchableOpacity>
    <TouchableOpacity accessibilityRole="button" style={[design.detailSection, { flexDirection: 'row', alignItems: 'center' }]}
      onPress={() => navigation.navigate('我的求助')}>
      <View style={[design.detailRowIcon, { backgroundColor: palette.purpleSoft }]}><Ionicons name="reader-outline" size={19} color={palette.purple} /></View>
      <View style={design.detailRowText}><Text style={design.detailRowTitle}>我的求助</Text>
        <Text style={design.detailRowSubtitle}>查看管理员处理进度</Text></View>
      <Ionicons name="chevron-forward" size={18} color="#8D9390" />
    </TouchableOpacity>
    <SupportContactCard />
    <View style={design.detailSection}>
      <Text style={design.detailSectionTitle}>客户端</Text>
      <TouchableOpacity accessibilityRole="button" style={design.detailRow} onPress={() => Alert.alert('注销此设备？',
        '本机凭据将立即删除。', [{ text: '取消', style: 'cancel' }, { text: '注销', style: 'destructive', onPress: () => void auth.logout() }])}>
        <View style={[design.detailRowIcon, { backgroundColor: '#F0EFEB' }]}><Ionicons name="log-out-outline" size={19} color="#606966" /></View>
        <View style={design.detailRowText}><Text style={design.detailRowTitle}>注销</Text><Text style={design.detailRowSubtitle}>移除此设备上的登录凭据</Text></View>
        <Ionicons name="chevron-forward" size={18} color="#8D9390" />
      </TouchableOpacity>
      <TouchableOpacity accessibilityRole="button" style={[design.detailRow, { borderBottomWidth: 0 }]} onPress={() => Alert.alert('重置此客户端？',
        '将注销并清除本机随机客户端标识。', [{ text: '取消', style: 'cancel' }, { text: '重置', style: 'destructive', onPress: () => void auth.resetClient() }])}>
        <View style={[design.detailRowIcon, { backgroundColor: palette.coralSoft }]}><Ionicons name="refresh-outline" size={19} color={palette.coral} /></View>
        <View style={design.detailRowText}><Text style={design.detailRowTitle}>重置此客户端</Text><Text style={design.detailRowSubtitle}>重新配对并生成新的设备标识</Text></View>
        <Ionicons name="chevron-forward" size={18} color="#8D9390" />
      </TouchableOpacity>
    </View>
    {auth.error && <Text accessibilityRole="alert" style={ui.warning}>{auth.error}</Text>}
  </ScrollView>;
}
