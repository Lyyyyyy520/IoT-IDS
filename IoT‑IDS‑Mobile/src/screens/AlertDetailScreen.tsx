import React, { useState } from 'react';
import {
  View,
  Text,
  ScrollView,
  TouchableOpacity,
  StyleSheet,
  Alert,
  Modal,
  ActivityIndicator,
} from 'react-native';
import { useRoute } from '@react-navigation/native';
import { Ionicons } from '@expo/vector-icons';
import { api } from '../api';
import { colors, riskColor, riskLabel, attackTypeLabel, statusLabel } from '../theme';
import { formatDateTime, formatConfidence } from '../utils/format';
import type { AlertItem } from '../types';
import { useAuth } from '../context/AuthContext';
import { RiskPill, StatusPill } from '../components/Badge';

function DetailRow({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <View style={styles.detailRow}>
      <Text style={styles.detailLabel}>{label}</Text>
      <Text style={[styles.detailValue, color ? { color } : null]} numberOfLines={3}>
        {value || '--'}
      </Text>
    </View>
  );
}

export default function AlertDetailScreen() {
  const route = useRoute<any>();
  const { isAdmin } = useAuth();
  const initial: AlertItem = route.params?.item;
  const [status, setStatus] = useState(initial.status);
  const [busy, setBusy] = useState(false);
  const [traceInfo, setTraceInfo] = useState<string | null>(null);
  const [showTrace, setShowTrace] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  const run = async (label: string, fn: () => Promise<any>) => {
    setBusy(true);
    setMessage(null);
    try {
      const res = await fn();
      setMessage(res?.message || `${label}成功`);
      return res;
    } catch (e: any) {
      setMessage(e?.message || `${label}失败`);
      return null;
    } finally {
      setBusy(false);
    }
  };

  const confirm = (title: string, body: string, action: () => void) => {
    Alert.alert(title, body, [
      { text: '取消', style: 'cancel' },
      { text: '确定', onPress: action },
    ]);
  };

  const handleBlock = () => {
    if (status === 'blocked') {
      confirm('解除拉黑', `确定解除对 ${initial.src_ip} 的拉黑？`, async () => {
        const res = await run('解除拉黑', () => api.unblockIp(initial.id));
        if (res?.success) setStatus('reviewed');
      });
    } else {
      confirm('拉黑 IP', `确定拉黑 ${initial.src_ip} 并阻断其后续流量？`, async () => {
        const res = await run('拉黑', () => api.blockIp(initial.id));
        if (res?.success) setStatus('blocked');
      });
    }
  };

  const handleTrace = async () => {
    const res = await run('溯源', () => api.traceAlert(initial.id));
    if (res?.trace_info) {
      setTraceInfo(res.trace_info);
      setShowTrace(true);
      if (status === 'new') setStatus('reviewed');
    }
  };

  const handleFalsePositive = () => {
    if (status === 'false_positive') {
      confirm('撤销误报', '确定撤销该告警的误报标记？', async () => {
        const res = await run('撤销误报', () => api.unmarkFalsePositive(initial.id));
        if (res?.success) setStatus('reviewed');
      });
    } else {
      confirm('标记误报', '确定将该告警标记为误报？', async () => {
        const res = await run('标记误报', () => api.markFalsePositive(initial.id));
        if (res?.success) setStatus('false_positive');
      });
    }
  };

  const actionBtn = (label: string, icon: keyof typeof Ionicons.glyphMap, onPress: () => void, danger = false) => (
    <TouchableOpacity
      style={[styles.actionBtn, danger && styles.actionBtnDanger, busy && { opacity: 0.5 }]}
      onPress={onPress}
      disabled={busy}
    >
      <Ionicons name={icon} size={16} color={danger ? colors.riskCritical : colors.accentBlue} />
      <Text style={[styles.actionText, danger && { color: colors.riskCritical }]}>{label}</Text>
    </TouchableOpacity>
  );

  return (
    <ScrollView style={styles.screen} contentContainerStyle={styles.content}>
      <View style={[styles.hero, { borderLeftColor: riskColor(initial.risk_level) }]}>
        <View style={styles.heroTop}>
          <Text style={styles.attack}>{attackTypeLabel(initial.attack_type)}</Text>
          <RiskPill level={initial.risk_level} />
        </View>
        <Text style={styles.heroDesc}>{initial.description || '无描述'}</Text>
        <View style={styles.statusRow}>
          <StatusPill status={status} />
          <Text style={styles.time}>{formatDateTime(initial.timestamp)}</Text>
        </View>
      </View>

      <View style={styles.details}>
        <DetailRow label="告警编号" value={`#${initial.id}`} />
        <DetailRow label="源 IP" value={initial.src_ip} color={colors.accentBlue} />
        <DetailRow label="目标 IP" value={initial.dst_ip} color={colors.accentBlue} />
        <DetailRow
          label="端口"
          value={
            initial.src_port
              ? `${initial.src_port} → ${initial.dst_port ?? '--'}${initial.protocol ? ` · ${initial.protocol}` : ''}`
              : initial.protocol || '--'
          }
        />
        <DetailRow label="风险等级" value={riskLabel(initial.risk_level)} color={riskColor(initial.risk_level)} />
        <DetailRow label="置信度" value={formatConfidence(initial.confidence)} />
        <DetailRow label="重复次数" value={`${initial.merged_count}`} />
        <DetailRow label="当前状态" value={statusLabel(status)} />
        <DetailRow label="发现时间" value={formatDateTime(initial.timestamp)} />
      </View>

      {message ? <Text style={styles.message}>{message}</Text> : null}

      <View style={styles.actions}>
        {actionBtn('溯源分析', 'search', handleTrace)}
        {isAdmin ? actionBtn('标记误报', 'flag-outline', handleFalsePositive) : null}
        {isAdmin ? (
          status === 'blocked'
            ? actionBtn('解除拉黑', 'lock-open-outline', handleBlock)
            : actionBtn('拉黑 IP', 'ban', handleBlock, true)
        ) : null}
      </View>

      {!isAdmin ? (
        <Text style={styles.permissionHint}>溯源与处置操作仅管理员可执行</Text>
      ) : null}

      <Modal
        visible={showTrace}
        animationType="slide"
        transparent
        onRequestClose={() => setShowTrace(false)}
      >
        <View style={styles.modalOverlay}>
          <View style={styles.modalCard}>
            <View style={styles.modalHeader}>
              <Text style={styles.modalTitle}>溯源分析报告</Text>
              <TouchableOpacity onPress={() => setShowTrace(false)}>
                <Ionicons name="close" size={22} color={colors.textSecondary} />
              </TouchableOpacity>
            </View>
            <ScrollView style={{ maxHeight: 400 }}>
              <Text style={styles.traceText}>{traceInfo}</Text>
            </ScrollView>
          </View>
        </View>
      </Modal>

      {busy ? (
        <View style={styles.busyOverlay}>
          <ActivityIndicator size="large" color={colors.accentCyan} />
        </View>
      ) : null}
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: colors.bgBase },
  content: { padding: 12, paddingBottom: 32 },
  hero: {
    backgroundColor: colors.bgElevated,
    borderWidth: 1,
    borderColor: colors.border,
    borderLeftWidth: 4,
    borderRadius: 10,
    padding: 14,
    marginBottom: 12,
  },
  heroTop: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' },
  attack: { color: colors.textPrimary, fontSize: 20, fontWeight: '800' },
  heroDesc: { color: colors.textSecondary, fontSize: 13, marginTop: 8 },
  statusRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginTop: 10,
  },
  time: { color: colors.textMuted, fontSize: 12 },
  details: {
    backgroundColor: colors.bgElevated,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: 10,
    padding: 14,
    marginBottom: 12,
  },
  detailRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    paddingVertical: 6,
    borderBottomWidth: 1,
    borderBottomColor: colors.borderLight,
  },
  detailLabel: { color: colors.textSecondary, fontSize: 13, width: 80 },
  detailValue: {
    color: colors.textPrimary,
    fontSize: 13,
    flex: 1,
    textAlign: 'right',
    fontVariant: ['tabular-nums'],
  },
  message: {
    color: colors.riskLow,
    fontSize: 13,
    marginBottom: 10,
    textAlign: 'center',
  },
  actions: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 8,
  },
  actionBtn: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    backgroundColor: colors.bgElevated,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: 8,
    paddingHorizontal: 14,
    paddingVertical: 10,
  },
  actionBtnDanger: { borderColor: colors.riskCritical },
  actionText: { color: colors.accentBlue, fontSize: 13, fontWeight: '600' },
  permissionHint: { color: colors.textMuted, fontSize: 12, marginTop: 10, textAlign: 'center' },
  modalOverlay: {
    flex: 1,
    backgroundColor: 'rgba(0,0,0,0.6)',
    justifyContent: 'flex-end',
  },
  modalCard: {
    backgroundColor: colors.bgElevated,
    borderTopLeftRadius: 16,
    borderTopRightRadius: 16,
    padding: 20,
    paddingBottom: 32,
  },
  modalHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 12,
  },
  modalTitle: { color: colors.textPrimary, fontSize: 17, fontWeight: '700' },
  traceText: { color: colors.textPrimary, fontSize: 13, lineHeight: 20 },
  busyOverlay: {
    position: 'absolute',
    top: 0,
    left: 0,
    right: 0,
    bottom: 0,
    backgroundColor: 'rgba(0,0,0,0.3)',
    alignItems: 'center',
    justifyContent: 'center',
  },
});
