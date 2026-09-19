"""Managed MQTT heartbeat subscription without import-time network side effects."""
from dataclasses import dataclass
from datetime import datetime, timezone
import importlib
import logging
import os
from queue import Empty, Full, Queue
import random
import ssl
import threading
from typing import Any, Callable, Mapping

from config import (
    MqttConfigurationError,
    MqttSubscriberSettings,
    mqtt_subscriber_settings,
)
from services.device_state import DeviceStateService
from services.mqtt_ingestion import MqttHeartbeatIngestor


HEARTBEAT_TOPIC = "community/+/status"
COMPONENT_ID = "mqtt-heartbeat-subscriber"
_STOP = object()


class MqttDependencyError(RuntimeError):
    """Raised only when enabled MQTT requires an unavailable runtime dependency."""


@dataclass(frozen=True)
class _QueuedHeartbeat:
    topic: str
    payload: bytes
    received_at: datetime


@dataclass(frozen=True)
class _HealthTransition:
    readiness: str
    reason: str | None


def create_paho_client(settings: MqttSubscriberSettings):
    """Create a configured Paho client, importing Paho only when called."""
    settings.validate()
    try:
        mqtt = importlib.import_module("paho.mqtt.client")
    except (ImportError, ModuleNotFoundError) as exc:
        raise MqttDependencyError(
            "paho-mqtt is required when the MQTT subscriber is enabled"
        ) from exc

    client = mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        client_id=settings.client_id,
        clean_session=True,
        protocol=mqtt.MQTTv311,
    )
    client.username_pw_set(settings.username, settings.password)
    if settings.tls_enabled:
        context = ssl.create_default_context(cafile=settings.ca_file)
        context.check_hostname = True
        context.verify_mode = ssl.CERT_REQUIRED
        client.tls_set_context(context)
    client.reconnect_delay_set(
        min_delay=settings.reconnect_min_seconds,
        max_delay=settings.reconnect_max_seconds,
    )
    return client


def _numeric_code(value: Any) -> int | None:
    candidate = getattr(value, "value", value)
    try:
        return int(candidate)
    except (TypeError, ValueError):
        return None


def _connect_succeeded(reason_code: Any) -> bool:
    failure = getattr(reason_code, "is_failure", None)
    if failure is not None:
        return not bool(failure)
    return _numeric_code(reason_code) == 0


def _subscription_failed(reason_code: Any) -> bool:
    failure = getattr(reason_code, "is_failure", None)
    if failure is not None:
        return bool(failure)
    code = _numeric_code(reason_code)
    return code is None or code >= 128


def _reloader_parent(environment: Mapping[str, str]) -> bool:
    debug = environment.get("IOT_IDS_FLASK_DEBUG", "false").strip().lower()
    if debug not in {"1", "true", "yes", "on"}:
        return False
    child = environment.get("WERKZEUG_RUN_MAIN", "").strip().lower()
    return child not in {"1", "true"}


