import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  IncidentApiError,
  v3IncidentsApi,
  type CreateIncidentInput,
  type HelpCategory,
  type HelpRequestDetail,
  type HelpRequestItem,
  type HelpStatus,
  type IncidentsApi,
  type IncidentDetail,
  type IncidentListItem,
  type IncidentSeverity,
  type IncidentSource,
  type IncidentStatus,
  type SupportContact,
  type TransitionIncidentInput,
  type UpdateHelpRequestInput,
  type UpdateSupportContactInput,
} from '../../api/v3Incidents';
import { parseMonitorEvent, parseSnapshotRequired, type RealtimeEventType } from '../../api/v3Monitor';

export interface IncidentFilters {
  search: string;
  status: 'all' | IncidentStatus;
  severity: 'all' | IncidentSeverity;
  source: 'all' | IncidentSource;
  deviceId: string;
  from: string;
  to: string;
}

export interface HelpFilters {
  status: 'all' | HelpStatus;
  category: 'all' | HelpCategory;
  userId: string;
  deviceId: string;
  incidentId: string;
  from: string;
  to: string;
}

export const EMPTY_INCIDENT_FILTERS: IncidentFilters = {
  search: '', status: 'all', severity: 'all', source: 'all', deviceId: '', from: '', to: '',
};
export const EMPTY_HELP_FILTERS: HelpFilters = {
  status: 'all', category: 'all', userId: '', deviceId: '', incidentId: '', from: '', to: '',
};

export interface WorkspaceEventSource {
  onopen: ((event: Event) => void) | null;
  onerror: ((event: Event) => void) | null;
  addEventListener(type: string, listener: EventListener): void;
  close(): void;
}

interface Options {
  api?: IncidentsApi;
  initialIncidentId?: string | null;
  onSelectionChange?: (id: string | null) => void;
  eventSourceFactory?: (url: string) => WorkspaceEventSource;
  debounceMs?: number;
  pageSize?: number;
  helpActive?: boolean;
  setTimer?: typeof window.setTimeout;
  clearTimer?: typeof window.clearTimeout;
  now?: () => Date;
}

const INCIDENT_EVENTS: RealtimeEventType[] = [
  'incident.opened', 'incident.updated', 'incident.recovering', 'incident.resolved',
];

const systemNow = () => new Date();
const sourceFactory = (url: string): WorkspaceEventSource => new EventSource(url);
const browserSetTimer = (callback: () => void, delay: number) => window.setTimeout(callback, delay);
const browserClearTimer = (timer: number) => window.clearTimeout(timer);

function normalized(error: unknown): IncidentApiError {
  return error instanceof IncidentApiError
    ? error
    : new IncidentApiError('network', '事件工作区发生未知错误', { cause: error });
}

function aborted(error: unknown): boolean {
  return error instanceof IncidentApiError && error.kind === 'aborted';
}

function utc(value: string): string | undefined {
  if (!value) return undefined;
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toISOString();
}

