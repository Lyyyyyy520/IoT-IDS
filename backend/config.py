"""Environment-backed runtime settings for the transitional backend."""
import os
import secrets
import warnings


def _csv_env(name: str, default: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in os.getenv(name, default).split(",") if item.strip())


def runtime_environment() -> str:
    return os.getenv("IOT_IDS_ENV", "development").strip().lower()


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
