import React from 'react';
import { View, Text, StyleSheet } from 'react-native';
import {
  colors,
  riskColor,
  riskBg,
  riskLabel,
  statusLabel,
  statusColor,
} from '../theme';

function hexToRgba(hex: string, alpha = 0.14): string {
  const h = hex.replace('#', '');
  if (h.length !== 6) return `rgba(139,148,158,${alpha})`;
  const r = parseInt(h.slice(0, 2), 16);
  const g = parseInt(h.slice(2, 4), 16);
  const b = parseInt(h.slice(4, 6), 16);
  return `rgba(${r},${g},${b},${alpha})`;
}

export function Pill({
  text,
  color,
  bg,
}: {
  text: string;
  color: string;
  bg?: string;
}) {
  return (
    <View style={[styles.pill, { backgroundColor: bg ?? hexToRgba(color) }]}>
      <Text style={[styles.text, { color }]}>{text}</Text>
    </View>
  );
}

export function RiskPill({ level }: { level: string }) {
  return <Pill text={riskLabel(level)} color={riskColor(level)} bg={riskBg(level)} />;
}

export function StatusPill({ status }: { status: string }) {
  return <Pill text={statusLabel(status)} color={statusColor(status)} />;
}

const styles = StyleSheet.create({
  pill: {
    paddingHorizontal: 8,
    paddingVertical: 2,
    borderRadius: 4,
    alignSelf: 'flex-start',
  },
  text: {
    fontSize: 11,
    fontWeight: '600',
  },
});
