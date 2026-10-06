from dataclasses import replace
from datetime import datetime, timezone
import importlib
import json
import logging
from pathlib import Path
import sqlite3
import ssl
import threading
import time
from types import SimpleNamespace

import pytest

from config import (
    MqttConfigurationError,
    MqttSubscriberSettings,
    mqtt_subscriber_settings,
)
from services.device_state import DeviceStateService
from services.mqtt_subscriber import (
    COMPONENT_ID,
    HEARTBEAT_TOPIC,
    ManagedMqttHeartbeatSubscriber,
    create_paho_client,
)


NOW = datetime(2026, 9, 19, 9, 0, tzinfo=timezone.utc)
DEVICE_MAC = "AA:BB:CC:DD:EE:02"
BOOT_ID = "c" * 32


class FakeMqttClient:
    def __init__(self):
        self.on_connect = None
        self.on_connect_fail = None
        self.on_disconnect = None
        self.on_subscribe = None
        self.on_message = None
        self.connect_calls = []
        self.subscribe_calls = []
        self.reconnect_delays = []
        self.loop_start_calls = 0
        self.loop_stop_calls = 0
        self.disconnect_calls = 0
        self.next_mid = 41

    def connect_async(self, host, port, keepalive):
        self.connect_calls.append((host, port, keepalive))
        return 0

    def loop_start(self):
        self.loop_start_calls += 1
        return 0

    def loop_stop(self):
        self.loop_stop_calls += 1

    def disconnect(self):
        self.disconnect_calls += 1
        return 0

    def subscribe(self, topic, qos):
        self.subscribe_calls.append((topic, qos))
        return 0, self.next_mid

    def reconnect_delay_set(self, min_delay, max_delay):
        self.reconnect_delays.append((min_delay, max_delay))

    def emit_connect(self, reason_code=0):
        self.on_connect(self, None, {}, reason_code, None)

    def emit_connect_fail(self):
        self.on_connect_fail(self, None)

    def emit_subscribe(self, reason_codes=None):
        codes = [1] if reason_codes is None else reason_codes
        self.on_subscribe(self, None, self.next_mid, codes, None)

    def emit_disconnect(self, reason_code=1):
        self.on_disconnect(self, None, {}, reason_code, None)

    def emit_message(self, payload, *, retain=False, dup=False, mid=1):
        message = SimpleNamespace(
            topic="community/camera-01/status",
            payload=payload,
            retain=retain,
            dup=dup,
            qos=1,
            mid=mid,
        )
        self.on_message(self, None, message)


def _enabled_settings(**changes):
    settings = MqttSubscriberSettings(
        enabled=True,
        host="mqtt.test.invalid",
        port=1883,
        username="backend-reader",
        password="test-only-password",
        client_id="test-heartbeat-subscriber",
        keepalive=30,
        qos=1,
        tls_enabled=False,
        queue_size=8,
        reconnect_min_seconds=1,
        reconnect_max_seconds=8,
        reconnect_jitter_ratio=0.25,
    )
    return replace(settings, **changes)


def _heartbeat(sequence=1):
    return json.dumps(
        {
            "schema_version": 2,
            "device_id": "camera-01",
            "boot_id": BOOT_ID,
            "sequence": sequence,
            "firmware_version": "0.3.0",
            "uptime_ms": sequence * 1000,
            "ip": "192.168.4.21",
            "mac": DEVICE_MAC,
            "telemetry": {"device_type": "camera", "state": "recording"},
        },
        separators=(",", ":"),
    ).encode("utf-8")


@pytest.fixture
def state_service(tmp_path):
    service = DeviceStateService(tmp_path / "subscriber.sqlite", clock=lambda: NOW)
    service.initialize()
    service.bind_device(
        device_id="camera-01",
        identity_kind="mac",
        identity_value=DEVICE_MAC,
        display_name="客厅摄像头",
        device_type="camera",
        area_id="building-a",
    )
    return service


def _subscriber(state_service, client, **options):
    created = []
    settings = options.pop("settings", _enabled_settings())
    environment = options.pop("environment", {})

    def factory(settings):
        created.append(settings)
        return client

    subscriber = ManagedMqttHeartbeatSubscriber(
        state_service,
        settings_provider=lambda: settings,
        client_factory=factory,
        clock=lambda: NOW,
        jitter_source=lambda low, high: (low + high) / 2,
        environment=environment,
        **options,
    )
    return subscriber, created


