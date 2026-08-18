import React from 'react';
import { View, Text, StyleSheet } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { colors } from '../theme';

interface Props {
  label: string;
  value: string | number;
  color?: string;
  icon?: keyof typeof Ionicons.glyphMap;
  sub?: string;
}

export default function StatCard({ label, value, color, icon, sub }: Props) {
  const accent = color ?? colors.accentBlue;
  return (
    <View style={styles.card}>
      <View style={styles.top}>
        <Text style={styles.label}>{label}</Text>
        {icon ? (
          <Ionicons name={icon} size={16} color={accent} />
        ) : null}
      </View>
      <Text style={[styles.value, { color: accent }]}>{value}</Text>
      {sub ? <Text style={styles.sub}>{sub}</Text> : null}
    </View>
  );
}

const styles = StyleSheet.create({
  card: {
    backgroundColor: colors.bgElevated,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: 10,
    padding: 14,
    flex: 1,
    margin: 4,
  },
  top: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
  },
  label: {
    color: colors.textSecondary,
    fontSize: 12,
  },
  value: {
    fontSize: 26,
    fontWeight: '700',
    marginTop: 6,
  },
  sub: {
    color: colors.textMuted,
    fontSize: 11,
    marginTop: 2,
  },
});
