"""Environment-backed runtime settings for the transitional backend."""
from dataclasses import dataclass, field
from hashlib import sha256
import os
from pathlib import Path
import secrets
from typing import Mapping
import warnings


def _csv_env(name: str, default: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in os.getenv(name, default).split(",") if item.strip())


def runtime_environment() -> str:
    return os.getenv("IOT_IDS_ENV", "development").strip().lower()


def database_path() -> str | None:
    """Return the explicitly configured shared legacy/v3 database path."""
    configured = os.getenv("IOT_IDS_DATABASE_PATH", "").strip()
    return configured or None


def session_secret() -> str:
    configured = os.getenv("IOT_IDS_SESSION_SECRET", "").strip()
    if configured:
        return configured
    if runtime_environment() == "production":
        raise RuntimeError("IOT_IDS_SESSION_SECRET is required in production")
    warnings.warn(
        "IOT_IDS_SESSION_SECRET is not configured; sessions will be invalid after restart",
        RuntimeWarning,
        stacklevel=2,
    )
    return secrets.token_hex(32)


def probe_token() -> str:
    return os.getenv("IOT_IDS_PROBE_TOKEN", "").strip()


def bootstrap_admin_username() -> str:
    return os.getenv("IOT_IDS_BOOTSTRAP_ADMIN_USERNAME", "admin").strip() or "admin"


def bootstrap_admin_password() -> str:
    """Return the optional one-time password used to create the first admin."""
    return os.getenv("IOT_IDS_BOOTSTRAP_ADMIN_PASSWORD", "")


def cors_origins() -> tuple[str, ...]:
    return _csv_env(
        "IOT_IDS_CORS_ORIGINS",
        "http://localhost:3000,http://127.0.0.1:3000",
    )


def lab_allowed_cidrs() -> tuple[str, ...]:
    return _csv_env("IOT_IDS_LAB_ALLOWED_CIDRS", "192.168.4.0/24")


def flask_debug_enabled() -> bool:
    return os.getenv("IOT_IDS_FLASK_DEBUG", "false").strip().lower() == "true"


class MqttConfigurationError(ValueError):
    """Raised for invalid MQTT environment configuration without secret values."""


class MobileConfigurationError(ValueError):
    """Raised for unsafe or invalid mobile-session configuration."""


@dataclass(frozen=True)
class MobileSecuritySettings:
    token_secret: str = field(repr=False)
    environment: str = "development"
    allow_insecure_http: bool = False
    trust_proxy: bool = False
    pairing_ttl_seconds: int = 300
    access_ttl_seconds: int = 1800
    refresh_ttl_seconds: int = 30 * 24 * 60 * 60
    pairing_max_attempts: int = 5
    rate_window_seconds: int = 300
    rate_block_seconds: int = 300
    claim_rate_limit: int = 10
    refresh_rate_limit: int = 20

    def validate(self) -> "MobileSecuritySettings":
        if len(self.token_secret) < 32:
            raise MobileConfigurationError(
                "IOT_IDS_MOBILE_TOKEN_SECRET must contain at least 32 characters"
            )
        if self.environment == "production" and self.allow_insecure_http:
            raise MobileConfigurationError(
                "plaintext mobile HTTP cannot be enabled in production"
            )
        limits = (
            ("pairing TTL", self.pairing_ttl_seconds, 60, 900),
            ("access TTL", self.access_ttl_seconds, 300, 3600),
            ("refresh TTL", self.refresh_ttl_seconds, 3600, 90 * 24 * 60 * 60),
            ("pairing attempts", self.pairing_max_attempts, 1, 10),
            ("rate window", self.rate_window_seconds, 10, 3600),
            ("rate block", self.rate_block_seconds, 10, 24 * 60 * 60),
            ("claim rate", self.claim_rate_limit, 1, 100),
            ("refresh rate", self.refresh_rate_limit, 1, 200),
        )
        for label, value, minimum, maximum in limits:
            if type(value) is not int or not minimum <= value <= maximum:
                raise MobileConfigurationError(
                    f"mobile {label} must be between {minimum} and {maximum}"
                )
        return self


