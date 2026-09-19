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


def test_first_release_auto_block_path_is_disabled():
    capture_source = (
        ROOT / "backend" / "services" / "traffic_capture.py"
    ).read_text(encoding="utf-8")
    assert "should_block = False" in capture_source
