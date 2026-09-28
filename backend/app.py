"""IoT IDS Flask application factory and current API registrations."""
import logging
from pathlib import Path
from datetime import datetime

from flask import Blueprint, Flask, current_app, jsonify, request
from flask_cors import CORS

from config import (
    MobileSecuritySettings,
    cors_origins,
    database_path as configured_database_path,
    flask_debug_enabled,
    mobile_security_settings,
    runtime_environment,
    session_secret,
)
from database import DatabaseUnavailableError
from services.auth import get_current_user, login_user, logout_user
from api.probe import probe_bp
from api.v3_devices import create_v3_devices_blueprint
from api.v3_device_discovery import create_v3_device_discovery_blueprint
from api.v3_system_health import create_v3_system_health_blueprint
from api.v3_incidents import create_v3_incidents_blueprint
from api.v3_mobile import create_v3_mobile_blueprint
from api.v3_realtime import create_v3_realtime_blueprint
from api.v3_traffic import create_v3_traffic_blueprint
from runtime_services import (
    BackendServiceContainer,
    EXTENSION_KEY,
    default_mqtt_settings_provider,
    get_service_container,
    start_runtime_services,
    stop_runtime_services,
)


LOGGER = logging.getLogger(__name__)
shared_api_bp = Blueprint("legacy_api", __name__)


@shared_api_bp.get("/api/health")
def health():
    """Read-only compatibility health endpoint; never expose local paths."""
    database = get_service_container(current_app).database_health()
    healthy = (
        database["available"]
        and database["legacy_schema_ready"]
        and database["v3_schema_ready"]
    )
    return jsonify({
        "status": "ok" if healthy else "degraded",
        "database": database,
        "timestamp": datetime.now().astimezone().isoformat(),
    })


@shared_api_bp.post("/api/auth/login")
def auth_login():
    data = request.get_json(silent=True) or {}
    username = data.get("username", "")
    password = data.get("password", "")
    if not isinstance(username, str) or not isinstance(password, str):
        return jsonify({"success": False, "message": "请输入账号和密码"}), 400
    username = username.strip()
    if not username or not password:
        return jsonify({"success": False, "message": "请输入账号和密码"}), 400
    result = login_user(username, password)
    return jsonify(result), (200 if result.get("success") else 401)


@shared_api_bp.post("/api/auth/logout")
def auth_logout():
    return jsonify(logout_user())


@shared_api_bp.get("/api/auth/me")
def auth_me():
    user = get_current_user()
    if not user:
        return jsonify({"authenticated": False}), 401
    return jsonify({"authenticated": True, "user": user})


