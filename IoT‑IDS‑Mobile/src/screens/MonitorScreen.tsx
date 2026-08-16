import React, { useCallback, useEffect, useState } from 'react';
import {
  View,
  Text,
  ScrollView,
  RefreshControl,
  TouchableOpacity,
  StyleSheet,
  ActivityIndicator,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { api } from '../api';
import { colors } from '../theme';
import { formatTime, formatNumber } from '../utils/format';
import type { TrafficLog, CaptureStatus, ProbeStatus } from '../types';
import Card from '../components/Card';
import EmptyState from '../components/EmptyState';
import LoadingView from '../components/LoadingView';
import { useAuth } from '../context/AuthContext';

export default function MonitorScreen() {
  const { isAdmin } = useAuth();
  const [capture, setCapture] = useState<CaptureStatus | null>(null);
  const [probe, setProbe] = useState<ProbeStatus | null>(null);
  const [logs, setLogs] = useState<TrafficLog[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      const [c, p, t] = await Promise.all([
        api.getCaptureStatus().catch(() => null),
        api.getProbeStatus().catch(() => null),
        api.getTrafficLogs().catch(() => null),
      ]);
      setCapture(c);
      setProbe(p);
      if (t) setLogs(t.items ?? []);
    } catch (e: any) {
      setError(e?.message || '加载失败');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
    const timer = setInterval(async () => {
      try {
        const [c, t] = await Promise.all([
          api.getCaptureStatus().catch(() => null),
          api.getTrafficLogs().catch(() => null),
        ]);
        if (c) setCapture(c);
        if (t) setLogs(t.items ?? []);
      } catch {
        // ignore polling errors
      }
    }, 5000);
    return () => clearInterval(timer);
  }, [load]);

  const onRefresh = async () => {
    setRefreshing(true);
    await load();
    setRefreshing(false);
  };

  const toggleCapture = async () => {
    setBusy(true);
    try {
      const running = capture?.running;
      const res = running ? await api.stopCapture() : await api.startCapture();
      setCapture(res as CaptureStatus);
    } catch (e: any) {
      setError(e?.message || '操作失败');
    } finally {
      setBusy(false);
    }
  };

  if (loading) return <LoadingView />;

  const running = capture?.running;

  return (
    <ScrollView
      style={styles.screen}
      contentContainerStyle={styles.content}
      refreshControl={
        <RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={colors.accentCyan} />
      }
    >
      {error ? (
        <View style={styles.banner}>
          <Ionicons name="cloud-offline-outline" size={16} color={colors.riskCritical} />
          <Text style={styles.bannerText}>{error}</Text>
        </View>
      ) : null}

      <Card title="实时抓包检测">
        <View style={styles.captureRow}>
          <View style={styles.captureStatus}>
            <View style={[styles.dot, { backgroundColor: running ? colors.riskLow : colors.textMuted }]} />
            <Text style={styles.captureState}>
              {running ? '检测运行中' : '检测已停止'}
            </Text>
            <Text style={styles.captureMode}>
              {capture?.mode ? `模式: ${capture.mode}` : ''}
            </Text>
          </View>
          <View style={styles.captureStats}>
            <Text style={styles.captureStatValue}>{formatNumber(capture?.packet_count)}</Text>
            <Text style={styles.captureStatLabel}>已捕获包</Text>
          </View>
          <View style={styles.captureStats}>
            <Text style={[styles.captureStatValue, { color: colors.riskCritical }]}>
              {formatNumber(capture?.attack_count)}
            </Text>
            <Text style={styles.captureStatLabel}>攻击包</Text>
          </View>
        </View>

        {isAdmin ? (
          <TouchableOpacity
            style={[
              styles.controlBtn,
              { backgroundColor: running ? colors.riskCritical : colors.riskLow },
              busy && { opacity: 0.6 },
            ]}
            onPress={toggleCapture}
            disabled={busy}
          >
            {busy ? (
              <ActivityIndicator size="small" color="#fff" />
            ) : (
              <>
                <Ionicons name={running ? 'stop' : 'play'} size={16} color="#fff" />
                <Text style={styles.controlText}>{running ? '停止检测' : '开始检测'}</Text>
              </>
            )}
          </TouchableOpacity>
        ) : (
          <Text style={styles.permissionHint}>仅管理员可控制抓包检测</Text>
        )}
      </Card>

      {probe ? (
        <Card title="探针节点">
          <View style={styles.probeRow}>
            <View style={styles.probeItem}>
              <Text style={styles.probeValue}>{probe.total_probes}</Text>
              <Text style={styles.probeLabel}>节点总数</Text>
            </View>
            <View style={styles.probeItem}>
              <Text style={[styles.probeValue, { color: colors.riskLow }]}>{probe.online_probes}</Text>
              <Text style={styles.probeLabel}>在线</Text>
            </View>
            <View style={styles.probeItem}>
              <Text style={[styles.probeValue, { color: colors.textMuted }]}>{probe.offline_probes}</Text>
              <Text style={styles.probeLabel}>离线</Text>
            </View>
            <View style={styles.probeItem}>
              <Text style={[styles.probeValue, { color: colors.riskCritical }]}>{probe.alerts_from_probes}</Text>
              <Text style={styles.probeLabel}>上报告警</Text>
            </View>
          </View>
        </Card>
      ) : null}

      <Card title="实时流量日志">
        {logs.length === 0 ? (
          <EmptyState icon="swap-vertical-outline" title="暂无流量" message="等待抓包或探针上报流量数据" />
        ) : (
          logs.map((l) => (
            <View key={l.id} style={styles.logRow}>
              <Text style={styles.logTime}>{formatTime(l.timestamp)}</Text>
              <View style={styles.logFlow}>
                <Text style={styles.logIp} numberOfLines={1}>{l.src_ip}:{l.src_port ?? 0}</Text>
                <Text style={styles.logArrow}>→</Text>
                <Text style={styles.logIp} numberOfLines={1}>{l.dst_ip}:{l.dst_port ?? 0}</Text>
              </View>
              <View style={styles.logRight}>
                <Text style={styles.logProto}>{l.protocol || '--'}</Text>
                <Text style={styles.logLen}>{l.length ?? 0}B</Text>
              </View>
            </View>
          ))
        )}
      </Card>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: colors.bgBase },
  content: { padding: 12, paddingBottom: 24 },
  banner: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    backgroundColor: colors.bgElevated,
    borderWidth: 1,
    borderColor: colors.riskCritical,
    borderRadius: 8,
    padding: 10,
    marginBottom: 12,
  },
  bannerText: { color: colors.riskCritical, fontSize: 13, flex: 1 },
  captureRow: {
    flexDirection: 'row',
    alignItems: 'center',
    marginBottom: 14,
  },
  captureStatus: { flex: 1 },
  captureState: { color: colors.textPrimary, fontSize: 15, fontWeight: '600', marginTop: 4 },
  captureMode: { color: colors.textMuted, fontSize: 11, marginTop: 2 },
  dot: { width: 10, height: 10, borderRadius: 5 },
  captureStats: { alignItems: 'center', marginLeft: 16 },
  captureStatValue: { color: colors.textPrimary, fontSize: 18, fontWeight: '700', fontVariant: ['tabular-nums'] },
  captureStatLabel: { color: colors.textSecondary, fontSize: 11 },
  controlBtn: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 6,
    borderRadius: 8,
    paddingVertical: 11,
  },
  controlText: { color: '#fff', fontSize: 14, fontWeight: '700' },
  permissionHint: { color: colors.textMuted, fontSize: 12, textAlign: 'center' },
  probeRow: { flexDirection: 'row' },
  probeItem: { flex: 1, alignItems: 'center' },
  probeValue: { color: colors.textPrimary, fontSize: 18, fontWeight: '700', fontVariant: ['tabular-nums'] },
  probeLabel: { color: colors.textSecondary, fontSize: 11, marginTop: 2 },
  logRow: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingVertical: 8,
    borderBottomWidth: 1,
    borderBottomColor: colors.borderLight,
  },
  logTime: { color: colors.textMuted, fontSize: 11, width: 52, fontVariant: ['tabular-nums'] },
  logFlow: { flex: 1, flexDirection: 'row', alignItems: 'center' },
  logIp: { color: colors.accentBlue, fontSize: 12, flexShrink: 1, fontVariant: ['tabular-nums'] },
  logArrow: { color: colors.textMuted, marginHorizontal: 6, fontSize: 12 },
  logRight: { alignItems: 'flex-end', marginLeft: 8 },
  logProto: { color: colors.textSecondary, fontSize: 12 },
  logLen: { color: colors.textMuted, fontSize: 11 },
});
