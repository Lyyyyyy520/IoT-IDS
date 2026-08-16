import React, { useCallback, useEffect, useState } from 'react';
import { ScrollView, RefreshControl, StyleSheet } from 'react-native';
import { api } from '../api';
import { colors } from '../theme';
import Card from '../components/Card';
import Heatmap from '../components/Heatmap';
import MitreChain from '../components/MitreChain';
import TopologyView from '../components/TopologyView';
import EmptyState from '../components/EmptyState';
import LoadingView from '../components/LoadingView';

export default function AnalysisScreen() {
  const [heatmap, setHeatmap] = useState<any>(null);
  const [mitre, setMitre] = useState<any>(null);
  const [topology, setTopology] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      const [h, m, t] = await Promise.all([
        api.getHeatmap().catch(() => null),
        api.getMitre().catch(() => null),
        api.getTopology().catch(() => null),
      ]);
      setHeatmap(h);
      setMitre(m);
      setTopology(t);
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

  if (loading) return <LoadingView />;

  return (
    <ScrollView
      style={styles.screen}
      contentContainerStyle={styles.content}
      refreshControl={
        <RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={colors.accentCyan} />
      }
    >
      {error ? (
        <EmptyState icon="cloud-offline-outline" title="加载失败" message={error} />
      ) : null}

      <Card title="网络拓扑">
        <TopologyView nodes={topology?.nodes ?? []} links={topology?.links ?? []} />
      </Card>

      <Card title="攻击热力图（近 7 天 × 24 小时）">
        <Heatmap data={heatmap?.data ?? []} days={heatmap?.days ?? []} hours={heatmap?.hours ?? []} />
      </Card>

      <Card title="MITRE ATT&CK 攻击链">
        <MitreChain stages={mitre?.stages ?? []} />
      </Card>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: colors.bgBase },
  content: { padding: 12, paddingBottom: 24 },
});
