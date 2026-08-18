import React from 'react';
import { View, Text, StyleSheet, TouchableOpacity } from 'react-native';
import { colors, riskColor, attackTypeLabel } from '../theme';
import { formatDateTime, formatConfidence } from '../utils/format';
import type { AlertItem } from '../types';
import { RiskPill, StatusPill } from './Badge';

interface Props {
  item: AlertItem;
  onPress?: () => void;
}

export default function AlertCard({ item, onPress }: Props) {
  return (
    <TouchableOpacity
      style={[styles.card, { borderLeftColor: riskColor(item.risk_level) }]}
      onPress={onPress}
      activeOpacity={0.7}
    >
      <View style={styles.row}>
        <Text style={styles.attack}>{attackTypeLabel(item.attack_type)}</Text>
        <RiskPill level={item.risk_level} />
      </View>

      <View style={styles.ipRow}>
        <Text style={styles.ip} numberOfLines={1}>{item.src_ip}</Text>
        <Text style={styles.arrow}>→</Text>
        <Text style={styles.ip} numberOfLines={1}>{item.dst_ip}</Text>
      </View>

      <View style={styles.metaRow}>
        <Text style={styles.meta}>
          置信度 {formatConfidence(item.confidence)}
          {item.merged_count > 1 ? ` · 合并 ${item.merged_count} 次` : ''}
        </Text>
        <Text style={styles.meta}>{formatDateTime(item.timestamp)}</Text>
      </View>

      <View style={styles.bottomRow}>
        {item.description ? (
          <Text style={styles.desc} numberOfLines={1}>{item.description}</Text>
        ) : <View />}
        <StatusPill status={item.status} />
      </View>
    </TouchableOpacity>
  );
}

const styles = StyleSheet.create({
  card: {
    backgroundColor: colors.bgElevated,
    borderWidth: 1,
    borderColor: colors.border,
    borderLeftWidth: 4,
    borderRadius: 10,
    padding: 12,
    marginBottom: 10,
  },
  row: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
  },
  attack: {
    color: colors.textPrimary,
    fontSize: 15,
    fontWeight: '700',
  },
  ipRow: {
    flexDirection: 'row',
    alignItems: 'center',
    marginTop: 6,
  },
  ip: {
    color: colors.accentBlue,
    fontSize: 13,
    fontVariant: ['tabular-nums'],
    flexShrink: 1,
  },
  arrow: {
    color: colors.textMuted,
    marginHorizontal: 8,
    fontSize: 13,
  },
  metaRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    marginTop: 6,
  },
  meta: {
    color: colors.textSecondary,
    fontSize: 12,
  },
  bottomRow: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    marginTop: 8,
  },
  desc: {
    color: colors.textMuted,
    fontSize: 12,
    flex: 1,
    marginRight: 8,
  },
});
