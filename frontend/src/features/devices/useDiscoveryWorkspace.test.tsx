import { act, renderHook, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { DiscoveryCandidate, DiscoveryListResponse } from '../../api/v3Discovery';
import { DiscoveryApiError } from '../../api/v3Discovery';
import { useDiscoveryWorkspace, type DiscoveryApi } from './useDiscoveryWorkspace';

function row(id: string): DiscoveryCandidate {
  return {
    candidate_id: id, proposed_device_id: `${id}-suggested`, status: 'pending', conflict: false,
    conflict_reason: null, first_seen_at: '2026-09-20T01:00:00Z', last_seen_at: '2026-09-20T02:00:00Z',
    source_count: 1, observation_count: 1, claimed_device_id: null, claimed_at: null,
    ignored_at: null, ignored_reason: null, candidate_version: 1, updated_at: '2026-09-20T02:00:00Z',
    sources: ['arp'],
  };
}
function list(items: DiscoveryCandidate[]): DiscoveryListResponse {
  return { items, total: items.length, limit: 30, offset: 0 };
}
function fakeApi(overrides: Partial<DiscoveryApi> = {}): DiscoveryApi {
  return {
    list: vi.fn().mockResolvedValue(list([row('candidate-01')])),
    get: vi.fn().mockResolvedValue(row('candidate-01')),
    claim: vi.fn(), ignore: vi.fn(), restore: vi.fn(),
    ...overrides,
  } as unknown as DiscoveryApi;
}

describe('useDiscoveryWorkspace', () => {
  it('loads only when the discovery tab is active and renders an honest empty result', async () => {
    const api = fakeApi({ list: vi.fn().mockResolvedValue(list([])) });
    const { result, rerender } = renderHook(({ active }) => useDiscoveryWorkspace({ active, api }), {
      initialProps: { active: false },
    });
    expect(api.list).not.toHaveBeenCalled();
    rerender({ active: true });
    await waitFor(() => expect(api.list).toHaveBeenCalledTimes(1));
    expect(result.current.items).toEqual([]);
    expect(result.current.total).toBe(0);
  });

  it('cancels the prior request and ignores its late response after filters change', async () => {
    let resolveFirst!: (response: DiscoveryListResponse) => void;
    let firstSignal: AbortSignal | undefined;
    const api = fakeApi({
      list: vi.fn((_query, signal) => {
        if (vi.mocked(api.list).mock.calls.length === 1) {
          firstSignal = signal;
          return new Promise<DiscoveryListResponse>((resolve) => { resolveFirst = resolve; });
        }
        return Promise.resolve(list([row('new-filter-result')]));
      }),
    });
    const { result } = renderHook(() => useDiscoveryWorkspace({ active: true, api, debounceMs: 0 }));
    await waitFor(() => expect(api.list).toHaveBeenCalledTimes(1));
    act(() => result.current.patchFilters({ source: 'arp' }));
    await waitFor(() => expect(api.list).toHaveBeenCalledTimes(2));
    expect(firstSignal?.aborted).toBe(true);
    await act(async () => resolveFirst(list([row('stale-old-result')])));
    await waitFor(() => expect(result.current.items.map((item) => item.candidate_id)).toEqual(['new-filter-result']));
  });

  it('aborts list and detail requests when the workspace unmounts', async () => {
    let listSignal: AbortSignal | undefined;
    let detailSignal: AbortSignal | undefined;
    const never = new Promise<DiscoveryListResponse>(() => {});
    const api = fakeApi({
      list: vi.fn((_query, signal) => { listSignal = signal; return never; }),
      get: vi.fn((_id, signal) => { detailSignal = signal; return new Promise<DiscoveryCandidate>(() => {}); }),
    });
    const { result, unmount } = renderHook(() => useDiscoveryWorkspace({ active: true, api }));
    await waitFor(() => expect(api.list).toHaveBeenCalled());
    act(() => { void result.current.selectCandidate('candidate-01'); });
    await waitFor(() => expect(api.get).toHaveBeenCalled());
    unmount();
    expect(listSignal?.aborted).toBe(true);
    expect(detailSignal?.aborted).toBe(true);
  });

  it('keeps service errors typed rather than filling the candidate list with fixtures', async () => {
    const api = fakeApi({ list: vi.fn().mockRejectedValue(new DiscoveryApiError('unavailable', 'unavailable')) });
    const { result } = renderHook(() => useDiscoveryWorkspace({ active: true, api }));
    await waitFor(() => expect(result.current.error?.kind).toBe('unavailable'));
    expect(result.current.items).toEqual([]);
  });
});
