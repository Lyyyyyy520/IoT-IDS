import { StrictMode, type ReactNode } from 'react';
import { act, renderHook, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { DevicesApi } from '../../api/v3Devices';
import { deviceDetail, listItem, listResponse } from '../../test/deviceFixtures';
import { useDeviceWorkspace } from './useDeviceWorkspace';

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((accept, fail) => { resolve = accept; reject = fail; });
  return { promise, resolve, reject };
}

function fakeApi(overrides: Partial<DevicesApi> = {}): DevicesApi {
  return {
    listDevices: vi.fn().mockResolvedValue(listResponse()),
    getDevice: vi.fn().mockResolvedValue(deviceDetail),
    createDevice: vi.fn().mockResolvedValue(deviceDetail),
    updateDevice: vi.fn().mockResolvedValue(deviceDetail),
    setOperationMode: vi.fn().mockResolvedValue(deviceDetail),
    retireDevice: vi.fn().mockResolvedValue(deviceDetail),
    restoreDevice: vi.fn().mockResolvedValue(deviceDetail),
    deleteDevice: vi.fn().mockResolvedValue({
      device_id: 'camera-01', deleted: true, retained_management_audit: true,
    }),
    ...overrides,
  };
}

describe('useDeviceWorkspace', () => {
  it('accepts the live response after the StrictMode setup and cleanup rehearsal', async () => {
    const api = fakeApi();
    const wrapper = ({ children }: { children: ReactNode }) => (
      <StrictMode>{children}</StrictMode>
    );
    const { result } = renderHook(
      () => useDeviceWorkspace({ api, initialDeviceId: 'camera-01', focusTarget: null }),
      { wrapper },
    );
    await waitFor(() => expect(result.current.items[0]?.device_id).toBe('camera-01'));
    await waitFor(() => expect(result.current.detail?.device_id).toBe('camera-01'));
  });

  it('debounces search and sends all list filters with bounded pagination', async () => {
    const api = fakeApi();
    const now = () => new Date('2026-09-20T03:00:00Z');
    const { result } = renderHook(() => useDeviceWorkspace({
      api, debounceMs: 1, focusTarget: null, now,
    }));
    await waitFor(() => expect(api.listDevices).toHaveBeenCalledTimes(1));

    act(() => result.current.patchFilters({
      search: 'camera',
      connectionStatus: 'online',
      operationMode: 'maintenance',
      areaId: 'east-gate',
      lifecycle: 'retired',
    }));
    await waitFor(() => expect(
      vi.mocked(api.listDevices).mock.calls.some(([query]) => query.search === 'camera'),
    ).toBe(true));
    expect(vi.mocked(api.listDevices).mock.calls.at(-1)?.[0]).toEqual({
      search: 'camera',
      connection_status: 'online',
      operation_mode: 'maintenance',
      area_id: 'east-gate',
      retired: true,
      limit: 24,
      offset: 0,
    });
  });

  it('loads more in stable pages without duplicating an existing device', async () => {
    const second = listItem({ device_id: 'door-01', display_name: '门禁' });
    const api = fakeApi({
      listDevices: vi.fn()
        .mockResolvedValueOnce(listResponse([listItem()], { total: 2 }))
        .mockResolvedValueOnce(listResponse([listItem(), second], { total: 2, offset: 1 })),
    });
    const { result } = renderHook(() => useDeviceWorkspace({ api, focusTarget: null }));
    await waitFor(() => expect(result.current.items).toHaveLength(1));
    await act(async () => { await result.current.loadMore(); });
    expect(result.current.items.map((item) => item.device_id)).toEqual(['camera-01', 'door-01']);
    expect(vi.mocked(api.listDevices).mock.calls[1][0]).toMatchObject({ offset: 1, limit: 24 });
  });

  it('prevents a slow old response from replacing a newer filtered result', async () => {
    const oldRequest = deferred<ReturnType<typeof listResponse>>();
    const fresh = listItem({ device_id: 'sensor-01', display_name: '新筛选结果' });
    const listDevices = vi.fn()
      .mockReturnValueOnce(oldRequest.promise)
      .mockResolvedValueOnce(listResponse([fresh]));
    const api = fakeApi({ listDevices });
    const { result } = renderHook(() => useDeviceWorkspace({ api, debounceMs: 1, focusTarget: null }));
    await waitFor(() => expect(listDevices).toHaveBeenCalledTimes(1));
    act(() => result.current.patchFilters({ search: 'sensor' }));
    await waitFor(() => expect(listDevices).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(result.current.items[0]?.device_id).toBe('sensor-01'));

    await act(async () => { oldRequest.resolve(listResponse([listItem()])); await oldRequest.promise; });
    expect(result.current.items[0]?.device_id).toBe('sensor-01');
  });

  it('auto-selects the query device, refreshes on focus, and reports a missing query id', async () => {
    let focusListener: EventListener | null = null;
    const focusTarget = {
      addEventListener: vi.fn((_type: string, listener: EventListener) => { focusListener = listener; }),
      removeEventListener: vi.fn(),
    } as unknown as Window;
    const onInvalidSelection = vi.fn();
    const api = fakeApi();
    const { result, rerender } = renderHook(
      ({ initial }) => useDeviceWorkspace({
        api,
        initialDeviceId: initial,
        onInvalidSelection,
        focusTarget,
      }),
      { initialProps: { initial: 'camera-01' as string | null } },
    );
    await waitFor(() => expect(result.current.detail?.device_id).toBe('camera-01'));
    act(() => focusListener?.(new Event('focus')));
    await waitFor(() => expect(api.listDevices).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(api.getDevice).toHaveBeenCalledTimes(2));

    vi.mocked(api.getDevice).mockRejectedValueOnce(
      new (await import('../../api/v3Devices')).DeviceApiError('not_found', 'missing'),
    );
    rerender({ initial: 'missing-01' });
    await waitFor(() => expect(onInvalidSelection).toHaveBeenCalledWith('missing-01'));
    expect(result.current.selectionNotice).toContain('不存在或已被删除');
  });

  it('aborts unfinished list and detail requests on unmount', async () => {
    let listSignal: AbortSignal | undefined;
    let detailSignal: AbortSignal | undefined;
    const never = new Promise<never>(() => {});
    const api = fakeApi({
      listDevices: vi.fn((_query, signal) => { listSignal = signal; return never; }),
      getDevice: vi.fn((_id, signal) => { detailSignal = signal; return never; }),
    });
    const { unmount } = renderHook(() => useDeviceWorkspace({
      api, initialDeviceId: 'camera-01', focusTarget: null,
    }));
    await waitFor(() => expect(listSignal).toBeDefined());
    await waitFor(() => expect(detailSignal).toBeDefined());
    unmount();
    expect(listSignal?.aborted).toBe(true);
    expect(detailSignal?.aborted).toBe(true);
  });

  it('keeps a legal empty result empty and applies server-returned mutation state', async () => {
    const created = {
      ...deviceDetail,
      device_id: 'new-device',
      display_name: '新设备',
      connection_status: 'unknown' as const,
      profile_version: 1,
      state_version: 0,
    };
    const api = fakeApi({
      listDevices: vi.fn().mockResolvedValue(listResponse([])),
      createDevice: vi.fn().mockResolvedValue(created),
    });
    const onSelectionChange = vi.fn();
    const { result } = renderHook(() => useDeviceWorkspace({
      api, focusTarget: null, onSelectionChange,
    }));
    await waitFor(() => expect(result.current.listLoading).toBe(false));
    expect(result.current.items).toEqual([]);
    await act(async () => {
      await result.current.createDevice({
        device_id: 'new-device', mac: 'AA:BB:CC:DD:EE:11', display_name: '新设备',
        device_type: 'sensor', area_id: null, importance: 'normal', profile_source: 'physical',
      });
    });
    expect(result.current.detail).toMatchObject({
      device_id: 'new-device', connection_status: 'unknown', profile_version: 1,
    });
    expect(onSelectionChange).toHaveBeenCalledWith('new-device');
  });

  it('clears the selected device and refreshes the real list after deletion', async () => {
    const api = fakeApi();
    const onSelectionChange = vi.fn();
    const { result } = renderHook(() => useDeviceWorkspace({
      api,
      initialDeviceId: 'camera-01',
      focusTarget: null,
      onSelectionChange,
    }));
    await waitFor(() => expect(result.current.detail?.device_id).toBe('camera-01'));
    await act(async () => {
      await result.current.deleteDevice('camera-01', '东门摄像头');
    });
    expect(result.current.detail).toBeNull();
    expect(result.current.selectedId).toBeNull();
    expect(onSelectionChange).toHaveBeenLastCalledWith(null);
    expect(api.deleteDevice).toHaveBeenCalledWith(
      'camera-01',
      '东门摄像头',
      expect.any(AbortSignal),
    );
    expect(api.listDevices).toHaveBeenCalledTimes(2);
  });
});
