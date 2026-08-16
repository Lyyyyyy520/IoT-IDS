import React, { useState } from 'react';
import {
  View,
  Text,
  TextInput,
  TouchableOpacity,
  StyleSheet,
  ActivityIndicator,
} from 'react-native';
import { colors } from '../theme';
import { getBaseUrl, setBaseUrl, getDefaultBaseUrl } from '../api/client';

export default function ServerConfigForm({ onSaved }: { onSaved?: () => void }) {
  const [url, setUrl] = useState(getBaseUrl());
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [result, setResult] = useState<{ ok: boolean; text: string } | null>(null);

  const cleaned = url.trim().replace(/\/+$/, '');

  const handleSave = async () => {
    setSaving(true);
    await setBaseUrl(cleaned);
    setSaving(false);
    setResult({ ok: true, text: '地址已保存' });
    onSaved?.();
  };

  const handleTest = async () => {
    setTesting(true);
    setResult(null);
    try {
      const res = await fetch(`${cleaned}/health`);
      const data = await res.json();
      if (res.ok && data.status === 'ok') {
        setResult({ ok: true, text: `连接成功 · 模型${data.model_loaded ? '已' : '未'}加载` });
      } else {
        setResult({ ok: false, text: `响应异常 (${res.status})` });
      }
    } catch (e: any) {
      setResult({ ok: false, text: `连接失败: ${e?.message ?? '网络错误'}` });
    } finally {
      setTesting(false);
    }
  };

  const handleReset = () => {
    setUrl(getDefaultBaseUrl());
    setResult(null);
  };

  return (
    <View>
      <Text style={styles.hint}>
        后端地址（原网页 Flask 服务），默认端口 5000，API 前缀 /api
      </Text>
      <TextInput
        style={styles.input}
        value={url}
        onChangeText={setUrl}
        placeholder="http://192.168.1.100:5000/api"
        placeholderTextColor={colors.textMuted}
        autoCapitalize="none"
        autoCorrect={false}
        keyboardType="url"
      />

      {result ? (
        <Text style={[styles.result, { color: result.ok ? colors.riskLow : colors.riskCritical }]}>
          {result.text}
        </Text>
      ) : null}

      <View style={styles.btnRow}>
        <TouchableOpacity style={[styles.btn, styles.btnGhost]} onPress={handleReset} disabled={testing || saving}>
          <Text style={styles.btnGhostText}>恢复默认</Text>
        </TouchableOpacity>
        <TouchableOpacity style={[styles.btn, styles.btnGhost]} onPress={handleTest} disabled={testing || saving}>
          {testing ? <ActivityIndicator size="small" color={colors.accentCyan} /> : <Text style={styles.btnGhostText}>测试连接</Text>}
        </TouchableOpacity>
        <TouchableOpacity style={[styles.btn, styles.btnPrimary]} onPress={handleSave} disabled={saving}>
          {saving ? <ActivityIndicator size="small" color="#fff" /> : <Text style={styles.btnPrimaryText}>保存</Text>}
        </TouchableOpacity>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  hint: {
    color: colors.textSecondary,
    fontSize: 12,
    marginBottom: 8,
  },
  input: {
    backgroundColor: colors.bgBase,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: 8,
    color: colors.textPrimary,
    paddingHorizontal: 12,
    paddingVertical: 10,
    fontSize: 14,
    fontVariant: ['tabular-nums'],
  },
  result: {
    fontSize: 13,
    marginTop: 10,
  },
  btnRow: {
    flexDirection: 'row',
    marginTop: 14,
    gap: 8,
  },
  btn: {
    flex: 1,
    borderRadius: 8,
    paddingVertical: 10,
    alignItems: 'center',
    justifyContent: 'center',
  },
  btnGhost: {
    backgroundColor: colors.bgSurface,
    borderWidth: 1,
    borderColor: colors.border,
  },
  btnGhostText: {
    color: colors.textPrimary,
    fontSize: 13,
  },
  btnPrimary: {
    backgroundColor: colors.accentBlue,
  },
  btnPrimaryText: {
    color: '#fff',
    fontSize: 13,
    fontWeight: '600',
  },
});
