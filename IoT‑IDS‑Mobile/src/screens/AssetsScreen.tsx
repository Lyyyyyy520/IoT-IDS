import React, { useCallback, useEffect, useState } from 'react';
import { View, Text, ScrollView, RefreshControl, StyleSheet } from 'react-native';
import { api } from '../api';
import { colors } from '../theme';
import type { Asset } from '../types';
import DeviceCard from '../components/DeviceCard';
import EmptyState from '../components/EmptyState';
import LoadingView from '../components/LoadingView';

export default function AssetsScreen() {
  const [assets, setAssets] = useState<Asset[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      const d = await api.getAssets();
      setAssets(d.items ?? []);
    } catch (e: any) {
      setError(e?.message || '加载失败');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const onRefresh = async () => {
    setRefreshing(true);
    await load();
    setRefreshing(false);
  };

  const online = assets.filter((a) => a.status === 'online').length;
  const alert = assets.filter((a) => a.status === 'alert').length;

  if (loading) return <LoadingView />;

  return (
    <ScrollView
      style={styles.screen}
      contentContainerStyle={styles.content}
      refreshControl={
        <RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={colors.accentCyan} />
      }
    >
      <View style={styles.summary}>
        <View style={styles.summaryItem}>
          <Text style={styles.summaryValue}>{assets.length}</Text>
          <Text style={styles.summaryLabel}>设备总数</Text>
        </View>
        <View style={styles.summaryItem}>
          <Text style={[styles.summaryValue, { color: colors.riskLow }]}>{online}</Text>
          <Text style={styles.summaryLabel}>在线</Text>
        </View>
        <View style={styles.summaryItem}>
          <Text style={[styles.summaryValue, { color: colors.riskCritical }]}>{alert}</Text>
          <Text style={styles.summaryLabel}>告警</Text>
        </View>
        <View style={styles.summaryItem}>
          <Text style={[styles.summaryValue, { color: colors.textMuted }]}>
            {assets.length - online}
          </Text>
          <Text style={styles.summaryLabel}>离线</Text>
        </View>
      </View>

      {error ? (
        <EmptyState icon="cloud-offline-outline" title="加载失败" message={error} />
      ) : assets.length === 0 ? (
        <EmptyState icon="hardware-chip-outline" title="暂无设备" message="后端尚未注册任何 IoT 设备" />
      ) : (
        assets.map((a) => <DeviceCard key={a.id} item={a} />)
      )}
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  screen: {
    flex: 1,
    backgroundColor: colors.bgBase,
  },
  content: {
    padding: 12,
    paddingBottom: 24,
  },
  summary: {
    flexDirection: 'row',
    backgroundColor: colors.bgElevated,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: 10,
    paddingVertical: 14,
    marginBottom: 12,
  },
  summaryItem: {
    flex: 1,
    alignItems: 'center',
  },
  summaryValue: {
    color: colors.textPrimary,
    fontSize: 20,
    fontWeight: '700',
    fontVariant: ['tabular-nums'],
  },
  summaryLabel: {
    color: colors.textSecondary,
    fontSize: 11,
    marginTop: 2,
  },
});
