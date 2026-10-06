import React, { useEffect, useRef } from 'react';
import { Alert, Linking, Text, TouchableOpacity, View } from 'react-native';
import { useMobile } from './MobileContext';
import { palette, ui } from './ui';
import { safeEmailUrl, safePhoneUrl } from './supportContactUtils';

async function openSafe(url: string | null) {
  if (!url) { Alert.alert('联系方式不可用', '管理员提供的联系方式格式无效。'); return; }
  try {
    if (!(await Linking.canOpenURL(url))) { Alert.alert('无法打开', '此设备暂时无法打开相应联系应用。'); return; }
    await Linking.openURL(url);
  } catch { Alert.alert('无法打开', '请稍后重试，或通过其他方式联系管理员。'); }
}

export default function SupportContactCard() {
  const auth = useMobile();
  const contact = auth.supportContact;
  const syncRef = useRef(auth.syncSupportContact);
  syncRef.current = auth.syncSupportContact;
  useEffect(() => { if (!contact && !auth.supportContactError) void syncRef.current(); }, []);
  return <View style={ui.card}>
    <Text style={ui.cardTitle}>联系管理员</Text>
    {auth.supportContactStale && <Text accessibilityRole="alert" style={ui.warning}>联系方式可能已过期，请重新获取。</Text>}
    {!contact ? <Text style={ui.muted}>{auth.supportContactError ?? '正在获取公开联系方式…'}</Text> : !contact.available ?
      <Text style={ui.muted}>管理员尚未配置联系方式。</Text> : <>
        <Text style={ui.body}>{contact.display_name}</Text>
        {contact.working_hours && <Text style={ui.muted}>工作时间：{contact.working_hours}</Text>}
        {contact.public_note && <Text style={ui.muted}>{contact.public_note}</Text>}
        {contact.phone && <TouchableOpacity accessibilityRole="button" accessibilityLabel={`拨打管理员电话 ${contact.phone}`}
          style={ui.secondaryButton} onPress={() => void openSafe(safePhoneUrl(contact.phone!))}>
          <Text style={[ui.secondaryText, { color: palette.green }]}>拨打电话</Text>
        </TouchableOpacity>}
        {contact.email && <TouchableOpacity accessibilityRole="button" accessibilityLabel={`给管理员发送邮件 ${contact.email}`}
          style={ui.secondaryButton} onPress={() => void openSafe(safeEmailUrl(contact.email!))}>
          <Text style={[ui.secondaryText, { color: palette.green }]}>发送邮件</Text>
        </TouchableOpacity>}
        {!contact.phone && !contact.email && <Text style={ui.muted}>管理员尚未配置电话或邮箱。</Text>}
      </>}
    <TouchableOpacity accessibilityRole="button" style={ui.secondaryButton} onPress={() => void auth.syncSupportContact()}>
      <Text style={ui.secondaryText}>刷新联系方式</Text>
    </TouchableOpacity>
  </View>;
}
