"""Bounded-poll SSE formatting over the persistent realtime event store."""
import json
import time
from typing import Callable, Iterator

from services.realtime_events import ReplayBatch, RealtimeEventStore


Waiter = Callable[[float], None]
MonotonicClock = Callable[[], float]


def _json(value: dict) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def format_persisted_event(event: dict) -> str:
    data = {
        "event_id": event["event_id"],
        "event_type": event["event_type"],
        "occurred_at": event["occurred_at"],
        "device_id": event["device_id"],
        "state_version": event["state_version"],
        "payload": event["payload"],
    }
    return (
        f"id: {event['event_id']}\n"
        f"event: {event['event_type']}\n"
        f"data: {_json(data)}\n\n"
    )


def format_snapshot_required(batch: ReplayBatch) -> str:
    data = {
        "event_cursor": batch.current_cursor,
        "reason": batch.reason or "replay_unavailable",
    }
    return f"event: snapshot.required\ndata: {_json(data)}\n\n"


class SseEventStream:
    """Poll persistent events without per-client threads or unbounded queues."""

    def __init__(
        self,
        event_store: RealtimeEventStore,
        *,
        replay_limit: int = 256,
        poll_interval: float = 1.0,
        keepalive_interval: float = 15.0,
        waiter: Waiter = time.sleep,
        monotonic_clock: MonotonicClock = time.monotonic,
        max_idle_cycles: int | None = None,
    ):
        if replay_limit <= 0:
            raise ValueError("replay_limit must be positive")
        if poll_interval <= 0:
            raise ValueError("poll_interval must be positive")
        if keepalive_interval < 0:
            raise ValueError("keepalive_interval must not be negative")
        if max_idle_cycles is not None and max_idle_cycles <= 0:
            raise ValueError("max_idle_cycles must be positive when provided")
        self.event_store = event_store
        self.replay_limit = replay_limit
        self.poll_interval = poll_interval
        self.keepalive_interval = keepalive_interval
        self.waiter = waiter
        self.monotonic_clock = monotonic_clock
        self.max_idle_cycles = max_idle_cycles

    def prepare(self, after: int) -> ReplayBatch:
        return self.event_store.read_after(after, limit=self.replay_limit)

    def generate(
        self,
        after: int,
        *,
        initial_batch: ReplayBatch | None = None,
    ) -> Iterator[str]:
        cursor = after
        batch = initial_batch or self.prepare(after)
        last_keepalive = self.monotonic_clock()
        idle_cycles = 0
        try:
            while True:
                if batch.requires_snapshot:
                    yield format_snapshot_required(batch)
                    return
                if batch.events:
                    for event in batch.events:
                        yield format_persisted_event(event)
                        cursor = event["event_id"]
                    idle_cycles = 0
                else:
                    now = self.monotonic_clock()
                    if now - last_keepalive >= self.keepalive_interval:
                        yield ": keepalive\n\n"
                        last_keepalive = now
                    idle_cycles += 1
                    if (
                        self.max_idle_cycles is not None
                        and idle_cycles >= self.max_idle_cycles
                    ):
                        return
                    self.waiter(self.poll_interval)
                batch = self.prepare(cursor)
        except GeneratorExit:
            return


__all__ = [
    "SseEventStream",
    "format_persisted_event",
    "format_snapshot_required",
]
