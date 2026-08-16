/**
 * IoT IDS — Dark Tech Theme (mirrors the web frontend's theme.css palette)
 */

export const colors = {
  // Risk color system
  riskCritical: '#FF4444', // 高危 - 红
  riskHigh: '#FF8800', // 中危 - 橙
  riskMedium: '#FFCC00', // 低危 - 黄
  riskLow: '#00CC66', // 正常/安全 - 绿

  // Tech dark palette
  bgBase: '#0D1117',
  bgElevated: '#161B22',
  bgSurface: '#1C2333',
  bgHover: '#21283D',

  border: '#30363D',
  borderLight: '#21262D',

  textPrimary: '#E6EDF3',
  textSecondary: '#8B949E',
  textMuted: '#484F58',

  // Accent
  accentBlue: '#58A6FF',
  accentCyan: '#39D2C0',
  accentPurple: '#BC8CFF',

  white: '#FFFFFF',
  danger: '#FF4444',
  success: '#00CC66',
  warning: '#FFCC00',
};

export function riskColor(level: string): string {
  switch (level) {
    case 'critical':
      return colors.riskCritical;
    case 'high':
      return colors.riskHigh;
    case 'medium':
      return colors.riskMedium;
    case 'low':
      return colors.riskLow;
    default:
      return colors.textSecondary;
  }
}

export function riskBg(level: string): string {
  switch (level) {
    case 'critical':
      return 'rgba(255, 68, 68, 0.14)';
    case 'high':
      return 'rgba(255, 136, 0, 0.14)';
    case 'medium':
      return 'rgba(255, 204, 0, 0.12)';
    case 'low':
      return 'rgba(0, 204, 102, 0.12)';
    default:
      return 'rgba(139, 148, 158, 0.12)';
  }
}

export function riskLabel(level: string): string {
  switch (level) {
    case 'critical':
      return '高危';
    case 'high':
      return '中危';
    case 'medium':
      return '低危';
    case 'low':
      return '正常';
    default:
      return level || '未知';
  }
}

export function statusLabel(status: string): string {
  switch (status) {
    case 'new':
      return '待处理';
    case 'reviewed':
      return '已复核';
    case 'resolved':
      return '已处置';
    case 'false_positive':
      return '误报';
    case 'blocked':
      return '已拉黑';
    case 'online':
      return '在线';
    case 'offline':
      return '离线';
    case 'alert':
      return '告警';
    default:
      return status || '未知';
  }
}

export function statusColor(status: string): string {
  switch (status) {
    case 'new':
      return colors.riskCritical;
    case 'reviewed':
      return colors.accentBlue;
    case 'resolved':
      return colors.riskLow;
    case 'false_positive':
      return colors.textMuted;
    case 'blocked':
      return colors.riskHigh;
    case 'online':
      return colors.riskLow;
    case 'offline':
      return colors.textMuted;
    case 'alert':
      return colors.riskCritical;
    default:
      return colors.textSecondary;
  }
}

export function attackTypeLabel(type: string): string {
  switch ((type || '').toLowerCase()) {
    case 'mirai':
      return 'Mirai';
    case 'gafgyt':
      return 'Gafgyt';
    case 'portscan':
      return '端口扫描';
    case 'bruteforce':
      return '暴力破解';
    case 'ddos':
      return 'DDoS';
    case 'dos':
      return 'DoS';
    case 'recon':
      return '侦查';
    case 'theft':
      return '数据窃取';
    case 'other':
      return '其他攻击';
    case 'normal':
      return '正常';
    default:
      return type || '未知';
  }
}

export function deviceTypeLabel(type: string): string {
  switch ((type || '').toLowerCase()) {
    case 'camera':
      return '摄像头';
    case 'door':
      return '门禁';
    case 'sensor':
      return '传感器';
    case 'router':
      return '路由器';
    case 'hub':
      return '网关';
    case 'socket':
      return '智能插座';
    case 'lock':
      return '智能门锁';
    case 'probe':
      return '探针节点';
    case 'other':
      return '其他设备';
    default:
      return type || '未知设备';
  }
}
