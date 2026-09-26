import type {
  HelpCategory,
  HelpStatus,
  IncidentApiError,
  IncidentRole,
  IncidentSeverity,
  IncidentSource,
  IncidentStatus,
} from '../../api/v3Incidents';

export const STATUS_LABELS: Record<IncidentStatus, string> = {
  open: '待处理', acknowledged: '已确认处理', recovering: '恢复中', resolved: '已解决', false_positive: '误报结案',
};
export const SEVERITY_LABELS: Record<IncidentSeverity, string> = {
  info: '提示', low: '低', medium: '中', high: '高', critical: '严重',
};
export const SOURCE_LABELS: Record<IncidentSource, string> = {
  manual: '人工记录', rule: '快速规则', system: '系统事件',
};
export const ROLE_LABELS: Record<IncidentRole, string> = {
  affected: '受影响设备', suspected_source: '疑似来源', observer: '观察设备', unknown: '角色待确认',
};
export const HELP_STATUS_LABELS: Record<HelpStatus, string> = {
  open: '待处理', in_progress: '处理中', waiting_for_user: '等待用户', closed: '已关闭',
};
export const HELP_CATEGORY_LABELS: Record<HelpCategory, string> = {
  device_issue: '设备问题', security_question: '安全咨询', service_problem: '服务问题', other: '其他',
};

export function localTime(value: string | null): string {
  if (!value) return '尚无记录';
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
  }).format(new Date(value));
}

export function incidentErrorMessage(error: IncidentApiError): string {
  const known: Partial<Record<IncidentApiError['kind'], string>> = {
    bad_request: '请求内容或筛选条件不符合接口契约。',
    unauthorized: '登录状态已失效，请重新登录。',
    forbidden: '当前账号没有执行此操作的权限。',
    not_found: '事件或求助记录不存在。',
    too_large: '提交内容超过服务端大小限制。',
    conflict: '数据已被其他管理员更新或事件状态已变化，请加载最新记录后检查；当前内容未自动覆盖。',
    rate_limited: '操作过于频繁，请稍后重试。',
    unavailable: '事件服务或数据库尚未准备。',
    network: '无法连接后端；已保留最后一次真实数据。',
    invalid_response: '服务响应不符合 v3 契约，未显示该数据。',
    http: '后端暂时无法完成请求，请稍后重试。',
    aborted: '请求已取消。',
  };
  const prefix = known[error.kind] ?? '事件工作区请求未能完成。';
  return error.requestId ? `${prefix} 请求 ID：${error.requestId}` : prefix;
}

export const PUBLIC_TEXT_FORBIDDEN = /(?:\bgnn\b|graph[_ -]?id|model[_ -]?(?:version|score|feature)|rule[_ -]?expression|规则表达式|模型(?:分数|特征|版本)|(?:\d{1,3}\.){3}\d{1,3}|(?:[0-9a-f]{2}[:-]){5}[0-9a-f]{2}|\bport\s*[:=]?\s*\d+|端口\s*[:：]?\s*\d+)/i;
