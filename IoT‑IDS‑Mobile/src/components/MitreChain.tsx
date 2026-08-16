import React from 'react';
import { View, Text, StyleSheet } from 'react-native';
import { colors } from '../theme';

interface Stage {
  name: string;
  mitre: string;
  desc: string;
  alert_count: number;
  active: boolean;
  intensity: number;
}

export default function MitreChain({ stages }: { stages: Stage[] }) {
  if (!stages || stages.length === 0) {
    return <Text style={styles.empty}>暂无攻击链数据</Text>;
  }

  return (
    <View>
      {stages.map((s, i) => (
        <View key={s.mitre} style={styles.stageRow}>
          <View style={styles.stageLeft}>
            <View style={[styles.dot, { backgroundColor: s.active ? colors.riskCritical : colors.textMuted }]} />
            {i < stages.length - 1 ? <View style={styles.connector} /> : null}
          </View>
          <View style={styles.stageBody}>
            <View style={styles.stageTop}>
              <Text style={[styles.stageName, s.active && { color: colors.textPrimary }]}>
                {s.name}
              </Text>
              <Text style={styles.stageCount}>
                {s.alert_count} 条告警
              </Text>
            </View>
            <Text style={styles.stageDesc}>{s.desc}</Text>
            <View style={styles.track}>
              <View
                style={[
                  styles.fill,
                  {
                    width: `${Math.max(0, Math.min(100, s.intensity))}%`,
                    backgroundColor: s.active ? colors.riskCritical : colors.textMuted,
                  },
                ]}
              />
            </View>
          </View>
        </View>
      ))}
    </View>
  );
}

const styles = StyleSheet.create({
  stageRow: {
    flexDirection: 'row',
  },
  stageLeft: {
    alignItems: 'center',
    width: 20,
  },
  dot: {
    width: 10,
    height: 10,
    borderRadius: 5,
    marginTop: 4,
  },
  connector: {
    flex: 1,
    width: 2,
    backgroundColor: colors.border,
    marginVertical: 2,
  },
  stageBody: {
    flex: 1,
    marginLeft: 8,
    paddingBottom: 14,
  },
  stageTop: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
  },
  stageName: {
    color: colors.textSecondary,
    fontSize: 14,
    fontWeight: '600',
  },
  stageCount: {
    color: colors.textMuted,
    fontSize: 12,
    fontVariant: ['tabular-nums'],
  },
  stageDesc: {
    color: colors.textMuted,
    fontSize: 11,
    marginTop: 2,
  },
  track: {
    height: 6,
    borderRadius: 3,
    backgroundColor: colors.bgSurface,
    marginTop: 6,
    overflow: 'hidden',
  },
  fill: {
    height: '100%',
    borderRadius: 3,
  },
  empty: {
    color: colors.textMuted,
    fontSize: 13,
    textAlign: 'center',
    paddingVertical: 16,
  },
});