def _wait_for(predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition was not reached before timeout")


def _observation_count(service):
    with sqlite3.connect(service.database_path) as connection:
        return connection.execute(
            "SELECT COUNT(*) FROM v3_device_state_observations"
        ).fetchone()[0]


def _health(service):
    return service.get_component_health(COMPONENT_ID)


def test_default_disabled_does_not_create_client_or_thread(state_service):
    factory_calls = []
    before = {thread.ident for thread in threading.enumerate()}
    subscriber = ManagedMqttHeartbeatSubscriber(
        state_service,
        settings_provider=lambda: mqtt_subscriber_settings({}),
        client_factory=lambda settings: factory_calls.append(settings),
        environment={},
    )

    assert subscriber.start() is False
    assert subscriber.is_running is False
    assert factory_calls == []
    assert {thread.ident for thread in threading.enumerate()} == before
    subscriber.stop()


def test_enabled_missing_configuration_fails_safely_and_marks_degraded(
    state_service, caplog
):
    secret = "must-never-appear-in-logs"
    environment = {
        "IOT_IDS_MQTT_ENABLED": "true",
        "IOT_IDS_MQTT_BACKEND_PASSWORD": secret,
    }
    subscriber = ManagedMqttHeartbeatSubscriber(
        state_service,
        settings_provider=lambda: mqtt_subscriber_settings(environment),
        client_factory=lambda _settings: pytest.fail("client must not be created"),
        environment={},
    )

    with caplog.at_level(logging.ERROR):
        assert subscriber.start() is False

    assert _health(state_service)["readiness"] == "degraded"
    assert _health(state_service)["reason"] == "configuration_error"
    assert secret not in caplog.text


def test_configuration_validation_and_password_repr(tmp_path):
    ca_file = tmp_path / "test-ca.pem"
    ca_file.write_text("test certificate placeholder", encoding="utf-8")
    environment = {
        "IOT_IDS_MQTT_ENABLED": "true",
        "IOT_IDS_MQTT_HOST": "mqtt.test.invalid",
        "IOT_IDS_MQTT_PORT": "8883",
        "IOT_IDS_MQTT_BACKEND_USERNAME": "backend-reader",
        "IOT_IDS_MQTT_BACKEND_PASSWORD": "private-test-value",
        "IOT_IDS_MQTT_CLIENT_ID": "backend-test",
        "IOT_IDS_MQTT_KEEPALIVE": "45",
        "IOT_IDS_MQTT_QOS": "1",
        "IOT_IDS_MQTT_TLS_ENABLED": "true",
        "IOT_IDS_MQTT_CA_FILE": str(ca_file),
        "IOT_IDS_MQTT_QUEUE_SIZE": "32",
        "IOT_IDS_MQTT_RECONNECT_MIN_SECONDS": "2",
        "IOT_IDS_MQTT_RECONNECT_MAX_SECONDS": "30",
        "IOT_IDS_MQTT_RECONNECT_JITTER": "0.1",
    }

    settings = mqtt_subscriber_settings(environment)

    assert settings.enabled and settings.tls_enabled
    assert settings.queue_size == 32
    assert settings.reconnect_max_seconds == 30
    assert "private-test-value" not in repr(settings)
    with pytest.raises(MqttConfigurationError):
        mqtt_subscriber_settings({**environment, "IOT_IDS_MQTT_QOS": "0"})
    with pytest.raises(MqttConfigurationError):
        mqtt_subscriber_settings(
            {**environment, "IOT_IDS_MQTT_CA_FILE": str(tmp_path / "missing.pem")}
        )


def test_paho_factory_enforces_server_verification(monkeypatch, tmp_path):
    ca_file = tmp_path / "ca.pem"
    ca_file.write_text("placeholder", encoding="utf-8")
    calls = {}

    class FakePahoClient:
        def __init__(self, **kwargs):
            calls["constructor"] = kwargs

        def username_pw_set(self, username, password):
            calls["credentials"] = (username, password)

        def tls_set_context(self, context):
            calls["tls_context"] = context

        def reconnect_delay_set(self, min_delay, max_delay):
            calls["reconnect"] = (min_delay, max_delay)

    fake_module = SimpleNamespace(
        Client=FakePahoClient,
        CallbackAPIVersion=SimpleNamespace(VERSION2="v2"),
        MQTTv311="mqtt-v311",
    )
    fake_context = SimpleNamespace(check_hostname=False, verify_mode=None)
    monkeypatch.setattr(importlib, "import_module", lambda _name: fake_module)
    monkeypatch.setattr(
        ssl,
        "create_default_context",
        lambda cafile: calls.setdefault("ca_file", cafile) and fake_context,
    )
    settings = replace(
        _enabled_settings(),
        tls_enabled=True,
        port=8883,
        ca_file=str(ca_file),
    )

    client = create_paho_client(settings)

    assert client is not None
    assert calls["ca_file"] == str(ca_file)
    assert calls["tls_context"] is fake_context
    assert fake_context.check_hostname is True
    assert fake_context.verify_mode == ssl.CERT_REQUIRED
    assert calls["constructor"]["clean_session"] is True
    assert not hasattr(client, "tls_insecure_set")


def test_start_stop_are_idempotent_and_plaintext_is_warned(state_service, caplog):
    client = FakeMqttClient()
    subscriber, created = _subscriber(state_service, client)

    with caplog.at_level(logging.WARNING):
        assert subscriber.start() is True
        assert subscriber.start() is True

    assert len(created) == 1
    assert client.connect_calls == [("mqtt.test.invalid", 1883, 30)]
    assert client.loop_start_calls == 1
    assert "isolated_trusted_lan" in caplog.text

    subscriber.stop()
    subscriber.stop()
    assert client.disconnect_calls == 1
    assert client.loop_stop_calls == 1
    assert subscriber.is_running is False


def test_stop_does_not_deadlock_with_network_disconnect_callback(state_service):
    class CallbackDuringStopClient(FakeMqttClient):
        def __init__(self):
            super().__init__()
            self.callback_blocked = None

        def loop_stop(self):
            super().loop_stop()
            callback_thread = threading.Thread(
                target=lambda: self.emit_disconnect(),
                daemon=True,
            )
            callback_thread.start()
            callback_thread.join(timeout=0.5)
            self.callback_blocked = callback_thread.is_alive()

    client = CallbackDuringStopClient()
    subscriber, _created = _subscriber(state_service, client)
    assert subscriber.start()

    subscriber.stop()

    assert client.callback_blocked is False
    assert subscriber.is_running is False


def test_debug_reloader_parent_does_not_start_a_second_client(state_service):
    client = FakeMqttClient()
    subscriber, created = _subscriber(
        state_service,
        client,
        environment={"IOT_IDS_FLASK_DEBUG": "true"},
    )

    assert subscriber.start() is False
    assert created == []
    assert subscriber.is_running is False


def test_connect_subscribes_only_heartbeat_topic_and_becomes_ready(state_service):
    client = FakeMqttClient()
    subscriber, _created = _subscriber(state_service, client)
    try:
        assert subscriber.start()
        assert _health(state_service)["readiness"] == "warming_up"

        client.emit_connect()
        assert client.subscribe_calls == [(HEARTBEAT_TOPIC, 1)]
        client.emit_subscribe()
        _wait_for(lambda: _health(state_service)["readiness"] == "ready")

        health = _health(state_service)
        assert health["reason"] is None
        assert health["state_version"] == 2
    finally:
        subscriber.stop()


def test_valid_message_flows_through_existing_ingestion(state_service):
    client = FakeMqttClient()
    subscriber, _created = _subscriber(state_service, client)
    try:
        assert subscriber.start()
        client.emit_message(_heartbeat())
        _wait_for(lambda: _observation_count(state_service) == 1)
        assert state_service.get_device_state("camera-01")["connection_status"] == "online"
    finally:
        subscriber.stop()


def test_ingestion_rejection_does_not_kill_worker(state_service):
    client = FakeMqttClient()
    subscriber, _created = _subscriber(state_service, client)
    try:
        assert subscriber.start()
        client.emit_message(b"{invalid-json")
        client.emit_message(_heartbeat())
        _wait_for(lambda: _observation_count(state_service) == 1)
        assert subscriber.is_running
    finally:
        subscriber.stop()


def test_retained_message_never_reaches_ingestion(state_service):
    client = FakeMqttClient()
    subscriber, _created = _subscriber(state_service, client)
    try:
        assert subscriber.start()
        client.emit_message(_heartbeat(), retain=True)
        time.sleep(0.08)
        assert _observation_count(state_service) == 0
    finally:
        subscriber.stop()


def test_qos_duplicate_is_rejected_by_existing_replay_logic(state_service):
    client = FakeMqttClient()
    subscriber, _created = _subscriber(state_service, client)
    try:
        assert subscriber.start()
        payload = _heartbeat()
        client.emit_message(payload, mid=7)
        client.emit_message(payload, dup=True, mid=7)
        _wait_for(lambda: _observation_count(state_service) == 1)
        subscriber._message_queue.join()
        assert _observation_count(state_service) == 1
        assert subscriber.is_running
    finally:
        subscriber.stop()


def test_disconnect_degrades_and_resubscribe_recovers_ready(state_service):
    client = FakeMqttClient()
    subscriber, _created = _subscriber(state_service, client)
    try:
        assert subscriber.start()
        client.emit_connect()
        client.emit_subscribe()
        _wait_for(lambda: _health(state_service)["readiness"] == "ready")

        client.emit_disconnect()
        _wait_for(lambda: _health(state_service)["readiness"] == "degraded")
        reconnect_min, reconnect_max = client.reconnect_delays[-1]
        assert reconnect_min == reconnect_max
        assert 1 <= reconnect_min <= 1.25

        client.emit_connect()
        client.emit_subscribe()
        _wait_for(lambda: _health(state_service)["readiness"] == "ready")
        assert client.subscribe_calls == [
            (HEARTBEAT_TOPIC, 1),
            (HEARTBEAT_TOPIC, 1),
        ]
        assert _health(state_service)["state_version"] == 4
    finally:
        subscriber.stop()


def test_reconnect_backoff_is_exponential_jittered_and_bounded(state_service):
    client = FakeMqttClient()
    subscriber, _created = _subscriber(
        state_service,
        client,
        settings=_enabled_settings(reconnect_max_seconds=4),
    )
    try:
        assert subscriber.start()
        for _attempt in range(5):
            client.emit_connect_fail()

        delays = [minimum for minimum, maximum in client.reconnect_delays]
        assert all(minimum == maximum for minimum, maximum in client.reconnect_delays)
        assert delays == sorted(delays)
        assert all(1 <= delay <= 4 for delay in delays)
        assert delays[-1] == delays[-2]
    finally:
        subscriber.stop()


def test_subscription_rejection_marks_component_degraded(state_service):
    client = FakeMqttClient()
    subscriber, _created = _subscriber(state_service, client)
    try:
        assert subscriber.start()
        client.emit_connect()
        client.emit_subscribe([128])
        _wait_for(lambda: _health(state_service)["readiness"] == "degraded")
        assert _health(state_service)["reason"] == "subscription_rejected"
        assert subscriber.is_running
    finally:
        subscriber.stop()


def test_worker_exception_degrades_but_does_not_block_next_message(state_service):
    completed = threading.Event()

    class FlakyIngestor:
        def __init__(self):
            self.calls = 0

        def ingest(self, **_message):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("untrusted third-party exception detail")
            completed.set()
            return SimpleNamespace(accepted=True, code="accepted")

    ingestor = FlakyIngestor()
    client = FakeMqttClient()
    subscriber, _created = _subscriber(
        state_service,
        client,
        ingestor=ingestor,
    )
    try:
        assert subscriber.start()
        client.emit_message(b"first")
        client.emit_message(b"second")
        assert completed.wait(timeout=1)
        assert ingestor.calls == 2
        assert _health(state_service)["readiness"] == "degraded"
        assert _health(state_service)["reason"] == "worker_ingestion_error"
        assert subscriber.is_running
    finally:
        subscriber.stop()


def test_queue_overflow_is_nonblocking_and_marks_component_degraded(state_service):
    started = threading.Event()
    release = threading.Event()

    class BlockingIngestor:
        def __init__(self):
            self.calls = 0

        def ingest(self, **_message):
            self.calls += 1
            if self.calls == 1:
                started.set()
                release.wait(timeout=2)
            return SimpleNamespace(accepted=True, code="accepted")

    client = FakeMqttClient()
    subscriber, _created = _subscriber(
        state_service,
        client,
        settings=_enabled_settings(queue_size=1),
        ingestor=BlockingIngestor(),
    )
    try:
        assert subscriber.start()
        client.emit_message(b"first")
        assert started.wait(timeout=1)
        client.emit_message(b"second")
        before = time.monotonic()
        client.emit_message(b"overflow")
        elapsed = time.monotonic() - before
        assert elapsed < 0.1

        release.set()
        _wait_for(lambda: _health(state_service)["readiness"] == "degraded")
        assert _health(state_service)["reason"] == "message_queue_overflow"
    finally:
        release.set()
        subscriber.stop()


def test_client_creation_exception_and_logs_do_not_expose_password(
    state_service, caplog
):
    secret = "never-log-this-password"
    settings = _enabled_settings(password=secret)

    def failing_factory(_settings):
        raise RuntimeError(secret)

    subscriber = ManagedMqttHeartbeatSubscriber(
        state_service,
        settings_provider=lambda: settings,
        client_factory=failing_factory,
        environment={},
    )
    with caplog.at_level(logging.ERROR):
        assert subscriber.start() is False

    assert secret not in caplog.text
    assert secret not in repr(settings)
    assert _health(state_service)["reason"] == "client_creation_failed"


def test_import_and_flask_source_have_no_automatic_mqtt_start():
    module = importlib.import_module("services.mqtt_subscriber")
    assert module.ManagedMqttHeartbeatSubscriber is not None
    app_source = (Path(__file__).parents[1] / "app.py").read_text(encoding="utf-8")
    assert "start_runtime_services(application)" in app_source
    assert 'if __name__ == "__main__":' in app_source
    assert "paho" not in module.__dict__
