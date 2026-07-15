import { Tag } from 'antd';
import type { AlertItem } from '../../api';

const riskColorMap: Record<string, string> = {
  critical: '#FF4444',
  high: '#FF8800',
  medium: '#FFCC00',
  low: '#00CC66',
};

const riskLabelMap: Record<string, string> = {
  critical: '高危',
  high: '中危',
  medium: '低危',
  low: '安全',
};

export default function AlertCard({ alert, compact = false }: { alert: AlertItem; compact?: boolean }) {
  return (
    <div
      style={{
        padding: compact ? '10px 16px' : '14px 20px',
        borderBottom: '1px solid var(--border-light)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        gap: 12,
        cursor: 'pointer',
        transition: 'background 0.15s',
      }}
      onMouseEnter={(e) => (e.currentTarget.style.background = 'var(--bg-hover)')}
      onMouseLeave={(e) => (e.currentTarget.style.background = 'transparent')}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, flex: 1, minWidth: 0 }}>
        <div
          style={{
            width: 6,
            height: 6,
            borderRadius: '50%',
            background: riskColorMap[alert.risk_level] || '#58A6FF',
            flexShrink: 0,
          }}
        />
        <div style={{ minWidth: 0 }}>
          <div style={{ fontSize: 13, fontWeight: 500, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
            {alert.attack_type} 攻击
          </div>
          <div style={{ fontSize: 11, color: 'var(--text-secondary)', marginTop: 2 }}>
            {alert.src_ip} · {alert.timestamp}
          </div>
        </div>
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexShrink: 0 }}>
        {alert.merged_count > 1 && (
          <Tag style={{ margin: 0, fontSize: 10 }}>×{alert.merged_count}</Tag>
        )}
        <Tag
          color={riskColorMap[alert.risk_level]}
          style={{ margin: 0, fontSize: 10 }}
        >
          {riskLabelMap[alert.risk_level]}
        </Tag>
      </div>
    </div>
  );
}