class ManagedMqttHeartbeatSubscriber:
    """Explicitly managed network client feeding the existing ingestion service."""

    def __init__(
        self,
        state_service: DeviceStateService,
        *,
        settings_provider: Callable[[], MqttSubscriberSettings] = mqtt_subscriber_settings,
        client_factory: Callable[[MqttSubscriberSettings], Any] = create_paho_client,
        ingestor: MqttHeartbeatIngestor | None = None,
        clock: Callable[[], datetime] | None = None,
        jitter_source: Callable[[float, float], float] | None = None,
        logger: logging.Logger | None = None,
        environment: Mapping[str, str] | None = None,
        worker_join_timeout: float = 5.0,
    ):
        self.state_service = state_service
        self._settings_provider = settings_provider
        self._client_factory = client_factory
        self._ingestor = ingestor or MqttHeartbeatIngestor(state_service)
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._jitter_source = jitter_source or random.uniform
        self._logger = logger or logging.getLogger(__name__)
        self._environment = os.environ if environment is None else environment
        self._worker_join_timeout = worker_join_timeout

        self._lifecycle_lock = threading.RLock()
        self._active = False
        self._client = None
        self._settings: MqttSubscriberSettings | None = None
        self._message_queue: Queue | None = None
        self._health_events: Queue | None = None
        self._stop_event: threading.Event | None = None
        self._worker: threading.Thread | None = None
        self._loop_started = False
        self._subscription_mid: int | None = None
        self._reconnect_attempt = 0

    @property
    def is_running(self) -> bool:
        with self._lifecycle_lock:
            return self._active

    def start(self) -> bool:
        """Start once; return false when disabled or unable to start safely."""
        with self._lifecycle_lock:
            if self._active:
                return True
            try:
                settings = self._settings_provider()
                if not isinstance(settings, MqttSubscriberSettings):
                    raise MqttConfigurationError(
                        "MQTT settings provider returned an invalid object"
                    )
                settings.validate()
            except Exception as exc:
                self._logger.error(
                    "mqtt_subscriber_start_failed code=configuration_error type=%s",
                    type(exc).__name__,
                )
                self._set_health_direct("degraded", "configuration_error")
                return False

            if not settings.enabled:
                self._logger.info("mqtt_subscriber_disabled")
                return False
            if _reloader_parent(self._environment):
                self._logger.info("mqtt_subscriber_skipped_in_reloader_parent")
                return False
            if not settings.tls_enabled:
                self._logger.warning(
                    "mqtt_plaintext_enabled restrict_usage_to_isolated_trusted_lan"
                )

            self._set_health_direct(
                "warming_up", "waiting_for_connection", restarted=True
            )
            try:
                client = self._client_factory(settings)
            except Exception as exc:
                self._logger.error(
                    "mqtt_subscriber_start_failed code=client_creation_failed type=%s",
                    type(exc).__name__,
                )
                self._set_health_direct("degraded", "client_creation_failed")
                return False

            self._settings = settings
            self._client = client
            self._message_queue = Queue(maxsize=settings.queue_size)
            self._health_events = Queue(maxsize=32)
            self._stop_event = threading.Event()
            self._subscription_mid = None
            self._reconnect_attempt = 0
            self._loop_started = False
            self._active = True
            self._install_callbacks(client)
            self._worker = threading.Thread(
                target=self._worker_loop,
                name="mqtt-heartbeat-worker",
                daemon=True,
            )
            self._worker.start()

            try:
                result = client.connect_async(
                    settings.host,
                    settings.port,
                    settings.keepalive,
                )
                if result not in (None, 0):
                    raise RuntimeError("connect_async returned failure")
                loop_result = client.loop_start()
                if loop_result not in (None, 0):
                    raise RuntimeError("loop_start returned failure")
                self._loop_started = True
            except Exception as exc:
                self._logger.error(
                    "mqtt_subscriber_start_failed code=network_start_failed type=%s",
                    type(exc).__name__,
                )
                self._set_health_direct("degraded", "network_start_failed")
                self._stop_locked()
                return False
            return True

    def stop(self) -> None:
        """Stop network and worker resources; repeated calls are harmless."""
        with self._lifecycle_lock:
            self._stop_locked()

    def _stop_locked(self) -> None:
        if not self._active and self._client is None and self._worker is None:
            return
        self._active = False
        stop_event = self._stop_event
        client = self._client
        worker = self._worker
        message_queue = self._message_queue

        if stop_event is not None:
            stop_event.set()
        if client is not None:
            try:
                client.disconnect()
            except Exception as exc:
                self._logger.warning(
                    "mqtt_subscriber_stop_issue code=disconnect_failed type=%s",
                    type(exc).__name__,
                )
            if self._loop_started:
                try:
                    client.loop_stop()
                except Exception as exc:
                    self._logger.warning(
                        "mqtt_subscriber_stop_issue code=loop_stop_failed type=%s",
                        type(exc).__name__,
                    )

        if message_queue is not None:
            while True:
                try:
                    message_queue.get_nowait()
                    message_queue.task_done()
                except Empty:
                    break
            try:
                message_queue.put_nowait(_STOP)
            except Full:
                self._logger.warning("mqtt_subscriber_stop_issue code=queue_not_drained")

        if worker is not None and worker is not threading.current_thread():
            worker.join(timeout=self._worker_join_timeout)
            if worker.is_alive():
                self._logger.error("mqtt_subscriber_stop_failed code=worker_timeout")
                self._set_health_direct("degraded", "worker_stop_timeout")

        self._client = None
        self._settings = None
        self._message_queue = None
        self._health_events = None
        self._stop_event = None
        self._worker = None
        self._loop_started = False
        self._subscription_mid = None

    def _install_callbacks(self, client: Any) -> None:
        client.on_connect = self._on_connect
        client.on_connect_fail = self._on_connect_fail
        client.on_disconnect = self._on_disconnect
        client.on_subscribe = self._on_subscribe
        client.on_message = self._on_message

    def _on_connect(
        self,
        client: Any,
        _userdata: Any,
        _flags: Any,
        reason_code: Any,
        _properties: Any = None,
    ) -> None:
        try:
            if not self._active:
                return
            if not _connect_succeeded(reason_code):
                delay = self._configure_next_reconnect(client)
                self._enqueue_health(
                    "degraded", f"connection_rejected_reconnect_in_{delay:.2f}s"
                )
                return
            self._reconnect_attempt = 0
            settings = self._settings
            if settings is None:
                self._enqueue_health("degraded", "missing_runtime_settings")
                return
            result = client.subscribe(HEARTBEAT_TOPIC, qos=settings.qos)
            result_code, mid = result
            if result_code != 0:
                self._enqueue_health("degraded", "subscription_request_failed")
                return
            self._subscription_mid = int(mid)
        except Exception as exc:
            self._logger.error(
                "mqtt_callback_failed callback=connect type=%s", type(exc).__name__
            )
            self._enqueue_health("degraded", "connect_callback_error")

    def _on_connect_fail(self, client: Any, _userdata: Any) -> None:
        try:
            if not self._active:
                return
            delay = self._configure_next_reconnect(client)
            self._enqueue_health(
                "degraded", f"connection_failed_reconnect_in_{delay:.2f}s"
            )
        except Exception as exc:
            self._logger.error(
                "mqtt_callback_failed callback=connect_fail type=%s",
                type(exc).__name__,
            )
            self._enqueue_health("degraded", "connect_fail_callback_error")

    def _on_disconnect(
        self,
        client: Any,
        _userdata: Any,
        _disconnect_flags: Any,
        _reason_code: Any,
        _properties: Any = None,
    ) -> None:
        try:
            if not self._active:
                return
            self._subscription_mid = None
            delay = self._configure_next_reconnect(client)
            self._enqueue_health(
                "degraded", f"disconnected_reconnect_in_{delay:.2f}s"
            )
        except Exception as exc:
            self._logger.error(
                "mqtt_callback_failed callback=disconnect type=%s",
                type(exc).__name__,
            )
            self._enqueue_health("degraded", "disconnect_callback_error")

    def _on_subscribe(
        self,
        _client: Any,
        _userdata: Any,
        mid: int,
        reason_codes: Any,
        _properties: Any = None,
    ) -> None:
        try:
            if not self._active:
                return
            expected_mid = self._subscription_mid
            if expected_mid != int(mid):
                self._enqueue_health("degraded", "unexpected_subscription_ack")
                return
            codes = (
                reason_codes
                if isinstance(reason_codes, (list, tuple))
                else [reason_codes]
            )
            if not codes or any(_subscription_failed(code) for code in codes):
                self._enqueue_health("degraded", "subscription_rejected")
                return
            self._enqueue_health("ready", None)
        except Exception as exc:
            self._logger.error(
                "mqtt_callback_failed callback=subscribe type=%s",
                type(exc).__name__,
            )
            self._enqueue_health("degraded", "subscribe_callback_error")

    def _on_message(self, _client: Any, _userdata: Any, message: Any) -> None:
        try:
            if not self._active:
                return
            if bool(getattr(message, "retain", False)):
                self._logger.warning("mqtt_message_rejected code=retained_heartbeat")
                return
            message_queue = self._message_queue
            if message_queue is None:
                self._enqueue_health("degraded", "message_queue_unavailable")
                return
            item = _QueuedHeartbeat(
                topic=str(message.topic),
                payload=bytes(message.payload),
                received_at=self._clock(),
            )
            try:
                message_queue.put_nowait(item)
            except Full:
                self._logger.error("mqtt_message_dropped code=queue_full")
                self._enqueue_health("degraded", "message_queue_overflow")
        except Exception as exc:
            self._logger.error(
                "mqtt_callback_failed callback=message type=%s", type(exc).__name__
            )
            self._enqueue_health("degraded", "message_callback_error")

    def _configure_next_reconnect(self, client: Any) -> float:
        settings = self._settings
        attempt = self._reconnect_attempt
        self._reconnect_attempt += 1
        if settings is None:
            return 0.0
        exponent = min(attempt, 30)
        base = min(
            settings.reconnect_max_seconds,
            settings.reconnect_min_seconds * (2 ** exponent),
        )
        spread = base * settings.reconnect_jitter_ratio
        low = max(settings.reconnect_min_seconds, base - spread)
        high = min(settings.reconnect_max_seconds, base + spread)
        delay = min(
            settings.reconnect_max_seconds,
            max(settings.reconnect_min_seconds, self._jitter_source(low, high)),
        )
        client.reconnect_delay_set(min_delay=delay, max_delay=delay)
        return delay

    def _enqueue_health(self, readiness: str, reason: str | None) -> None:
        health_events = self._health_events
        if health_events is not None:
            transition = _HealthTransition(readiness, reason)
            try:
                health_events.put_nowait(transition)
            except Full:
                try:
                    health_events.get_nowait()
                except Empty:
                    pass
                try:
                    health_events.put_nowait(transition)
                except Full:
                    pass

    def _worker_loop(self) -> None:
        message_queue = self._message_queue
        stop_event = self._stop_event
        if message_queue is None or stop_event is None:
            return
        while True:
            self._drain_health_events()
            try:
                item = message_queue.get(timeout=0.05)
            except Empty:
                if stop_event.is_set():
                    break
                continue
            try:
                if item is _STOP:
                    break
                if stop_event.is_set():
                    continue
                try:
                    result = self._ingestor.ingest(
                        topic=item.topic,
                        payload=item.payload,
                        received_at=item.received_at,
                    )
                    if not result.accepted:
                        code = self._safe_result_code(result.code)
                        self._logger.warning(
                            "mqtt_message_rejected code=%s", code
                        )
                except Exception as exc:
                    self._logger.error(
                        "mqtt_worker_failed code=ingestion_exception type=%s",
                        type(exc).__name__,
                    )
                    self._set_health_direct("degraded", "worker_ingestion_error")
            finally:
                message_queue.task_done()
        self._drain_health_events()

    def _drain_health_events(self) -> None:
        health_events = self._health_events
        if health_events is None:
            return
        while True:
            try:
                transition = health_events.get_nowait()
            except Empty:
                return
            self._set_health_direct(transition.readiness, transition.reason)

    @staticmethod
    def _safe_result_code(value: Any) -> str:
        code = str(value)
        if not code or len(code) > 64:
            return "invalid_result_code"
        if not all(character.isalnum() or character in "_-" for character in code):
            return "invalid_result_code"
        return code

    def _set_health_direct(
        self,
        readiness: str,
        reason: str | None,
        *,
        restarted: bool = False,
    ) -> None:
        try:
            if restarted:
                self.state_service.restart_component(COMPONENT_ID, reason)
            else:
                self.state_service.set_component_readiness(
                    COMPONENT_ID, readiness, reason
                )
        except Exception as exc:
            self._logger.error(
                "mqtt_health_update_failed type=%s", type(exc).__name__
            )


__all__ = [
    "COMPONENT_ID",
    "HEARTBEAT_TOPIC",
    "ManagedMqttHeartbeatSubscriber",
    "MqttDependencyError",
    "create_paho_client",
]
