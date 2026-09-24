import React, { useEffect, useRef } from 'react';
import { Alert, ScrollView, Text, TouchableOpacity, View } from 'react-native';
import { useNavigation } from '@react-navigation/native';
import { useMobile } from './MobileContext';
import SupportContactCard from './SupportContactCard';
import { ui } from './ui';

export default function SettingsScreen() {
  const auth = useMobile();
  const navigation = useNavigation<any>();
  const contactSyncRef = useRef(auth.syncSupportContact);
  contactSyncRef.current = auth.syncSupportContact;
  useEffect(() => { void contactSyncRef.current(); }, []);
  return <ScrollView style={ui.page} contentContainerStyle={ui.content}>
    <Text style={ui.title}>设置</Text>
    <View style={ui.card}>
      <Text style={ui.cardTitle}>当前连接</Text>
      <Text style={ui.body}>{auth.config?.baseUrl ?? '尚未配置'}</Text>
      {auth.config?.insecureLan && <Text style={ui.warning}>HTTP 开发模式：连接未加密</Text>}
      <Text style={ui.muted}>仅显示本人授权范围，移动端不使用 Web 管理员登录。</Text>
    </View>
    <TouchableOpacity accessibilityRole="button" style={ui.button} onPress={() => navigation.navigate('提交求助', {})}>
      <Text style={ui.buttonText}>联系管理员 / 提交求助</Text>
    </TouchableOpacity>
    <TouchableOpacity accessibilityRole="button" style={ui.secondaryButton} onPress={() => navigation.navigate('我的求助')}>
      <Text style={ui.secondaryText}>查看我的求助进度</Text>
    </TouchableOpacity>
    <SupportContactCard />
    <TouchableOpacity accessibilityRole="button" style={ui.secondaryButton} onPress={() => Alert.alert('注销此设备？',
      '本机凭据将立即删除。', [{ text: '取消', style: 'cancel' }, { text: '注销', style: 'destructive', onPress: () => void auth.logout() }])}>
      <Text style={ui.secondaryText}>注销</Text>
    </TouchableOpacity>
    <TouchableOpacity accessibilityRole="button" style={ui.secondaryButton} onPress={() => Alert.alert('重置此客户端？',
      '将注销并清除本机随机客户端标识。', [{ text: '取消', style: 'cancel' }, { text: '重置', style: 'destructive', onPress: () => void auth.resetClient() }])}>
      <Text style={ui.secondaryText}>重置此客户端</Text>
    </TouchableOpacity>
    {auth.error && <Text accessibilityRole="alert" style={ui.warning}>{auth.error}</Text>}
  </ScrollView>;
}