def create_app(
    config_overrides=None,
    *,
    mqtt_settings_provider=None,
    mqtt_subscriber_factory=None,
    service_environment=None,
    mobile_settings: MobileSecuritySettings | None = None,
):
    """Create one side-effect-free Flask application instance."""
    overrides = dict(config_overrides or {})
    configured_secret = overrides.get("SECRET_KEY") or session_secret()
    application = Flask(__name__)
    application.config.from_mapping(
        SECRET_KEY=configured_secret,
        SESSION_PERMANENT=True,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=runtime_environment() == "production",
        MAX_CONTENT_LENGTH=2 * 1024 * 1024,
        DATABASE_PATH=configured_database_path(),
        DEBUG=flask_debug_enabled(),
        V3_REPLAY_LIMIT=256,
        V3_POLL_INTERVAL=1.0,
        V3_KEEPALIVE_INTERVAL=15.0,
    )
    application.config.update(overrides)
    from v3_db_maintenance import RetentionSettings
    RetentionSettings.from_environment()
    resolved_mobile_settings = (
        mobile_settings
        or application.config.get("MOBILE_SECURITY_SETTINGS")
        or mobile_security_settings(fallback_secret=configured_secret)
    )
    if not isinstance(resolved_mobile_settings, MobileSecuritySettings):
        raise TypeError("MOBILE_SECURITY_SETTINGS must be MobileSecuritySettings")
    resolved_mobile_settings.validate()
    application.config["MOBILE_SECURITY_SETTINGS"] = resolved_mobile_settings

    configured_path = application.config.get("DATABASE_PATH")
    normalized_path = (
        Path(configured_path).expanduser().resolve()
        if configured_path is not None and str(configured_path).strip()
        else None
    )
    container_options = {
        "database_path": normalized_path,
        "mqtt_settings_provider": mqtt_settings_provider or default_mqtt_settings_provider,
        "traffic_clock": application.config.get("V3_CLOCK"),
    }
    if mqtt_subscriber_factory is not None:
        container_options["mqtt_subscriber_factory"] = mqtt_subscriber_factory
    if service_environment is not None:
        container_options["environment"] = service_environment
    application.extensions[EXTENSION_KEY] = BackendServiceContainer(**container_options)

    CORS(
        application,
        supports_credentials=True,
        origins=list(application.config.get("CORS_ORIGINS", cors_origins())),
    )
    application.register_blueprint(shared_api_bp)
    application.register_blueprint(probe_bp)
    application.register_blueprint(
        create_v3_devices_blueprint(normalized_path, clock=application.config.get("V3_CLOCK"))
    )
    discovery_blueprint = create_v3_device_discovery_blueprint(
        normalized_path,
        clock=application.config.get("V3_CLOCK"),
    )
    application.extensions["iot_ids_device_discovery"] = discovery_blueprint.discovery_service
    application.register_blueprint(discovery_blueprint)
    application.register_blueprint(
        create_v3_realtime_blueprint(
            normalized_path,
            clock=application.config.get("V3_CLOCK"),
            waiter=application.config.get("V3_WAITER"),
            monotonic_clock=application.config.get("V3_MONOTONIC_CLOCK"),
            replay_limit=application.config["V3_REPLAY_LIMIT"],
            poll_interval=application.config["V3_POLL_INTERVAL"],
            keepalive_interval=application.config["V3_KEEPALIVE_INTERVAL"],
            max_idle_cycles=application.config.get("V3_MAX_IDLE_CYCLES"),
        )
    )
    traffic_service = get_service_container(application).get_traffic_service()
    application.register_blueprint(
        create_v3_traffic_blueprint(traffic_service, clock=application.config.get("V3_CLOCK"))
    )
    mobile_blueprint = create_v3_mobile_blueprint(
        normalized_path,
        resolved_mobile_settings,
        clock=application.config.get("V3_CLOCK"),
        fault_injector=application.config.get("MOBILE_FAULT_INJECTOR"),
        traffic_service=traffic_service,
    )
    application.extensions["iot_ids_mobile_access"] = mobile_blueprint.mobile_service
    application.register_blueprint(mobile_blueprint)
    incident_blueprint = create_v3_incidents_blueprint(
        normalized_path,
        resolved_mobile_settings,
        mobile_blueprint.mobile_service,
        clock=application.config.get("V3_CLOCK"),
        fault_injector=application.config.get("INCIDENT_FAULT_INJECTOR"),
    )
    application.extensions["iot_ids_incident_workflow"] = incident_blueprint.incident_service
    application.register_blueprint(incident_blueprint)
    application.register_blueprint(
        create_v3_system_health_blueprint(
            normalized_path,
            get_service_container(application),
            clock=application.config.get("V3_CLOCK"),
        )
    )

    @application.after_request
    def record_runtime_degradation(response):
        if response.status_code >= 500:
            path = request.path
            if path.startswith("/api/v3/events"):
                component = "event_log"
            elif path.startswith("/api/v3/traffic") or path == "/api/probe/push":
                component = "traffic"
            elif path.startswith("/api/v3/incidents"):
                component = "incident"
            elif path.startswith("/api/v3/mobile"):
                component = "mobile"
            elif path.startswith("/api/v3/devices/discovered"):
                component = "discovery"
            else:
                component = "api"
            container = get_service_container(application)
            reason = "database_unavailable" if response.status_code == 503 else "request_failed"
            container.mark_degraded(component, reason)
            if response.status_code == 503:
                container.mark_degraded("database", "database_unavailable")
        return response

    @application.errorhandler(DatabaseUnavailableError)
    def database_unavailable(_error):
        return jsonify({"error": "数据库不可用", "code": "database_unavailable"}), 503

    return application


def validate_runtime_configuration(application) -> dict:
    """Return a safe startup report without modifying the database."""
    database = get_service_container(application).database_health()
    if not database["available"]:
        LOGGER.warning("database_unavailable reason=%s", database["reason"])
    elif not database["v3_schema_ready"]:
        LOGGER.warning("database_v3_schema_unavailable")
    return {"database": database}


def main() -> None:
    application = create_app()
    validate_runtime_configuration(application)
    start_runtime_services(application)
    print("IoT IDS Backend starting...")
    print("    http://localhost:5000/api/health")
    try:
        application.run(
            host="0.0.0.0",
            port=5000,
            debug=bool(application.debug),
            use_reloader=bool(application.debug),
        )
    finally:
        stop_runtime_services(application)


if __name__ == "__main__":
    main()
