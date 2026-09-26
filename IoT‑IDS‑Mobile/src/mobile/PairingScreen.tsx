import React, { useEffect, useState } from 'react';
import { KeyboardAvoidingView, Platform, ScrollView, Switch, Text, TextInput, TouchableOpacity, View } from 'react-native';
import { useMobile } from './MobileContext';
import { palette, ui } from './ui';

export function normalizePairingCode(input: string): string {
  return input.toUpperCase().replace(/[\s-]/g, '').replace(/[^A-HJ-NP-Z2-9]/g, '').slice(0, 32);
}
export function formatPairingCode(input: string): string {
  return normalizePairingCode(input).replace(/(.{4})(?=.)/g, '$1-');
}

export default function PairingScreen() {
  const auth = useMobile();
  const [address, setAddress] = useState(auth.config?.baseUrl ?? '');
  const [code, setCode] = useState('');
  const [name, setName] = useState('我的手机');
  const [insecureLan, setInsecureLan] = useState(auth.config?.insecureLan ?? false);
  const [localError, setLocalError] = useState<string | null>(null);
  useEffect(() => () => setCode(''), []);
  const submit = async () => {
    if (code.length !== 32 || !name.trim() || name.trim().length > 100) {
      setLocalError('请填写完整配对码和客户端名称'); return;
    }
    setLocalError(null);
    const success = await auth.pair(address, insecureLan, code, name);
    if (success) setCode('');
  };
  return <KeyboardAvoidingView style={ui.page} behavior={Platform.OS === 'ios' ? 'padding' : undefined}>
    <ScrollView contentContainerStyle={ui.content} keyboardShouldPersistTaps="handled">
      <Text style={ui.title}>连接我的设备</Text>
      <Text style={ui.subtitle}>请向管理员获取一次性配对码。配对后仅能查看授权给您的设备。</Text>
      <View style={ui.card}>
        <Text style={ui.label}>服务器地址</Text>
        <TextInput accessibilityLabel="服务器地址" style={ui.input} value={address}
          onChangeText={setAddress} placeholder="https://example.com" autoCapitalize="none"
          autoCorrect={false} keyboardType="url" />
        {__DEV__ && <View style={{ flexDirection: 'row', alignItems: 'center', gap: 10 }}>
          <Switch accessibilityLabel="隔离局域网 HTTP 开发模式" value={insecureLan} onValueChange={setInsecureLan} />
          <Text style={ui.body}>隔离局域网 HTTP 开发模式</Text>
        </View>}
        {insecureLan && <Text style={ui.warning}>HTTP 不加密，仅供隔离局域网开发测试；请勿在公网使用。</Text>}
        <Text style={ui.label}>配对码</Text>
        <TextInput accessibilityLabel="配对码" style={[ui.input, { fontFamily: Platform.OS === 'ios' ? 'Menlo' : 'monospace' }]}
          value={formatPairingCode(code)} onChangeText={value => setCode(normalizePairingCode(value))}
          autoCapitalize="characters" autoCorrect={false} maxLength={39} />
        <Text style={ui.label}>客户端名称</Text>
        <TextInput accessibilityLabel="客户端名称" style={ui.input} value={name} onChangeText={setName} maxLength={100} />
        {(localError || auth.error) && <Text accessibilityRole="alert" style={ui.warning}>
          {localError || auth.error}{auth.requestId ? `（请求编号：${auth.requestId}）` : ''}
        </Text>}
        <TouchableOpacity accessibilityRole="button" disabled={auth.busy} style={[ui.button, auth.busy && { opacity: 0.5 }]} onPress={submit}>
          <Text style={ui.buttonText}>{auth.busy ? '正在配对…' : '安全配对'}</Text>
        </TouchableOpacity>
      </View>
      <Text style={ui.muted}>配对码不会保存在手机中。请确认地址来自管理员；不会忽略 TLS 证书错误。</Text>
    </ScrollView>
  </KeyboardAvoidingView>;
}
