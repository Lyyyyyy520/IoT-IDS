import { useEffect, useState } from 'react';
import { View, Text, ScrollView, TouchableOpacity, StyleSheet } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { useNavigation } from '@react-navigation/native';
import { api } from '../api';
import { colors } from '../theme';
import { getBaseUrl } from '../api/client';
import { useAuth } from '../context/AuthContext';
import Card from '../components/Card';
import ServerConfigForm from '../components/ServerConfigForm';

export default function SettingsScreen() {
  const navigation = useNavigation<any>();
  const { user, isAdmin, logout } = useAuth();
  const [health, setHealth] = useState<{ ok: boolean; text: string } | null>(null);

  useEffect(() => {
    (async () => {
      try {
        const h = await api.health();
        setHealth({ ok: h.status === 'ok', text: h.status === 'ok' ? '后端连接正常' : '后端状态异常' });
      } catch {
        setHealth({ ok: false, text: '无法连接后端服务' });
      }
    })();
  }, []);

  const handleLogout = async () => {
    await logout();
    navigation.reset({ index: 0, routes: [{ name: 'Login' }] });
  };

  return (
    <ScrollView style={styles.screen} contentContainerStyle={styles.content}>
      <Card title="账号信息">
        <View style={styles.userRow}>
          <View style={styles.avatar}>
            <Text style={styles.avatarText}>{(user?.username ?? '?').charAt(0).toUpperCase()}</Text>
          </View>
          <View style={styles.userInfo}>
            <Text style={styles.username}>{user?.username}</Text>
            <Text style={styles.role}>{isAdmin ? '管理员' : '普通用户'}</Text>
          </View>
          <TouchableOpacity style={styles.logoutBtn} onPress={handleLogout}>
            <Ionicons name="log-out-outline" size={15} color={colors.riskCritical} />
            <Text style={styles.logoutText}>退出登录</Text>
          </TouchableOpacity>
        </View>
      </Card>

      <Card
        title="后端连接"
        right={
          health ? (
            <View style={styles.healthRow}>
              <View
                style={[styles.healthDot, { backgroundColor: health.ok ? colors.riskLow : colors.riskCritical }]}
              />
              <Text style={{ color: health.ok ? colors.riskLow : colors.riskCritical, fontSize: 12 }}>
                {health.text}
              </Text>
            </View>
          ) : null
        }
      >
        <Text style={styles.currentUrl}>当前地址：{getBaseUrl()}</Text>
        <View style={{ marginTop: 8 }}>
          <ServerConfigForm />
        </View>
      </Card>

      <Card title="关于">
        <View style={styles.aboutRow}>
          <Text style={styles.aboutLabel}>应用</Text>
          <Text style={styles.aboutValue}>IoT IDS 移动端</Text>
        </View>
        <View style={styles.aboutRow}>
          <Text style={styles.aboutLabel}>版本</Text>
          <Text style={styles.aboutValue}>1.0.0 · Expo SDK 54</Text>
        </View>
        <View style={styles.aboutRow}>
          <Text style={styles.aboutLabel}>功能</Text>
          <Text style={styles.aboutValue}>设备列表 · 数据监控 · 入侵告警 · 历史记录</Text>
        </View>
      </Card>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: colors.bgBase },
  content: { padding: 12, paddingBottom: 32 },
  userRow: { flexDirection: 'row', alignItems: 'center' },
  avatar: {
    width: 44,
    height: 44,
    borderRadius: 22,
    backgroundColor: colors.accentBlue,
    alignItems: 'center',
    justifyContent: 'center',
  },
  avatarText: { color: '#fff', fontSize: 18, fontWeight: '700' },
  userInfo: { flex: 1, marginLeft: 12 },
  username: { color: colors.textPrimary, fontSize: 16, fontWeight: '700' },
  role: { color: colors.textSecondary, fontSize: 12, marginTop: 2 },
  logoutBtn: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 4,
    paddingHorizontal: 12,
    paddingVertical: 8,
    borderRadius: 8,
    borderWidth: 1,
    borderColor: colors.riskCritical,
  },
  logoutText: { color: colors.riskCritical, fontSize: 13, fontWeight: '600' },
  healthRow: { flexDirection: 'row', alignItems: 'center', gap: 5 },
  healthDot: { width: 8, height: 8, borderRadius: 4 },
  currentUrl: { color: colors.textMuted, fontSize: 12 },
  aboutRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    paddingVertical: 6,
    borderBottomWidth: 1,
    borderBottomColor: colors.borderLight,
  },
  aboutLabel: { color: colors.textSecondary, fontSize: 13 },
  aboutValue: { color: colors.textPrimary, fontSize: 13 },
});
