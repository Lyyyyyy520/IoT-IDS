import { afterEach, describe, expect, it, vi } from 'vitest';
import { deviceDetail } from '../test/deviceFixtures';
import {
  DiscoveryApiError,
  DISCOVERY_ENDPOINT,
  V3DiscoveryClient,
  parseDiscoveryCandidate,
  parseDiscoveryList,
} from './v3Discovery';

const candidate = {
  candidate_id: 'candidate-01',
  proposed_device_id: 'suggested-01',
  status: 'pending',
  conflict: false,
  conflict_reason: null,
  first_seen_at: '2026-09-20T01:00:00Z',
  last_seen_at: '2026-09-20T02:00:00Z',
  source_count: 1,
  observation_count: 2,
  claimed_device_id: null,
  claimed_at: null,
  ignored_at: null,
  ignored_reason: null,
  candidate_version: 1,
  updated_at: '2026-09-20T02:00:00Z',
  sources: ['arp'],
  device_type_hint: 'sensor',
  identity_kind: 'mac',
  mac_address: 'AA:BB:CC:DD:EE:01',
  latest_ip: '192.0.2.1',
  conflicting_proposed_device_ids: [],
  observations: [{
    source: 'arp', observed_at: '2026-09-20T01:00:00Z', received_at: '2026-09-20T02:00:00Z',
    proposed_device_id: 'suggested-01', sanitized_metadata: { device_type_hint: 'sensor' },
    ip_address: '192.0.2.1', evidence_hash: 'a'.repeat(64),
  }],
};

function jsonResponse(body: unknown, status = 200, csrf?: string): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: csrf ? { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf } : { 'Content-Type': 'application/json' },
  });
}

afterEach(() => vi.unstubAllGlobals());

describe('v3 discovery API runtime contract', () => {
  it('accepts both admin identity evidence and redacted operator payloads', () => {
    expect(parseDiscoveryCandidate(candidate).mac_address).toBe('AA:BB:CC:DD:EE:01');
    const operator: Record<string, unknown> = { ...candidate };
    delete operator.mac_address;
    delete operator.latest_ip;
    operator.observations = [{
      source: 'arp', observed_at: '2026-09-20T01:00:00Z', received_at: '2026-09-20T02:00:00Z',
      proposed_device_id: 'suggested-01', sanitized_metadata: { device_type_hint: 'sensor' },
    }];
    expect(parseDiscoveryCandidate(operator).observations?.[0].ip_address).toBeUndefined();
    expect(parseDiscoveryList({ items: [operator], total: 1, limit: 50, offset: 0 }).items).toHaveLength(1);
  });

  it('rejects malformed candidate identity and pagination data', () => {
    expect(() => parseDiscoveryCandidate({ ...candidate, candidate_version: 0 })).toThrow(DiscoveryApiError);
    expect(() => parseDiscoveryList({ items: 'not-an-array', total: 1, limit: 50, offset: 0 })).toThrow('items');
  });

  it('uses relative paths, cookie session, reads CSRF on GET, and sends it on claim', async () => {
    const fetch = vi.fn()
      .mockResolvedValueOnce(jsonResponse({ items: [candidate], total: 1, limit: 50, offset: 0 }, 200, 'csrf-memory-only'))
      .mockResolvedValueOnce(jsonResponse({
        device: deviceDetail,
        candidate: { ...candidate, status: 'claimed', conflict_reason: null, conflict: false, claimed_device_id: deviceDetail.device_id, claimed_at: candidate.updated_at, candidate_version: 2 },
        credential_provisioning_required: true,
        provisioning_message: '需要配置 MQTT ACL。',
      }, 201));
    vi.stubGlobal('fetch', fetch);
    const client = new V3DiscoveryClient();
    await client.list({ search: 'a b', source: 'arp', limit: 30, offset: 0 });
    const result = await client.claim('candidate/01', {
      expected_candidate_version: 1, device_id: 'manual-01', display_name: '手工确认设备',
      device_type: 'sensor', area_id: null, importance: 'normal', profile_source: 'physical',
    });
    expect(fetch.mock.calls[0][0]).toBe(`${DISCOVERY_ENDPOINT}?search=a+b&source=arp&limit=30&offset=0`);
    expect(fetch.mock.calls[0][1]).toMatchObject({ credentials: 'include', cache: 'no-store', method: 'GET' });
    expect(fetch.mock.calls[1][0]).toBe(`${DISCOVERY_ENDPOINT}/candidate%2F01/claim`);
    expect(fetch.mock.calls[1][1]).toMatchObject({
      method: 'POST', credentials: 'include',
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': 'csrf-memory-only' },
    });
    expect(JSON.parse(String(fetch.mock.calls[1][1]?.body))).toMatchObject({ device_id: 'manual-01' });
    expect(result.credential_provisioning_required).toBe(true);
  });

  it('refreshes discovery GET for a missing CSRF token before refusing to write', async () => {
    const fetch = vi.fn().mockResolvedValue(jsonResponse({ items: [], total: 0, limit: 1, offset: 0 }));
    vi.stubGlobal('fetch', fetch);
    await expect(new V3DiscoveryClient().ignore('candidate-01', 1, '无效')).rejects.toMatchObject({
      kind: 'invalid_response',
    });
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(fetch.mock.calls[0][1]?.method).toBe('GET');
  });

  it('maps conflict errors and preserves only safe request identifiers', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse({ error: {
      code: 'candidate_version_conflict', message: '版本冲突', request_id: 'req-safe-9',
    } }, 409, 'csrf')));
    await expect(new V3DiscoveryClient().list()).rejects.toMatchObject({
      kind: 'conflict', code: 'candidate_version_conflict', requestId: 'req-safe-9', status: 409,
    });
  });
});
