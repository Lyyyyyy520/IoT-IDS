/**
 * 通用格式化工具
 */

export function formatNumber(n: number | undefined | null): string {
  if (n == null || isNaN(n)) return '0';
  if (Math.abs(n) >= 10000) {
    return (n / 10000).toFixed(1).replace(/\.0$/, '') + 'w';
  }
  if (Math.abs(n) >= 1000) {
    return (n / 1000).toFixed(1).replace(/\.0$/, '') + 'k';
  }
  return String(n);
}

/** "2026-07-14 14:30:00" -> "14:30:00" */
export function formatTime(iso?: string | null): string {
  if (!iso) return '--';
  const m = iso.match(/(\d{2}:\d{2}(:\d{2})?)/);
  return m ? m[1] : iso.slice(0, 16);
}

/** "2026-07-14 14:30:00" -> "07-14 14:30" */
export function formatDateTime(iso?: string | null): string {
  if (!iso) return '--';
  const d = new Date(iso.replace(' ', 'T'));
  if (isNaN(d.getTime())) return iso.slice(0, 16);
  const p = (x: number) => String(x).padStart(2, '0');
  return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

/** 相对时间，如 "3 分钟前" */
export function relativeTime(iso?: string | null): string {
  if (!iso) return '--';
  const d = new Date(iso.replace(' ', 'T'));
  if (isNaN(d.getTime())) return iso.slice(0, 16);
  const diffSec = Math.max(0, (Date.now() - d.getTime()) / 1000);
  if (diffSec < 60) return '刚刚';
  if (diffSec < 3600) return `${Math.floor(diffSec / 60)} 分钟前`;
  if (diffSec < 86400) return `${Math.floor(diffSec / 3600)} 小时前`;
  return `${Math.floor(diffSec / 86400)} 天前`;
}

/** 置信度百分比显示 */
export function formatConfidence(c?: number | null): string {
  if (c == null || isNaN(c)) return '--';
  return `${(c * 100).toFixed(0)}%`;
}