def mobile_security_settings(
    environment: Mapping[str, str] | None = None,
    *,
    fallback_secret: str | bytes | None = None,
) -> MobileSecuritySettings:
    """Load mobile security settings without starting services or opening a database."""
    source = os.environ if environment is None else environment
    mode = _env_value(source, "IOT_IDS_ENV", "development").lower()
    configured_secret = source.get("IOT_IDS_MOBILE_TOKEN_SECRET", "").strip()
    if not configured_secret:
        if mode == "production":
            raise MobileConfigurationError(
                "IOT_IDS_MOBILE_TOKEN_SECRET is required in production"
            )
        if fallback_secret is None:
            configured_secret = secrets.token_hex(32)
        elif isinstance(fallback_secret, bytes):
            configured_secret = sha256(
                b"iot-ids-mobile-token:" + fallback_secret
            ).hexdigest()
        else:
            configured_secret = sha256(
                ("iot-ids-mobile-token:" + str(fallback_secret)).encode("utf-8")
            ).hexdigest()
    try:
        settings = MobileSecuritySettings(
            token_secret=configured_secret,
            environment=mode,
            allow_insecure_http=_boolean_setting(
                source, "IOT_IDS_MOBILE_ALLOW_INSECURE_HTTP", False
            ),
            trust_proxy=_boolean_setting(
                source, "IOT_IDS_MOBILE_TRUST_PROXY", False
            ),
            pairing_ttl_seconds=_integer_setting(
                source, "IOT_IDS_MOBILE_PAIRING_TTL_SECONDS", 300
            ),
            access_ttl_seconds=_integer_setting(
                source, "IOT_IDS_MOBILE_ACCESS_TTL_SECONDS", 1800
            ),
            refresh_ttl_seconds=_integer_setting(
                source, "IOT_IDS_MOBILE_REFRESH_TTL_SECONDS", 30 * 24 * 60 * 60
            ),
            pairing_max_attempts=_integer_setting(
                source, "IOT_IDS_MOBILE_PAIRING_MAX_ATTEMPTS", 5
            ),
            rate_window_seconds=_integer_setting(
                source, "IOT_IDS_MOBILE_RATE_WINDOW_SECONDS", 300
            ),
            rate_block_seconds=_integer_setting(
                source, "IOT_IDS_MOBILE_RATE_BLOCK_SECONDS", 300
            ),
            claim_rate_limit=_integer_setting(
                source, "IOT_IDS_MOBILE_CLAIM_RATE_LIMIT", 10
            ),
            refresh_rate_limit=_integer_setting(
                source, "IOT_IDS_MOBILE_REFRESH_RATE_LIMIT", 20
            ),
        )
    except MqttConfigurationError as exc:
        raise MobileConfigurationError(str(exc)) from exc
    return settings.validate()


def _env_value(environment: Mapping[str, str], name: str, default: str = "") -> str:
    return environment.get(name, default).strip()


def _boolean_setting(
    environment: Mapping[str, str], name: str, default: bool
) -> bool:
    raw = _env_value(environment, name, "true" if default else "false").lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    raise MqttConfigurationError(f"{name} must be true or false")


def _integer_setting(
    environment: Mapping[str, str], name: str, default: int
) -> int:
    try:
        return int(_env_value(environment, name, str(default)))
    except ValueError as exc:
        raise MqttConfigurationError(f"{name} must be an integer") from exc


def _float_setting(
    environment: Mapping[str, str], name: str, default: float
) -> float:
    try:
        return float(_env_value(environment, name, str(default)))
    except ValueError as exc:
        raise MqttConfigurationError(f"{name} must be numeric") from exc


