import type { MobileNotice, NoticeCollection } from './api';
import { mergeNoticeCollection, NoticesStore } from './noticesState';

const notice = (patch: Partial<MobileNotice> = {}): MobileNotice => ({
  incident_id: 'inc-1', user_title: '设备提醒', user_summary: '请留意设备服务状态。', severity: 'medium',
  affected_devices: [{ device_id: 'dev-1', display_name: '客厅设备', device_type: 'sensor', area_id: 'home' }],
  first_seen_at: '2026-09-24T01:00:00Z', updated_at: '2026-09-24T01:00:00Z', status: 'open',
  public_progress: '管理员尚未开始处理', read: false, first_read_at: null, acknowledged: false,
  acknowledged_at: null, resolved_at: null, ...patch,
});
const collection = (patch: Partial<NoticeCollection> = {}): NoticeCollection => ({
  mode: 'snapshot', notices: [], tombstones: [], next_cursor: '0:0', snapshot_required: false, ...patch,
});

it('loads a real full snapshot and de-duplicates repeated deltas by stable incident id', () => {
  const initial = mergeNoticeCollection(null, collection({ notices: [notice()] }));
  const updated = notice({ read: true, first_read_at: '2026-09-24T01:01:00Z', updated_at: '2026-09-24T01:01:00Z' });
  const once = mergeNoticeCollection(initial, collection({ mode: 'delta', notices: [updated], next_cursor: '2:0' }));
  const twice = mergeNoticeCollection(once, collection({ mode: 'delta', notices: [updated], next_cursor: '2:0' }));
  expect(twice).toHaveLength(1);
  expect(twice[0].read).toBe(true);
});

it('moves a tombstoned false-positive notice to neutral history and removes other tombstones', () => {
  const current = [notice(), notice({ incident_id: 'inc-2', user_title: '另一条提醒' })];
  const result = mergeNoticeCollection(current, collection({ mode: 'delta', tombstones: [
    { incident_id: 'inc-1', change_id: 4, reason: 'false_positive' },
    { incident_id: 'inc-2', change_id: 5, reason: 'revoked' },
  ] }));
  expect(result).toHaveLength(1);
  expect(result[0].status).toBe('false_positive');
});

it('replaces local state on full resynchronization, including scope changes', () => {
  const result = mergeNoticeCollection([notice()], collection({ notices: [notice({ incident_id: 'scoped-new' })], next_cursor: '8:3' }));
  expect(result.map(item => item.incident_id)).toEqual(['scoped-new']);
});

it('keeps the cursor only for complete snapshots, upserts detail responses and clears on logout', () => {
  const store = new NoticesStore();
  expect(store.cursor).toBeNull();
  store.apply(collection({ notices: [notice()], next_cursor: '4:2' }));
  expect(store.cursor).toBe('4:2');
  store.apply(collection({ mode: 'delta', notices: [notice({ acknowledged: true })], next_cursor: '5:2' }));
  expect(store.notices).toHaveLength(1);
  expect(store.notices?.[0].acknowledged).toBe(true);
  store.apply(collection({ notices: [notice()], next_cursor: '6:3', snapshot_required: true }));
  expect(store.cursor).toBeNull();
  store.upsert(notice({ read: true }));
  expect(store.notices?.[0].read).toBe(true);
  store.clear();
  expect(store.notices).toBeNull();
  expect(store.cursor).toBeNull();
});
