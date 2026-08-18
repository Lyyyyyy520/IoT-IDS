import React, { useState } from 'react';
import { View, Text, StyleSheet } from 'react-native';
import Svg, { Line, Circle, Text as SvgText } from 'react-native-svg';
import { colors } from '../theme';

interface Node {
  id: string;
  label: string;
  type: string;
  risk: string; // normal/high/critical
  ip?: string;
}

interface Link {
  source: string;
  target: string;
}

interface Props {
  nodes: Node[];
  links: Link[];
  height?: number;
}

function riskFill(risk: string): string {
  switch (risk) {
    case 'critical':
      return colors.riskCritical;
    case 'high':
      return colors.riskHigh;
    case 'normal':
      return colors.riskLow;
    default:
      return colors.accentBlue;
  }
}

export default function TopologyView({ nodes, links, height = 260 }: Props) {
  const [width, setWidth] = useState(0);

  if (!nodes || nodes.length === 0) {
    return (
      <View style={[styles.empty, { height }]}>
        <Text style={styles.emptyText}>暂无拓扑数据</Text>
      </View>
    );
  }

  const cx = width / 2;
  const cy = height / 2;
  const R = Math.min(width, height) / 2 - 44;

  // 环形布局
  const pos: Record<string, { x: number; y: number }> = {};
  nodes.forEach((n, i) => {
    const angle = (i / nodes.length) * 2 * Math.PI - Math.PI / 2;
    pos[n.id] = { x: cx + R * Math.cos(angle), y: cy + R * Math.sin(angle) };
  });

  const linksToDraw = (links || []).filter(
    (l) => pos[l.source] && pos[l.target] && l.source !== l.target,
  );

  return (
    <View style={{ height }} onLayout={(e) => setWidth(e.nativeEvent.layout.width)}>
      {width > 0 && (
        <Svg width={width} height={height}>
          {linksToDraw.map((l, i) => (
            <Line
              key={i}
              x1={pos[l.source].x}
              y1={pos[l.source].y}
              x2={pos[l.target].x}
              y2={pos[l.target].y}
              stroke={colors.border}
              strokeWidth={1}
            />
          ))}

          {nodes.map((n) => {
            const p = pos[n.id];
            return (
              <React.Fragment key={n.id}>
                <Circle
                  cx={p.x}
                  cy={p.y}
                  r={10}
                  fill={riskFill(n.risk)}
                  fillOpacity={0.9}
                />
                <SvgText
                  x={p.x}
                  y={p.y + 22}
                  fill={colors.textSecondary}
                  fontSize={9}
                  textAnchor="middle"
                >
                  {n.label}
                </SvgText>
              </React.Fragment>
            );
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
