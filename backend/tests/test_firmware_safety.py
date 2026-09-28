from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
FIRMWARE_FILES = (
    ROOT / "edge" / "esp32" / "community_device" / "community_device.ino",
    ROOT / "edge" / "esp32" / "camera_device" / "camera_device.ino",
)


def test_attack_examples_cannot_target_public_internet():
    for firmware in FIRMWARE_FILES:
        source = firmware.read_text(encoding="utf-8")
        assert "8.8.8.8" not in source
        assert "isAllowedAttackTarget" in source
        assert "LAB_ATTACK_ENABLED" in source
        assert "ATTACK_INTERVAL_MS = 100" in source
        assert "ATTACK_MAX_MS = 30000" in source


def test_firmware_requires_authenticated_mqtt_connect():
    for firmware in FIRMWARE_FILES:
        source = firmware.read_text(encoding="utf-8")
        assert "mqtt.connect(DEVICE_ID, MQTT_USER, MQTT_PASSWORD)" in source
        assert "mqtt.connect(DEVICE_ID))" not in source


def test_firmware_heartbeat_envelopes_share_v2_contract():
    envelope_fields = (
        "schema_version",
        "device_id",
        "boot_id",
        "sequence",
        "firmware_version",
        "uptime_ms",
        "ip",
        "mac",
        "telemetry",
    )
    for firmware in FIRMWARE_FILES:
        source = firmware.read_text(encoding="utf-8")
        assert "MQTT_HEARTBEAT_SCHEMA_VERSION = 2" in source
        assert "FIRMWARE_VERSION = \"0.3.0\"" in source
        assert "String buildDeviceTelemetry()" in source
        assert "String buildTelemetry()" in source
        for field in envelope_fields:
            assert f'\\\"{field}\\\"' in source
        assert "telemetrySequence++;" in source
        assert "generateBootId();" in source
        assert source.count("esp_random()") >= 4
        assert "WiFi.localIP().toString()" in source
        assert "WiFi.macAddress()" in source
        assert "esp_timer_get_time()" in source
        assert "mqtt.setBufferSize(MQTT_BUFFER_BYTES)" in source
        assert 'Serial.printf("[遥测] %s' not in source
        assert 'Serial.printf("[心跳] %s' not in source


def test_firmware_keeps_device_specific_fields_inside_telemetry():
    community = FIRMWARE_FILES[0].read_text(encoding="utf-8")
    camera = FIRMWARE_FILES[1].read_text(encoding="utf-8")
    for device_type in ("door", "light", "plug", "sensor", "speaker"):
        assert f'\\\"device_type\\\":\\\"{device_type}\\\"' in community
    assert '\\\"device_type\\\":\\\"camera\\\"' in camera
    assert '\\\"angle\\\":%d' in camera


def test_first_release_has_no_legacy_capture_or_block_entry_points():
    app_source = (ROOT / "backend" / "app.py").read_text(encoding="utf-8")
    for retired_path in (
        "/api/capture/start",
        "/api/capture/stop",
        "/api/alerts/<int:alert_id>/block",
        "/api/alerts/<int:alert_id>/unblock",
    ):
        assert retired_path not in app_source
