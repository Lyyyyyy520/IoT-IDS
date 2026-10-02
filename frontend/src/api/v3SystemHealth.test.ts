import { afterEach, describe, expect, it, vi } from 'vitest';
import { getSystemHealth } from './v3SystemHealth';

afterEach(() => vi.unstubAllGlobals());

describe('system health API', () => {
  it('requests the read-only endpoint with the Web session', async () => {
    const payload = {
      observed_at: '2026-09-24T12:00:00Z',
      components: {
        graph: { status: 'unavailable', updated_at: '2026-09-24T12:00:00Z', reason_code: 'graph_capability_unavailable' },
      },
      maintenance: {
      last_successful_at: null, last_plan_at: null, last_apply_at: null,
      last_plan_reason_code: 'maintenance_plan_read_only', reason_code: 'no_maintenance_run',
    },
      capacity: {},
      automatic_maintenance: false,
    };
    const fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => payload,
    });
    vi.stubGlobal('fetch', fetch);
    const controller = new AbortController();

    await expect(getSystemHealth(controller.signal)).resolves.toEqual(payload);
    expect(fetch).toHaveBeenCalledWith('/api/v3/system/health', {
      method: 'GET',
      credentials: 'include',
      headers: { Accept: 'application/json' },
      signal: controller.signal,
    });
  });

  it('does not treat an unavailable health API as a healthy response', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: false,
      status: 503,
    }));

    await expect(getSystemHealth()).rejects.toThrow('HTTP 503');
  });
});