export function useIncidentWorkspace(options: Options = {}) {
  const api = options.api ?? v3IncidentsApi;
  const pageSize = options.pageSize ?? 25;
  const now = options.now ?? systemNow;
  const sourceFactoryRef = useRef(options.eventSourceFactory ?? sourceFactory);
  sourceFactoryRef.current = options.eventSourceFactory ?? sourceFactory;
  const selectionCallbackRef = useRef(options.onSelectionChange);
  selectionCallbackRef.current = options.onSelectionChange;
  const timerFactoryRef = useRef(options.setTimer ?? browserSetTimer);
  timerFactoryRef.current = options.setTimer ?? browserSetTimer;
  const clearTimerRef = useRef(options.clearTimer ?? browserClearTimer);
  clearTimerRef.current = options.clearTimer ?? browserClearTimer;
  const setTimer = useCallback(
    (callback: () => void, delay: number) => timerFactoryRef.current(callback, delay),
    [],
  );
  const clearTimer = useCallback(
    (timer: number) => clearTimerRef.current(timer),
    [],
  );
  const [incidentFilters, setIncidentFilters] = useState(EMPTY_INCIDENT_FILTERS);
  const [debouncedSearch, setDebouncedSearch] = useState('');
  const [incidentPage, setIncidentPage] = useState(0);
  const incidentPageRef = useRef(0);
  const [incidents, setIncidents] = useState<IncidentListItem[]>([]);
  const [incidentTotal, setIncidentTotal] = useState(0);
  const [incidentLoading, setIncidentLoading] = useState(true);
  const [incidentError, setIncidentError] = useState<IncidentApiError | null>(null);
  const [incidentUpdatedAt, setIncidentUpdatedAt] = useState<string | null>(null);
  const [selectedIncidentId, setSelectedIncidentId] = useState(options.initialIncidentId ?? null);
  const selectedIncidentIdRef = useRef(options.initialIncidentId ?? null);
  const [incidentDetail, setIncidentDetail] = useState<IncidentDetail | null>(null);
  const [incidentDetailLoading, setIncidentDetailLoading] = useState(false);
  const [incidentDetailError, setIncidentDetailError] = useState<IncidentApiError | null>(null);
  const [mutationPending, setMutationPending] = useState(false);
  const mutationRef = useRef(false);
  const [realtime, setRealtime] = useState<'idle' | 'connecting' | 'connected' | 'disconnected'>('idle');
  const [stale, setStale] = useState(false);

  const [helpFilters, setHelpFilters] = useState(EMPTY_HELP_FILTERS);
  const [helpPage, setHelpPage] = useState(0);
  const helpPageRef = useRef(0);
  const [helpItems, setHelpItems] = useState<HelpRequestItem[]>([]);
  const [helpTotal, setHelpTotal] = useState(0);
  const [helpLoading, setHelpLoading] = useState(false);
  const [helpError, setHelpError] = useState<IncidentApiError | null>(null);
  const [selectedHelpId, setSelectedHelpId] = useState<string | null>(null);
  const selectedHelpIdRef = useRef<string | null>(null);
  const [helpDetail, setHelpDetail] = useState<HelpRequestDetail | null>(null);
  const [helpDetailLoading, setHelpDetailLoading] = useState(false);
  const [helpDetailError, setHelpDetailError] = useState<IncidentApiError | null>(null);
  const [contact, setContact] = useState<SupportContact | null>(null);
  const [contactLoading, setContactLoading] = useState(false);
  const [contactError, setContactError] = useState<IncidentApiError | null>(null);

  const mounted = useRef(true);
  const listAbort = useRef<AbortController | null>(null);
  const detailAbort = useRef<AbortController | null>(null);
  const helpAbort = useRef<AbortController | null>(null);
  const helpDetailAbort = useRef<AbortController | null>(null);
  const contactAbort = useRef<AbortController | null>(null);
  const mutationAbort = useRef<AbortController | null>(null);
  const listSequence = useRef(0);
  const detailSequence = useRef(0);
  const helpSequence = useRef(0);
  const helpDetailSequence = useRef(0);
  const sourceRef = useRef<WorkspaceEventSource | null>(null);
  const refreshTimer = useRef<number | null>(null);
  const lastEventId = useRef(0);
  const initialSelectionAttempt = useRef<string | null>(null);
  const runIncidentListRef = useRef<(page?: number) => Promise<void>>(async () => {});

  useEffect(() => {
    const timer = window.setTimeout(
      () => setDebouncedSearch(incidentFilters.search.trim()), options.debounceMs ?? 300,
    );
    return () => window.clearTimeout(timer);
  }, [incidentFilters.search, options.debounceMs]);

  const incidentQuery = useMemo(() => ({
    search: debouncedSearch || undefined,
    status: incidentFilters.status === 'all' ? undefined : incidentFilters.status,
    severity: incidentFilters.severity === 'all' ? undefined : incidentFilters.severity,
    source: incidentFilters.source === 'all' ? undefined : incidentFilters.source,
    device_id: incidentFilters.deviceId.trim() || undefined,
    from: utc(incidentFilters.from), to: utc(incidentFilters.to),
  }), [debouncedSearch, incidentFilters]);

  const closeSource = useCallback(() => {
    sourceRef.current?.close();
    sourceRef.current = null;
  }, []);

  const scheduleRealtimeRefresh = useCallback((reason: string) => {
    if (refreshTimer.current !== null) return;
    setStale(true);
    refreshTimer.current = setTimer(() => {
      refreshTimer.current = null;
      if (mounted.current) void runIncidentListRef.current(incidentPageRef.current);
    }, reason === 'snapshot.required' ? 0 : 250);
  }, [setTimer]);

  const connect = useCallback((cursor: number) => {
    closeSource();
    lastEventId.current = Math.max(lastEventId.current, cursor);
    const source = sourceFactoryRef.current(`/api/v3/events?after=${cursor}`);
    sourceRef.current = source;
    setRealtime('connecting');
    source.onopen = () => {
      if (sourceRef.current !== source || !mounted.current) return;
      setRealtime('connected');
      setStale(false);
    };
    source.onerror = () => {
      if (sourceRef.current !== source || !mounted.current) return;
      setRealtime('disconnected');
      setStale(true);
    };
    INCIDENT_EVENTS.forEach((eventName) => {
      source.addEventListener(eventName, ((message: MessageEvent<string>) => {
        if (sourceRef.current !== source || !mounted.current) return;
        try {
          const event = parseMonitorEvent(eventName, message.data);
          if (event.event_id <= lastEventId.current) return;
          lastEventId.current = event.event_id;
          scheduleRealtimeRefresh(eventName);
        } catch {
          scheduleRealtimeRefresh('invalid-event');
        }
      }) as EventListener);
    });
    source.addEventListener('snapshot.required', ((message: MessageEvent<string>) => {
      if (sourceRef.current !== source || !mounted.current) return;
      try { parseSnapshotRequired(message.data); } catch { /* full refresh is still safest */ }
      closeSource();
      scheduleRealtimeRefresh('snapshot.required');
    }) as EventListener);
  }, [closeSource, scheduleRealtimeRefresh]);

  const selectIncident = useCallback(async (id: string | null) => {
    selectedIncidentIdRef.current = id;
    initialSelectionAttempt.current = id;
    setSelectedIncidentId(id);
    selectionCallbackRef.current?.(id);
    detailAbort.current?.abort();
    const sequence = ++detailSequence.current;
    if (!id) {
      setIncidentDetail(null); setIncidentDetailError(null); setIncidentDetailLoading(false); return;
    }
    const controller = new AbortController();
    detailAbort.current = controller;
    setIncidentDetailLoading(true); setIncidentDetailError(null);
    try {
      const detail = await api.getIncident(id, controller.signal);
      if (!mounted.current || sequence !== detailSequence.current) return;
      setIncidentDetail(detail);
    } catch (error) {
      if (!mounted.current || sequence !== detailSequence.current || aborted(error)) return;
      const result = normalized(error);
      setIncidentDetailError(result);
      if (result.kind === 'not_found') {
        setSelectedIncidentId(null); selectedIncidentIdRef.current = null; setIncidentDetail(null);
        options.onSelectionChange?.(null);
      }
    } finally {
      if (mounted.current && sequence === detailSequence.current) setIncidentDetailLoading(false);
    }
  }, [api]);

  const runIncidentList = useCallback(async (page: number) => {
    const sequence = ++listSequence.current;
    listAbort.current?.abort();
    const controller = new AbortController();
    listAbort.current = controller;
    setIncidentLoading(true); setIncidentError(null);
    try {
      const result = await api.listIncidents({
        ...incidentQuery, limit: pageSize, offset: page * pageSize,
      }, controller.signal);
      if (!mounted.current || sequence !== listSequence.current) return;
      incidentPageRef.current = page;
      setIncidents(result.items); setIncidentTotal(result.total); setIncidentPage(page);
      setIncidentUpdatedAt(now().toISOString());
      setStale(false);
      connect(result.event_cursor);
      if (selectedIncidentIdRef.current) void selectIncident(selectedIncidentIdRef.current);
    } catch (error) {
      if (!mounted.current || sequence !== listSequence.current || aborted(error)) return;
      setIncidentError(normalized(error));
      setStale(true);
    } finally {
      if (mounted.current && sequence === listSequence.current) setIncidentLoading(false);
    }
  }, [api, connect, incidentQuery, now, pageSize, selectIncident]);
  runIncidentListRef.current = runIncidentList;

  useEffect(() => { void runIncidentList(0); }, [incidentQuery, runIncidentList]);

  useEffect(() => {
    if (!options.initialIncidentId) { initialSelectionAttempt.current = null; return; }
    if (initialSelectionAttempt.current === options.initialIncidentId) return;
    initialSelectionAttempt.current = options.initialIncidentId;
    void selectIncident(options.initialIncidentId);
  }, [options.initialIncidentId, selectIncident]);

  const runMutation = useCallback(async <T,>(operation: (signal: AbortSignal) => Promise<T>) => {
    if (mutationRef.current) throw new IncidentApiError('conflict', '已有操作正在提交');
    mutationRef.current = true; mutationAbort.current?.abort();
    const controller = new AbortController(); mutationAbort.current = controller; setMutationPending(true);
    try { return await operation(controller.signal); }
    finally { mutationRef.current = false; if (mounted.current) setMutationPending(false); }
  }, []);

  const acceptIncident = useCallback((detail: IncidentDetail) => {
    setIncidentDetail(detail); setIncidentDetailError(null);
    selectedIncidentIdRef.current = detail.incident_id; setSelectedIncidentId(detail.incident_id);
    selectionCallbackRef.current?.(detail.incident_id);
    void runIncidentList(incidentPageRef.current);
  }, [runIncidentList]);

  const createIncident = useCallback(async (input: CreateIncidentInput) => {
    const detail = await runMutation((signal) => api.createIncident(input, signal));
    acceptIncident(detail); return detail;
  }, [acceptIncident, api, runMutation]);

  const transitionIncident = useCallback(async (
    action: 'ack' | 'recovering' | 'resolve' | 'false-positive', input: TransitionIncidentInput,
  ) => {
    const id = selectedIncidentIdRef.current;
    if (!id) throw new IncidentApiError('not_found', '尚未选择事件');
    const detail = await runMutation((signal) => api.transitionIncident(id, action, input, signal));
    acceptIncident(detail); return detail;
  }, [acceptIncident, api, runMutation]);

  const helpQuery = useMemo(() => ({
    status: helpFilters.status === 'all' ? undefined : helpFilters.status,
    category: helpFilters.category === 'all' ? undefined : helpFilters.category,
    user_id: helpFilters.userId ? Number(helpFilters.userId) : undefined,
    device_id: helpFilters.deviceId.trim() || undefined,
    incident_id: helpFilters.incidentId.trim() || undefined,
    from: utc(helpFilters.from), to: utc(helpFilters.to),
  }), [helpFilters]);

  const runHelpList = useCallback(async (page: number) => {
    const sequence = ++helpSequence.current; helpAbort.current?.abort();
    const controller = new AbortController(); helpAbort.current = controller;
    setHelpLoading(true); setHelpError(null);
    try {
      const result = await api.listHelpRequests({ ...helpQuery, limit: pageSize, offset: page * pageSize }, controller.signal);
      if (!mounted.current || sequence !== helpSequence.current) return;
      helpPageRef.current = page;
      setHelpItems(result.items); setHelpTotal(result.total); setHelpPage(page);
    } catch (error) {
      if (!mounted.current || sequence !== helpSequence.current || aborted(error)) return;
      setHelpError(normalized(error));
    } finally { if (mounted.current && sequence === helpSequence.current) setHelpLoading(false); }
  }, [api, helpQuery, pageSize]);

  useEffect(() => {
    if (options.helpActive) void runHelpList(0);
  }, [options.helpActive, helpQuery, runHelpList]);

  const selectHelp = useCallback(async (id: string | null) => {
    selectedHelpIdRef.current = id; setSelectedHelpId(id); helpDetailAbort.current?.abort();
    const sequence = ++helpDetailSequence.current;
    if (!id) { setHelpDetail(null); setHelpDetailError(null); return; }
    const controller = new AbortController(); helpDetailAbort.current = controller;
    setHelpDetailLoading(true); setHelpDetailError(null);
    try {
      const detail = await api.getHelpRequest(id, controller.signal);
      if (mounted.current && sequence === helpDetailSequence.current) setHelpDetail(detail);
    } catch (error) {
      if (!mounted.current || sequence !== helpDetailSequence.current || aborted(error)) return;
      setHelpDetailError(normalized(error));
    } finally { if (mounted.current && sequence === helpDetailSequence.current) setHelpDetailLoading(false); }
  }, [api]);

  const updateHelp = useCallback(async (input: UpdateHelpRequestInput) => {
    const id = selectedHelpIdRef.current;
    if (!id) throw new IncidentApiError('not_found', '尚未选择求助请求');
    const detail = await runMutation((signal) => api.updateHelpRequest(id, input, signal));
    setHelpDetail(detail); void runHelpList(helpPageRef.current); return detail;
  }, [api, runHelpList, runMutation]);

  const loadContact = useCallback(async () => {
    contactAbort.current?.abort(); const controller = new AbortController(); contactAbort.current = controller;
    setContactLoading(true); setContactError(null);
    try { const result = await api.getSupportContact(controller.signal); if (mounted.current) setContact(result); }
    catch (error) { if (!aborted(error) && mounted.current) setContactError(normalized(error)); }
    finally { if (mounted.current) setContactLoading(false); }
  }, [api]);

  const updateContact = useCallback(async (input: UpdateSupportContactInput) => {
    const result = await runMutation((signal) => api.updateSupportContact(input, signal));
    setContact(result); return result;
  }, [api, runMutation]);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false; listSequence.current += 1; detailSequence.current += 1;
      helpSequence.current += 1; helpDetailSequence.current += 1;
      listAbort.current?.abort(); detailAbort.current?.abort(); helpAbort.current?.abort();
      helpDetailAbort.current?.abort(); contactAbort.current?.abort(); mutationAbort.current?.abort();
      closeSource(); if (refreshTimer.current !== null) clearTimer(refreshTimer.current);
    };
  }, [clearTimer, closeSource]);

  return {
    incidentFilters, setIncidentFilters, clearIncidentFilters: () => setIncidentFilters(EMPTY_INCIDENT_FILTERS),
    incidents, incidentTotal, incidentPage, incidentLoading, incidentError, incidentUpdatedAt,
    setIncidentPage: (page: number) => void runIncidentList(page), refreshIncidents: () => runIncidentList(incidentPageRef.current),
    selectedIncidentId, incidentDetail, incidentDetailLoading, incidentDetailError, selectIncident,
    createIncident, transitionIncident, mutationPending, realtime, stale,
    helpFilters, setHelpFilters, clearHelpFilters: () => setHelpFilters(EMPTY_HELP_FILTERS),
    helpItems, helpTotal, helpPage, helpLoading, helpError,
    setHelpPage: (page: number) => void runHelpList(page), refreshHelp: () => runHelpList(helpPageRef.current),
    selectedHelpId, helpDetail, helpDetailLoading, helpDetailError, selectHelp, updateHelp,
    contact, contactLoading, contactError, loadContact, updateContact,
    pageSize,
  };
}

export type IncidentWorkspace = ReturnType<typeof useIncidentWorkspace>;
