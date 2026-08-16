import React from 'react';
import { View, Text, StyleSheet } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { colors, deviceTypeLabel } from '../theme';
import { relativeTime } from '../utils/format';
import type { Asset } from '../types';
import { RiskPill } from './Badge';

function deviceIcon(type: string): keyof typeof Ionicons.glyphMap {
  switch ((type || '').toLowerCase()) {
    case 'camera':
      return 'videocam';
    case 'door':
      return 'lock-closed';
    case 'sensor':
      return 'thermometer';
    case 'router':
      return 'wifi';
    case 'hub':
      return 'git-network';
    case 'socket':
      return 'flash';
    case 'lock':
      return 'key';
    case 'probe':
      return 'radio';
    default:
      return 'hardware-chip';
  }
}

function statusDot(status: string): string {
  if (status === 'online') return colors.riskLow;
  if (status === 'alert') return colors.riskCritical;
  return colors.textMuted;
}

export default function DeviceCard({ item }: { item: Asset }) {
  return (
    <View style={styles.card}>
      <View style={[styles.iconWrap, { borderColor: colors.border }]}>
        <Ionicons name={deviceIcon(item.device_type)} size={20} color={colors.accentCyan} />
      </View>

      <View style={styles.body}>
        <View style={styles.row}>
          <Text style={styles.name} numberOfLines={1}>{item.name}</Text>
          <RiskPill level={item.risk_level} />
        </View>
        <Text style={styles.type}>{deviceTypeLabel(item.device_type)}</Text>
        <View style={styles.ipRow}>
          <Text style={styles.ip}>{item.ip_address}</Text>
          {item.mac_address ? <Text style={styles.mac}>{item.mac_address}</Text> : null}
        </View>
      </View>

      <View style={styles.statusCol}>
        <View style={styles.statusRow}>
          <View style={[styles.dot, { backgroundColor: statusDot(item.status) }]} />
          <Text style={styles.statusText}>
            {item.status === 'online' ? '在线' : item.status === 'alert' ? '告警' : '离线'}
          </Text>
        </View>
        <Text style={styles.lastSeen}>{relativeTime(item.last_seen)}</Text>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  card: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: colors.bgElevated,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: 10,
    padding: 12,
    marginBottom: 10,
  },
  iconWrap: {
    width: 42,
    height: 42,
    borderRadius: 21,
    borderWidth: 1,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: colors.bgSurface,
  },
  body: {
    flex: 1,
    marginLeft: 12,
  },
  row: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
  },
  name: {
    color: colors.textPrimary,
    fontSize: 15,
    fontWeight: '600',
    flex: 1,
    marginRight: 8,
  },
  type: {
    color: colors.textSecondary,
    fontSize: 12,
    marginTop: 2,
  },
  ipRow: {
    flexDirection: 'row',
    marginTop: 4,
    flexWrap: 'wrap',
  },
  ip: {
    color: colors.accentBlue,
    fontSize: 12,
    fontVariant: ['tabular-nums'],
  },
  mac: {
    color: colors.textMuted,
    fontSize: 11,
    marginLeft: 8,
    fontVariant: ['tabular-nums'],
  },
  statusCol: {
    alignItems: 'flex-end',
    marginLeft: 8,
  },
  statusRow: {
    flexDirection: 'row',
    alignItems: 'center',
  },
  dot: {
    width: 8,
    height: 8,
    borderRadius: 4,
    marginRight: 5,
  },
  statusText: {
    color: colors.textSecondary,
    fontSize: 12,
  },
  lastSeen: {
    color: colors.textMuted,
    fontSize: 11,
    marginTop: 4,
  },
});
