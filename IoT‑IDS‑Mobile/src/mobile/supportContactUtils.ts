export function safePhoneUrl(value: string): string | null {
  if (!/^[0-9+() .-]{5,32}$/.test(value)) return null;
  const normalized = value.replace(/[() .-]/g, '');
  return /^\+?[0-9]{5,20}$/.test(normalized) ? `tel:${normalized}` : null;
}

export function safeEmailUrl(value: string): string | null {
  if (value.length > 254 || /[\r\n<>;]/.test(value) || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value)) return null;
  const [local, domain] = value.split('@');
  if (!local || !domain || local.length > 64) return null;
  return `mailto:${encodeURIComponent(local)}@${encodeURIComponent(domain)}`;
}
