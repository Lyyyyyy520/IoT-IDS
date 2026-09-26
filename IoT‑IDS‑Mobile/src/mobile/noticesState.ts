import type { MobileNotice, NoticeCollection } from './api';

export function mergeNoticeCollection(current: MobileNotice[] | null, result: NoticeCollection): MobileNotice[] {
  if (result.mode === 'snapshot') return [...result.notices].sort((a, b) => b.updated_at.localeCompare(a.updated_at));
  const byId = new Map((current ?? []).map(item => [item.incident_id, item]));
  for (const item of result.notices) byId.set(item.incident_id, item);
  for (const item of result.tombstones) {
    const prior = byId.get(item.incident_id);
    if (prior && item.reason === 'false_positive') byId.set(item.incident_id, { ...prior, status: 'false_positive' });
    else byId.delete(item.incident_id);
  }
  return [...byId.values()].sort((a, b) => b.updated_at.localeCompare(a.updated_at));
}

export class NoticesStore {
  private entries: MobileNotice[] | null = null;
  private next: string | null = null;

  get notices(): MobileNotice[] | null { return this.entries ? [...this.entries] : null; }
  get cursor(): string | null { return this.next; }

  apply(result: NoticeCollection): MobileNotice[] {
    this.entries = mergeNoticeCollection(this.entries, result);
    this.next = result.snapshot_required ? null : result.next_cursor;
    return [...this.entries];
  }

  upsert(notice: MobileNotice): MobileNotice[] {
    this.entries = [...(this.entries ?? []).filter(item => item.incident_id !== notice.incident_id), notice]
      .sort((a, b) => b.updated_at.localeCompare(a.updated_at));
    return [...this.entries];
  }

  clear(): void { this.entries = null; this.next = null; }
}
