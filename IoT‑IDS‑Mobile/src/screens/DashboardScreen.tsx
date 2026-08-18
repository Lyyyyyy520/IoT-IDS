import React, { useCallback, useEffect, useState } from 'react';
import {
  View,
  Text,
  ScrollView,
  RefreshControl,
  StyleSheet,
} from 'react-native';
import { useNavigation } from '@react-navigation/native';
import { Ionicons } from '@expo/vector-icons';
import { api } from '../api';
import { colors } from '../theme';
import { formatNumber } from '../utils/format';
import type { DashboardStats } from '../types';
import Card from '../components/Card';
import StatCard from '../components/StatCard';
import TrafficChart from '../components/TrafficChart';
import DistributionBars from '../components/DistributionBars';
import AlertCard from '../components/AlertCard';
import EmptyState from '../components/EmptyState';
import LoadingView from '../components/LoadingView';

export default function DashboardScreen() {
  const navigation = useNavigation<any>();
  const [stats, setStats] = useState<DashboardStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [online, setOnline] = useState<boolean | null>(null);

  const load = useCallback(async (silent = false) => {
    if (!silent) setLoading(true);
    setError(null);
    try {
      const [s, h] = await Promise.all([api.getDashboardStats(), api.health()]);
      setStats(s);
      setOnline(h.status === 'ok');
    } catch (e: any) {
      setError(e?.message || '加载失败');
      setOnline(false);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
    const timer = setInterval(() => load(true), 30000);
    return () => clearInterval(timer);
  }, [load]);

  const onRefresh = async () => {
    setRefreshing(true);
    await load(true);
    setRefreshing(false);
  };

  if (loading && !stats) {
    return <LoadingView />;
  }

  return (
    <ScrollView
      style={styles.screen}
      contentContainerStyle={styles.content}
      refreshControl={
        <RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={colors.accentCyan} />
      }
    >
      {error ? (
        <View style={styles.banner}>
          <Ionicons name="cloud-offline-outline" size={16} color={colors.riskCritical} />
          <Text style={styles.bannerText}>{error}</Text>
        </View>
      ) : online === false ? (
        <View style={[styles.banner, { borderColor: colors.riskHigh }]}>
          <Ionicons name="cloud-offline-outline" size={16} color={colors.riskHigh} />
          <Text style={[styles.bannerText, { color: colors.riskHigh }]}>
            后端服务不可用，请检查后端是否启动
          </Text>
        </View>
      ) : null}

      {stats ? (
        <>
          <View style={styles.kpiRow}>
            <StatCard
              label="今日告警"
              value={formatNumber(stats.alerts_today)}
              color={colors.riskCritical}
              icon="warning"
            />
            <StatCard
              label="活跃威胁"
              value={formatNumber(stats.active_threats)}
              color={colors.riskHigh}
              icon="flash"
            />
          </View>
          <View style={styles.kpiRow}>
            <StatCard
              label="在线设备"
              value={`${stats.online_assets}/${stats.total_assets}`}
              color={colors.riskLow}
              icon="hardware-chip"
            />
            <StatCard
              label="安全评分"
              value={stats.risk_score}
              color={stats.risk_score > 60 ? colors.riskLow : colors.riskHigh}
              icon="shield-checkmark"
              sub={stats.system_status === 'normal' ? '状态正常' : '存在风险'}
            />
          </View>
          <View style={styles.kpiRow}>
            <StatCard
              label="扫描流量"
              value={formatNumber(stats.total_scanned)}
              color={colors.accentBlue}
              icon="swap-vertical"
            />
            <StatCard
              label="累计告警"
              value={formatNumber(stats.total_alerts)}
              color={colors.accentPurple}
              icon="alert-circle"
            />
          </View>

          <Card
            title="流量趋势（近 35 分钟）"
            right={
              <View style={styles.legend}>
                <View style={[styles.legendDot, { backgroundColor: colors.accentCyan }]} />
                <Text style={styles.legendText}>正常</Text>
                <View style={[styles.legendDot, { backgroundColor: colors.riskCritical }]} />
                <Text style={styles.legendText}>攻击</Text>
              </View>
            }
          >
            <TrafficChart data={stats.traffic_history} />
          </Card>

          <Card title="攻击类型分布">
            <DistributionBars data={stats.attack_distribution} />
          </Card>

          <Card
            title="最新告警"
            right={
              <Text
                style={styles.more}
                onPress={() => navigation.navigate('Alerts')}
              >
                查看全部 ›
              </Text>
            }
          >
            {stats.recent_alerts && stats.recent_alerts.length > 0 ? (
              stats.recent_alerts.map((a) => (
                <AlertCard
                  key={a.id}
                  item={a}
                  onPress={() => navigation.navigate('AlertDetail', { item: a })}
                />
              ))
            ) : (
              <EmptyState icon="shield-checkmark-outline" title="暂无告警" message="系统运行平稳，未检测到异常" />
            )}
          </Card>
        </>
      ) : (
        <EmptyState
          icon="cloud-offline-outline"
          title="无法获取数据"
          message={error ?? '请检查后端连接设置'}
        />
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
  banner: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    backgroundColor: colors.bgElevated,
    borderWidth: 1,
    borderColor: colors.riskCritical,
    borderRadius: 8,
    padding: 10,
    marginBottom: 12,
  },
  bannerText: {
    color: colors.riskCritical,
    fontSize: 13,
    flex: 1,
  },
  kpiRow: {
    flexDirection: 'row',
    marginHorizontal: -4,
  },
  legend: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 4,
  },
  legendDot: {
    width: 8,
    height: 8,
    borderRadius: 4,
  },
  legendText: {
    color: colors.textSecondary,
    fontSize: 11,
    marginRight: 4,
  },
  more: {
    color: colors.accentBlue,
    fontSize: 13,
  },
});