@dataclass(frozen=True)
class MqttSubscriberSettings:
    enabled: bool = False
    host: str = ""
    port: int = 8883
    username: str = ""
    password: str = field(default="", repr=False)
    client_id: str = "iot-ids-heartbeat-subscriber"
    keepalive: int = 60
    qos: int = 1
    tls_enabled: bool = True
    ca_file: str = ""
    queue_size: int = 256
    reconnect_min_seconds: float = 1.0
    reconnect_max_seconds: float = 60.0
    reconnect_jitter_ratio: float = 0.2

    def validate(self) -> "MqttSubscriberSettings":
        if not 1 <= self.port <= 65535:
            raise MqttConfigurationError("IOT_IDS_MQTT_PORT must be between 1 and 65535")
        if not 10 <= self.keepalive <= 3600:
            raise MqttConfigurationError(
                "IOT_IDS_MQTT_KEEPALIVE must be between 10 and 3600"
            )
        if self.qos != 1:
            raise MqttConfigurationError("IOT_IDS_MQTT_QOS must be 1")
        if not 1 <= self.queue_size <= 10000:
            raise MqttConfigurationError(
                "IOT_IDS_MQTT_QUEUE_SIZE must be between 1 and 10000"
            )
        if self.reconnect_min_seconds <= 0:
            raise MqttConfigurationError(
                "IOT_IDS_MQTT_RECONNECT_MIN_SECONDS must be greater than zero"
            )
        if not self.reconnect_min_seconds <= self.reconnect_max_seconds <= 3600:
            raise MqttConfigurationError(
                "IOT_IDS_MQTT_RECONNECT_MAX_SECONDS must be between the minimum and 3600"
            )
        if not 0 <= self.reconnect_jitter_ratio <= 0.5:
            raise MqttConfigurationError(
                "IOT_IDS_MQTT_RECONNECT_JITTER must be between 0 and 0.5"
            )
        if not self.enabled:
            return self
        missing = []
        if not self.host:
            missing.append("IOT_IDS_MQTT_HOST")
        if not self.username:
            missing.append("IOT_IDS_MQTT_BACKEND_USERNAME")
        if not self.password.strip():
            missing.append("IOT_IDS_MQTT_BACKEND_PASSWORD")
        if not self.client_id:
            missing.append("IOT_IDS_MQTT_CLIENT_ID")
        if missing:
            raise MqttConfigurationError(
                "missing required MQTT settings: " + ", ".join(missing)
            )
        if self.tls_enabled:
            if not self.ca_file:
                raise MqttConfigurationError(
                    "IOT_IDS_MQTT_CA_FILE is required when TLS is enabled"
                )
            ca_path = Path(self.ca_file)
            if not ca_path.is_file():
                raise MqttConfigurationError(
                    "IOT_IDS_MQTT_CA_FILE must reference an existing file"
                )
        return self


def mqtt_subscriber_settings(
    environment: Mapping[str, str] | None = None,
) -> MqttSubscriberSettings:
    """Load and validate MQTT subscriber settings without starting any client."""
    source = os.environ if environment is None else environment
    settings = MqttSubscriberSettings(
        enabled=_boolean_setting(source, "IOT_IDS_MQTT_ENABLED", False),
        host=_env_value(source, "IOT_IDS_MQTT_HOST"),
        port=_integer_setting(source, "IOT_IDS_MQTT_PORT", 8883),
        username=_env_value(source, "IOT_IDS_MQTT_BACKEND_USERNAME"),
        password=source.get("IOT_IDS_MQTT_BACKEND_PASSWORD", ""),
        client_id=_env_value(
            source,
            "IOT_IDS_MQTT_CLIENT_ID",
            "iot-ids-heartbeat-subscriber",
        ),
        keepalive=_integer_setting(source, "IOT_IDS_MQTT_KEEPALIVE", 60),
        qos=_integer_setting(source, "IOT_IDS_MQTT_QOS", 1),
        tls_enabled=_boolean_setting(source, "IOT_IDS_MQTT_TLS_ENABLED", True),
        ca_file=_env_value(source, "IOT_IDS_MQTT_CA_FILE"),
        queue_size=_integer_setting(source, "IOT_IDS_MQTT_QUEUE_SIZE", 256),
        reconnect_min_seconds=_float_setting(
            source, "IOT_IDS_MQTT_RECONNECT_MIN_SECONDS", 1.0
        ),
        reconnect_max_seconds=_float_setting(
            source, "IOT_IDS_MQTT_RECONNECT_MAX_SECONDS", 60.0
        ),
        reconnect_jitter_ratio=_float_setting(
            source, "IOT_IDS_MQTT_RECONNECT_JITTER", 0.2
        ),
    )
    return settings.validate()
