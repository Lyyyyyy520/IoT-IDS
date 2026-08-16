import React from 'react';
import { View, Text, StyleSheet } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { colors } from '../theme';

interface Props {
  icon?: keyof typeof Ionicons.glyphMap;
  title?: string;
  message?: string;
}

export default function EmptyState({
  icon = 'file-tray-outline',
  title = '暂无数据',
  message,
}: Props) {
  return (
    <View style={styles.wrap}>
      <Ionicons name={icon} size={40} color={colors.textMuted} />
      <Text style={styles.title}>{title}</Text>
      {message ? <Text style={styles.message}>{message}</Text> : null}
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: {
    alignItems: 'center',
    justifyContent: 'center',
    paddingVertical: 40,
  },
  title: {
    color: colors.textSecondary,
    fontSize: 14,
    marginTop: 12,
  },
  message: {
    color: colors.textMuted,
    fontSize: 12,
    marginTop: 6,
    textAlign: 'center',
    paddingHorizontal: 32,
  },
});
