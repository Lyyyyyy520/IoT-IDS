import React from 'react';
import { View, Text, StyleSheet } from 'react-native';
import { colors } from '../theme';
import type { AttackDistributionItem } from '../types';

const TYPE_COLORS: Record<string, string> = {
  Mirai: colors.riskCritical,
  Gafgyt: colors.riskHigh,
  其他攻击: colors.riskMedium,
  正常流量: colors.riskLow,
  端口扫描: colors.accentBlue,
  暴力破解: colors.accentPurple,
  Ddos: colors.riskHigh,
};

export default function DistributionBars({ data }: { data: AttackDistributionItem[] }) {
  if (!data || data.length === 0) {
    return <Text style={styles.empty}>暂无攻击分布数据</Text>;
  }

  const max = Math.max(1, ...data.map((d) => d.count));

  return (
    <View>
      {data.map((d, i) => (
        <View key={i} style={styles.row}>
          <Text style={styles.label} numberOfLines={1}>{d.type}</Text>
          <View style={styles.track}>
            <View
              style={[
                styles.fill,
                {
                  flex: d.count,
                  backgroundColor: TYPE_COLORS[d.type] ?? colors.accentCyan,
                },
              ]}
            />
            <View style={{ flex: max - d.count }} />
          </View>
          <Text style={styles.count}>{d.count}</Text>
        </View>
      ))}
    </View>
  );
}

const styles = StyleSheet.create({
  row: {
    flexDirection: 'row',
    alignItems: 'center',
    marginVertical: 5,
  },
  label: {
    width: 82,
    color: colors.textSecondary,
    fontSize: 12,
  },
  track: {
    flex: 1,
    flexDirection: 'row',
    height: 12,
    borderRadius: 6,
    overflow: 'hidden',
    backgroundColor: colors.bgSurface,
  },
  fill: {
    height: '100%',
    borderRadius: 6,
  },
  count: {
    width: 42,
    textAlign: 'right',
    color: colors.textPrimary,
    fontSize: 12,
    fontVariant: ['tabular-nums'],
  },
  empty: {
    color: colors.textMuted,
    fontSize: 13,
    textAlign: 'center',
    paddingVertical: 16,
  },
});
