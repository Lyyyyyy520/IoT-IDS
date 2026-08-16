import React, { useCallback, useEffect, useState } from 'react';
import {
  View,
  Text,
  FlatList,
  TouchableOpacity,
  RefreshControl,
  StyleSheet,
} from 'react-native';
import { useNavigation } from '@react-navigation/native';
import { api } from '../api';
import { colors } from '../theme';
import type { AlertItem } from '../types';
import AlertCard from '../components/AlertCard';
import EmptyState from '../components/EmptyState';
import LoadingView from '../components/LoadingView';

const RISK_FILTERS = [
  { key: '', label: '全部' },
  { key: 'critical', label: '高危' },
  { key: 'high', label: '中危' },
  { key: 'medium', label: '低危' },
  { key: 'low', label: '正常' },
];

export default function AlertsScreen() {
  const navigation = useNavigation<any>();
  const [items, setItems] = useState<AlertItem[]>([]);
  const [page, setPage] = useState(1);
  const [total, setTotal] = useState(0);
  const [risk, setRisk] = useState('');
  const [merged, setMerged] = useState(false);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [maxId, setMaxId] = useState(0);

  const load = useCallback(
    async (pageNum: number, reset: boolean) => {
      setError(null);
      try {
        const d = await api.getAlerts({
          page: pageNum,
          page_size: 20,
          risk_level: risk,
          merged,
        });
        setTotal(d.total);
        setItems((prev) => (reset ? d.items : [...prev, ...d.items]));
        setPage(pageNum);
      } catch (e: any) {
        setError(e?.message || '加载失败');
      } finally {
        setLoading(false);
        setLoadingMore(false);
      }
    },
    [risk, merged],
  );

  // 初始获取 max_id 用于实时轮询
  useEffect(() => {
    api.getNewAlerts(0).then((d) => setMaxId(d.max_id ?? 0)).catch(() => {});
  }, []);

  useEffect(() => {
    setLoading(true);
    load(1, true);
  }, [load]);

  // 无筛选时轮询新告警
  useEffect(() => {
    if (risk || merged) return;
    const timer = setInterval(async () => {
      try {
        const d = await api.getNewAlerts(maxId);
        if (d.items && d.items.length > 0) {
          setItems((prev) => {
            const seen = new Set(prev.map((i) => i.id));
            const fresh = d.items.filter((i) => !seen.has(i.id));
            return [...fresh, ...prev];
          });
        }
        if (d.max_id) setMaxId(d.max_id);
      } catch {
        // ignore
      }
    }, 10000);
    return () => clearInterval(timer);
  }, [maxId, risk, merged]);

  const onRefresh = async () => {
    setRefreshing(true);
    await load(1, true);
    setRefreshing(false);
  };

  const onEndReached = () => {
    if (loadingMore || loading) return;
    if (items.length >= total) return;
    setLoadingMore(true);
    load(page + 1, false);
  };

  if (loading) return <LoadingView />;

  return (
    <FlatList
      style={styles.screen}
      contentContainerStyle={styles.content}
      data={items}
      keyExtractor={(item) => String(item.id)}
      renderItem={({ item }) => (
        <AlertCard
          item={item}
          onPress={() => navigation.navigate('AlertDetail', { item })}
        />
      )}
      refreshControl={
        <RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={colors.accentCyan} />
      }
      ListHeaderComponent={
        <View>
          <View style={styles.filterRow}>
            {RISK_FILTERS.map((f) => (
              <TouchableOpacity
                key={f.key}
                style={[styles.chip, risk === f.key && styles.chipActive]}
                onPress={() => setRisk(f.key)}
              >
                <Text style={[styles.chipText, risk === f.key && styles.chipTextActive]}>
                  {f.label}
                </Text>
              </TouchableOpacity>
            ))}
          </View>
          <View style={styles.toolbar}>
            <Text style={styles.total}>共 {total} 条告警</Text>
            <TouchableOpacity
              style={[styles.mergeBtn, merged && styles.mergeBtnActive]}
              onPress={() => setMerged(!merged)}
            >
              <Text style={[styles.mergeText, merged && styles.mergeTextActive]}>
                {merged ? '已合并' : '合并显示'}
              </Text>
            </TouchableOpacity>
          </View>
          {error ? (
            <View style={styles.errorBox}>
              <Text style={styles.errorText}>{error}</Text>
            </View>
          ) : null}
        </View>
      }
      ListEmptyComponent={
        <EmptyState icon="warning-outline" title="暂无告警" message="当前筛选条件下没有告警记录" />
      }
      ListFooterComponent={
        loadingMore ? (
          <Text style={styles.footer}>加载中…</Text>
        ) : items.length >= total ? (
          <Text style={styles.footer}>已全部加载</Text>
        ) : null
      }
      onEndReached={onEndReached}
      onEndReachedThreshold={0.3}
    />
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: colors.bgBase },
  content: { padding: 12, paddingBottom: 24, flexGrow: 1 },
  filterRow: { flexDirection: 'row', flexWrap: 'wrap', gap: 8, marginBottom: 8 },
  chip: {
    paddingHorizontal: 12,
    paddingVertical: 6,
    borderRadius: 16,
    backgroundColor: colors.bgElevated,
    borderWidth: 1,
    borderColor: colors.border,
  },
  chipActive: { backgroundColor: colors.accentBlue, borderColor: colors.accentBlue },
  chipText: { color: colors.textSecondary, fontSize: 13 },
  chipTextActive: { color: '#fff', fontWeight: '600' },
  toolbar: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 10,
  },
  total: { color: colors.textSecondary, fontSize: 12 },
  mergeBtn: {
    paddingHorizontal: 10,
    paddingVertical: 4,
    borderRadius: 6,
    borderWidth: 1,
    borderColor: colors.border,
  },
  mergeBtnActive: { backgroundColor: colors.accentCyan, borderColor: colors.accentCyan },
  mergeText: { color: colors.textSecondary, fontSize: 12 },
  mergeTextActive: { color: colors.bgBase, fontWeight: '700' },
  errorBox: {
    backgroundColor: colors.bgElevated,
    borderWidth: 1,
    borderColor: colors.riskCritical,
    borderRadius: 8,
    padding: 10,
    marginBottom: 10,
  },
  errorText: { color: colors.riskCritical, fontSize: 13 },
  footer: {
    color: colors.textMuted,
    fontSize: 12,
    textAlign: 'center',
    paddingVertical: 14,
  },
});
