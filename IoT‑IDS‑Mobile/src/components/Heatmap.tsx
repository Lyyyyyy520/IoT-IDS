import React from 'react';
import { View, Text, StyleSheet } from 'react-native';
import { colors } from '../theme';

interface Props {
  data: number[][]; // [[hour, dow, count], ...]
  days: string[];
  hours: string[];
}

function hexToRgb(hex: string): { r: number; g: number; b: number } {
  const h = hex.replace('#', '');
  return {
    r: parseInt(h.slice(0, 2), 16),
    g: parseInt(h.slice(2, 4), 16),
    b: parseInt(h.slice(4, 6), 16),
  };
}

function heatColor(ratio: number): string {
  if (ratio <= 0) return colors.bgSurface;
  const from = hexToRgb(colors.accentCyan);
  const to = hexToRgb(colors.riskCritical);
  const r = Math.round(from.r + (to.r - from.r) * ratio);
  const g = Math.round(from.g + (to.g - from.g) * ratio);
  const b = Math.round(from.b + (to.b - from.b) * ratio);
  return `rgb(${r},${g},${b})`;
}

export default function Heatmap({ data, days, hours }: Props) {
  // 建索引 dow -> hour -> count
  const map: Record<number, Record<number, number>> = {};
  let max = 1;
  (data || []).forEach(([hour, dow, count]) => {
    if (!map[dow]) map[dow] = {};
    map[dow][hour] = count;
    if (count > max) max = count;
  });

  const dayList = days && days.length ? days : ['周日', '周一', '周二', '周三', '周四', '周五', '周六'];

  if (!data || data.length === 0) {
    return <Text style={styles.empty}>暂无热力图数据</Text>;
  }

  return (
    <View>
      {/* 小时表头 */}
      <View style={styles.row}>
        <View style={styles.dayLabel} />
        {Array.from({ length: 24 }, (_, h) => (
          <View key={h} style={styles.hourCell}>
            {h % 6 === 0 ? <Text style={styles.hourText}>{h}</Text> : null}
          </View>
        ))}
      </View>

      {dayList.map((day, dow) => (
        <View key={dow} style={styles.row}>
          <Text style={styles.dayLabel}>{day}</Text>
          {Array.from({ length: 24 }, (_, h) => {
            const count = map[dow]?.[h] ?? 0;
            const ratio = count / max;
            return (
              <View
                key={h}
                style={[
                  styles.cell,
                  { backgroundColor: count > 0 ? heatColor(ratio) : colors.bgSurface },
                ]}
              />
            );
          })}
        </View>
      ))}

      <View style={styles.legend}>
        <Text style={styles.legendText}>少</Text>
        {[0, 0.25, 0.5, 0.75, 1].map((r) => (
          <View key={r} style={[styles.legendCell, { backgroundColor: r === 0 ? colors.bgSurface : heatColor(r) }]} />
        ))}
        <Text style={styles.legendText}>多</Text>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  row: {
    flexDirection: 'row',
    alignItems: 'center',
    marginBottom: 2,
  },
  dayLabel: {
    width: 34,
    color: colors.textSecondary,
    fontSize: 10,
    marginRight: 2,
  },
  hourCell: {
    width: 11,
    alignItems: 'center',
    justifyContent: 'center',
  },
  hourText: {
    color: colors.textMuted,
    fontSize: 8,
  },
  cell: {
    width: 10,
    height: 15,
    marginRight: 1,
    borderRadius: 1,
  },
  legend: {
    flexDirection: 'row',
    alignItems: 'center',
    marginTop: 10,
    gap: 3,
  },
  legendCell: {
    width: 16,
    height: 10,
    borderRadius: 2,
  },
  legendText: {
    color: colors.textMuted,
    fontSize: 10,
    marginHorizontal: 4,
  },
  empty: {
    color: colors.textMuted,
    fontSize: 13,
    textAlign: 'center',
    paddingVertical: 16,
  },
});
