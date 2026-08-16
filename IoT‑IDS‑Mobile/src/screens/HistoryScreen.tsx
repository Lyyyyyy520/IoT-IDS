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
import { Ionicons } from '@expo/vector-icons';
import { api } from '../api';
import { colors } from '../theme';
import { formatDateTime } from '../utils/format';
import type { AlertItem, AuditLog } from '../types';
import AlertCard from '../components/AlertCard';
import EmptyState from '../components/EmptyState';
import LoadingView from '../components/LoadingView';
import { useAuth } from '../context/AuthContext';

const TIME_RANGES = [
  { key: '1h', label: '1 小时' },
  { key: '24h', label: '24 小时' },
  { key: '7d', label: '7 天' },
  { key: '', label: '全部' },
];

const ACTION_LABELS: Record<string, string> = {
  login: '登录',
  logout: '登出',
  login_failed: '登录失败',
  block_ip: '拉黑 IP',
  unblock_ip: '解除拉黑',
  trace_alert: '溯源分析',
  mark_fp: '标记误报',
  unmark_fp: '撤销误报',
  capture_start: '开始抓包',
  capture_stop: '停止抓包',
  update_config: '更新配置',
  create_policy: '新增策略',
  update_policy: '编辑策略',
  delete_policy: '删除策略',
};

export default function HistoryScreen() {
  const navigation = useNavigation<any>();
  const { isAdmin } = useAuth();
  const [tab, setTab] = useState<'alerts' | 'audit'>('alerts');

  // 告警历史状态
  const [items, setItems] = useState<AlertItem[]>([]);
  const [page, setPage] = useState(1);
  const [total, setTotal] = useState(0);
  const [timeRange, setTimeRange] = useState('24h');
  const [merged, setMerged] = useState(true);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // 审计日志状态
  const [logs, setLogs] = useState<AuditLog[]>([]);
  const [logsLoading, setLogsLoading] = useState(false);

  const loadAlerts = useCallback(
    async (pageNum: number, reset: boolean) => {
      setError(null);
      try {
        const d = await api.getAlerts({
          page: pageNum,
          page_size: 20,
          time_range: timeRange,
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
    [timeRange, merged],
  );

  const loadAudit = useCallback(async () => {
    setLogsLoading(true);
    try {
      const d = await api.getAuditLogs();
      setLogs(d.items ?? []);
    } catch {
      // ignore
    } finally {
      setLogsLoading(false);
    }
  }, []);

  useEffect(() => {
    setLoading(true);
    loadAlerts(1, true);
  }, [loadAlerts]);

  useEffect(() => {
    if (tab === 'audit') loadAudit();
  }, [tab, loadAudit]);

  const onRefresh = async () => {
    setRefreshing(true);
    if (tab === 'alerts') await loadAlerts(1, true);
    else await loadAudit();
    setRefreshing(false);
  };

  const onEndReached = () => {
    if (tab !== 'alerts' || loadingMore || loading) return;
    if (items.length >= total) return;
    setLoadingMore(true);
    loadAlerts(page + 1, false);
  };

  const renderAudit = ({ item }: { item: AuditLog }) => (
    <View style={styles.auditRow}>
      <View style={styles.auditIcon}>
        <Ionicons name="document-text-outline" size={16} color={colors.accentBlue} />
      </View>
      <View style={styles.auditBody}>
        <View style={styles.auditTop}>
          <Text style={styles.auditUser}>{item.username}</Text>
          <Text style={styles.auditAction}>{ACTION_LABELS[item.action] ?? item.action}</Text>
        </View>
        {item.detail ? <Text style={styles.auditDetail} numberOfLines={1}>{item.detail}</Text> : null}
        <Text style={styles.auditTime}>{formatDateTime(item.created_at)}</Text>
      </View>
    </View>
  );

  if (tab === 'alerts' && loading) return <LoadingView />;

  return (
    <View style={styles.screen}>
      <View style={styles.tabs}>
        <TouchableOpacity
          style={[styles.tab, tab === 'alerts' && styles.tabActive]}
          onPress={() => setTab('alerts')}
        >
          <Text style={[styles.tabText, tab === 'alerts' && styles.tabTextActive]}>告警历史</Text>
        </TouchableOpacity>
        {isAdmin ? (
          <TouchableOpacity
            style={[styles.tab, tab === 'audit' && styles.tabActive]}
            onPress={() => setTab('audit')}
          >
            <Text style={[styles.tabText, tab === 'audit' && styles.tabTextActive]}>审计日志</Text>
          </TouchableOpacity>
        ) : null}
      </View>

      {tab === 'alerts' ? (
        <FlatList
          data={items}
          keyExtractor={(item) => String(item.id)}
          renderItem={({ item }) => (
            <AlertCard
              item={item}
              onPress={() => navigation.navigate('AlertDetail', { item })}
            />
          )}
          contentContainerStyle={styles.content}
          refreshControl={
            <RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={colors.accentCyan} />
          }
          ListHeaderComponent={
            <View>
              <View style={styles.filterRow}>
                {TIME_RANGES.map((t) => (
                  <TouchableOpacity
                    key={t.key || 'all'}
                    style={[styles.chip, timeRange === t.key && styles.chipActive]}
                    onPress={() => setTimeRange(t.key)}
                  >
                    <Text style={[styles.chipText, timeRange === t.key && styles.chipTextActive]}>
                      {t.label}
                    </Text>
                  </TouchableOpacity>
                ))}
              </View>
              <View style={styles.toolbar}>
                <Text style={styles.total}>共 {total} 条记录</Text>
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
            <EmptyState icon="time-outline" title="暂无历史记录" message="该时间范围内没有告警记录" />
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
      ) : (
        <FlatList
          data={logs}
          keyExtractor={(item) => String(item.id)}
          renderItem={renderAudit}
          contentContainerStyle={styles.content}
          refreshControl={
            <RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={colors.accentCyan} />
          }
          ListEmptyComponent={
            logsLoading ? (
              <LoadingView />
            ) : (
              <EmptyState icon="document-text-outline" title="暂无审计日志" />
            )
          }
        />
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: colors.bgBase },
  tabs: {
    flexDirection: 'row',
    backgroundColor: colors.bgElevated,
    borderBottomWidth: 1,
    borderBottomColor: colors.border,
    paddingHorizontal: 12,
  },
  tab: {
    paddingVertical: 12,
    paddingHorizontal: 16,
    borderBottomWidth: 2,
    borderBottomColor: 'transparent',
  },
  tabActive: { borderBottomColor: colors.accentCyan },
  tabText: { color: colors.textSecondary, fontSize: 14 },
  tabTextActive: { color: colors.accentCyan, fontWeight: '700' },
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
  footer: { color: colors.textMuted, fontSize: 12, textAlign: 'center', paddingVertical: 14 },
  auditRow: {
    flexDirection: 'row',
    backgroundColor: colors.bgElevated,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: 10,
    padding: 12,
    marginBottom: 10,
  },
  auditIcon: {
    width: 34,
    height: 34,
    borderRadius: 17,
    backgroundColor: colors.bgSurface,
    alignItems: 'center',
    justifyContent: 'center',
  },
  auditBody: { flex: 1, marginLeft: 12 },
  auditTop: { flexDirection: 'row', justifyContent: 'space-between' },
  auditUser: { color: colors.textPrimary, fontSize: 14, fontWeight: '600' },
  auditAction: { color: colors.accentCyan, fontSize: 13 },
  auditDetail: { color: colors.textSecondary, fontSize: 12, marginTop: 4 },
  auditTime: { color: colors.textMuted, fontSize: 11, marginTop: 4 },
});
