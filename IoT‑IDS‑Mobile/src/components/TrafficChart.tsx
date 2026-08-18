import React, { useState } from 'react';
import { View, Text, StyleSheet } from 'react-native';
import Svg, { Line, Polyline, Text as SvgText } from 'react-native-svg';
import { colors } from '../theme';
import type { TrafficPoint } from '../types';

interface Props {
  data: TrafficPoint[];
  height?: number;
}

export default function TrafficChart({ data, height = 180 }: Props) {
  const [width, setWidth] = useState(0);

  if (!data || data.length === 0) {
    return (
      <View style={[styles.empty, { height }]}>
        <Text style={styles.emptyText}>暂无流量数据</Text>
      </View>
    );
  }

  const padL = 36;
  const padR = 10;
  const padT = 10;
  const padB = 22;
  const plotW = Math.max(10, width - padL - padR);
  const plotH = Math.max(10, height - padT - padB);

  let maxV = 1;
  data.forEach((p) => {
    maxV = Math.max(maxV, p.normal || 0, p.attack || 0);
  });
  maxV = Math.max(1, maxV * 1.15);

  const n = data.length;
  const x = (i: number) => padL + (n === 1 ? plotW / 2 : (i / (n - 1)) * plotW);
  const y = (v: number) => padT + (1 - v / maxV) * plotH;

  const normalPts = data.map((p, i) => `${x(i)},${y(p.normal || 0)}`).join(' ');
  const attackPts = data.map((p, i) => `${x(i)},${y(p.attack || 0)}`).join(' ');

  const grid = [0, 0.25, 0.5, 0.75, 1];
  const labelStep = Math.max(1, Math.ceil(n / 5));

  return (
    <View style={{ height }} onLayout={(e) => setWidth(e.nativeEvent.layout.width)}>
      {width > 0 && (
        <Svg width={width} height={height}>
          {grid.map((g, idx) => {
            const gy = padT + g * plotH;
            return (
              <React.Fragment key={idx}>
                <Line
                  x1={padL}
                  y1={gy}
                  x2={width - padR}
                  y2={gy}
                  stroke={colors.borderLight}
                  strokeWidth={0.5}
                />
                <SvgText
                  x={padL - 4}
                  y={gy + 3}
                  fill={colors.textMuted}
                  fontSize={9}
                  textAnchor="end"
                >
                  {Math.round(maxV * (1 - g))}
                </SvgText>
              </React.Fragment>
            );
          })}

          <Polyline points={normalPts} fill="none" stroke={colors.accentCyan} strokeWidth={2} />
          <Polyline points={attackPts} fill="none" stroke={colors.riskCritical} strokeWidth={2} />

          {data.map((p, i) => {
            if (i % labelStep === 0 || i === n - 1) {
              return (
                <SvgText
                  key={i}
                  x={x(i)}
                  y={height - 5}
                  fill={colors.textMuted}
                  fontSize={9}
                  textAnchor="middle"
                >
                  {p.time}
                </SvgText>
              );
            }
            return null;
          })}
        </Svg>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  empty: {
    alignItems: 'center',
    justifyContent: 'center',
  },
  emptyText: {
    color: colors.textMuted,
    fontSize: 13,
  },
});
