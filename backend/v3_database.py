"""Additive, transactional SQLite migrations for the v3 data foundation.

The application-facing initializer still accepts an explicit path. The
operator CLI adds stricter existing-file and backup requirements around these
same migration definitions.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import sqlite3
from typing import Iterable


MIGRATION_TABLE = "v3_schema_migrations"

MIGRATION_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS v3_schema_migrations (
    version INTEGER PRIMARY KEY CHECK (version > 0),
    name TEXT NOT NULL UNIQUE,
    checksum TEXT NOT NULL,
    applied_at TEXT NOT NULL
)
""".strip()

V3_DEVICE_STATE_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS v3_device_profiles (
        device_id TEXT PRIMARY KEY,
        identity_kind TEXT NOT NULL,
        identity_value TEXT NOT NULL,
        display_name TEXT NOT NULL,
        device_type TEXT NOT NULL,
        area_id TEXT,
        operation_mode TEXT NOT NULL DEFAULT 'active'
            CHECK (operation_mode IN ('active', 'maintenance', 'disabled')),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE (identity_kind, identity_value)
    )
    """.strip(),
    """
    CREATE TABLE IF NOT EXISTS v3_device_state_observations (
        observation_id INTEGER PRIMARY KEY AUTOINCREMENT,
        device_id TEXT NOT NULL,
        source TEXT NOT NULL,
        sequence INTEGER CHECK (sequence IS NULL OR sequence >= 0),
        ip_address TEXT,
        observed_at TEXT NOT NULL,
        received_at TEXT NOT NULL,
        payload_json TEXT,
        FOREIGN KEY (device_id) REFERENCES v3_device_profiles(device_id),
        UNIQUE (device_id, source, sequence)
    )
    """.strip(),
    """
    CREATE TABLE IF NOT EXISTS v3_device_current_state (
        device_id TEXT PRIMARY KEY,
        connection_status TEXT NOT NULL DEFAULT 'unknown'
            CHECK (connection_status IN ('unknown', 'online', 'stale', 'offline')),
        ip_address TEXT,
        last_observed_at TEXT,
        last_received_at TEXT,
        last_observation_id INTEGER,
        state_version INTEGER NOT NULL DEFAULT 0 CHECK (state_version >= 0),
        updated_at TEXT NOT NULL,
        FOREIGN KEY (device_id) REFERENCES v3_device_profiles(device_id),
        FOREIGN KEY (last_observation_id)
            REFERENCES v3_device_state_observations(observation_id)
    )
    """.strip(),
    """
    CREATE TABLE IF NOT EXISTS v3_system_component_health (
        component_id TEXT PRIMARY KEY,
        readiness TEXT NOT NULL
            CHECK (readiness IN ('warming_up', 'ready', 'degraded')),
        started_at TEXT NOT NULL,
        ready_at TEXT,
        reason TEXT,
        state_version INTEGER NOT NULL DEFAULT 1 CHECK (state_version > 0),
        updated_at TEXT NOT NULL
    )
    """.strip(),
    """
    CREATE INDEX IF NOT EXISTS idx_v3_observations_device_received
        ON v3_device_state_observations(device_id, received_at DESC)
    """.strip(),
    """
    CREATE INDEX IF NOT EXISTS idx_v3_profiles_area
        ON v3_device_profiles(area_id)
    """.strip(),
    """
    CREATE INDEX IF NOT EXISTS idx_v3_current_connection
        ON v3_device_current_state(connection_status)
    """.strip(),
)

# Retained as a readable schema representation for documentation and tooling.
V3_DEVICE_STATE_SCHEMA = ";\n\n".join(V3_DEVICE_STATE_STATEMENTS) + ";\n"


@dataclass(frozen=True)
class SchemaMigration:
    version: int
    name: str
    statements: tuple[str, ...]

    @property
    def checksum(self) -> str:
        material = "\n-- statement boundary --\n".join(self.statements)
        return sha256(material.encode("utf-8")).hexdigest()


V3_DEVICE_STATE_MIGRATION = SchemaMigration(
    version=1,
    name="device_state_foundation",
    statements=V3_DEVICE_STATE_STATEMENTS,
)

V3_MQTT_HEARTBEAT_STATEMENTS = (
    "ALTER TABLE v3_device_state_observations ADD COLUMN boot_id TEXT",
    "ALTER TABLE v3_device_state_observations ADD COLUMN firmware_version TEXT",
    "ALTER TABLE v3_device_state_observations ADD COLUMN uptime_ms INTEGER",
    """
    CREATE TABLE v3_mqtt_boot_sessions (
        device_id TEXT NOT NULL,
        boot_id TEXT NOT NULL,
        first_received_at TEXT NOT NULL,
        last_received_at TEXT NOT NULL,
        last_sequence INTEGER NOT NULL CHECK (last_sequence >= 0),
        last_uptime_ms INTEGER NOT NULL CHECK (last_uptime_ms >= 0),
        firmware_version TEXT NOT NULL,
        PRIMARY KEY (device_id, boot_id),
        FOREIGN KEY (device_id) REFERENCES v3_device_profiles(device_id)
    )
    """.strip(),
    """
    CREATE TABLE v3_mqtt_device_cursors (
        device_id TEXT PRIMARY KEY,
        current_boot_id TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        FOREIGN KEY (device_id) REFERENCES v3_device_profiles(device_id),
        FOREIGN KEY (device_id, current_boot_id)
            REFERENCES v3_mqtt_boot_sessions(device_id, boot_id)
    )
    """.strip(),
    """
    CREATE UNIQUE INDEX idx_v3_mqtt_observation_sequence
        ON v3_device_state_observations(device_id, boot_id, sequence)
        WHERE boot_id IS NOT NULL
    """.strip(),
    """
    CREATE INDEX idx_v3_mqtt_sessions_last_received
        ON v3_mqtt_boot_sessions(last_received_at DESC)
    """.strip(),
)

V3_MQTT_HEARTBEAT_MIGRATION = SchemaMigration(
    version=2,
    name="mqtt_heartbeat_sessions",
    statements=V3_MQTT_HEARTBEAT_STATEMENTS,
)

V3_REALTIME_EVENT_STATEMENTS = (
    """
    CREATE TABLE v3_realtime_events (
        event_id INTEGER PRIMARY KEY AUTOINCREMENT,
        event_type TEXT NOT NULL,
        occurred_at TEXT NOT NULL,
        device_id TEXT,
        state_version INTEGER
            CHECK (state_version IS NULL OR state_version >= 0),
        payload_json TEXT NOT NULL,
        FOREIGN KEY (device_id) REFERENCES v3_device_profiles(device_id)
    )
    """.strip(),
    """
    CREATE INDEX idx_v3_realtime_events_type
        ON v3_realtime_events(event_type, event_id)
    """.strip(),
    """
    CREATE INDEX idx_v3_realtime_events_device
        ON v3_realtime_events(device_id, event_id)
    """.strip(),
)

V3_REALTIME_EVENT_MIGRATION = SchemaMigration(
    version=3,
    name="realtime_event_log",
    statements=V3_REALTIME_EVENT_STATEMENTS,
)

V3_DEVICE_LIFECYCLE_STATEMENTS = (
    "ALTER TABLE v3_device_profiles ADD COLUMN importance TEXT NOT NULL "
    "DEFAULT 'normal' CHECK (importance IN ('low', 'normal', 'high', 'critical'))",
    "ALTER TABLE v3_device_profiles ADD COLUMN profile_source TEXT NOT NULL "
    "DEFAULT 'unclassified' CHECK (profile_source IN "
    "('unclassified', 'physical', 'virtual', 'gateway'))",
    "ALTER TABLE v3_device_profiles ADD COLUMN profile_version INTEGER NOT NULL "
    "DEFAULT 1 CHECK (profile_version > 0)",
    "ALTER TABLE v3_device_profiles ADD COLUMN retired_at TEXT",
    "ALTER TABLE v3_device_profiles ADD COLUMN retirement_reason TEXT",
    """
    CREATE TABLE v3_device_management_audit (
        audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
        device_id TEXT NOT NULL,
        action TEXT NOT NULL CHECK (action IN (
            'created', 'updated', 'operation_mode_changed',
            'retired', 'restored', 'deleted'
        )),
        actor_user_id INTEGER NOT NULL,
        actor_username TEXT NOT NULL,
        actor_role TEXT NOT NULL CHECK (actor_role IN ('admin', 'operator', 'user')),
        occurred_at TEXT NOT NULL,
        request_id TEXT NOT NULL,
        before_json TEXT,
        after_json TEXT
    )
    """.strip(),
    """
    CREATE INDEX idx_v3_device_management_audit_device
        ON v3_device_management_audit(device_id, audit_id)
    """.strip(),
    """
    CREATE INDEX idx_v3_profiles_lifecycle
        ON v3_device_profiles(retired_at, operation_mode, device_id)
    """.strip(),
)

V3_DEVICE_LIFECYCLE_MIGRATION = SchemaMigration(
    version=4,
    name="device_lifecycle_management",
    statements=V3_DEVICE_LIFECYCLE_STATEMENTS,
)

V3_DEVICE_TRAFFIC_STATEMENTS = (
    """
    CREATE TABLE v3_device_ip_bindings (
        binding_id INTEGER PRIMARY KEY AUTOINCREMENT,
        device_id TEXT NOT NULL,
        ip_address TEXT NOT NULL,
        valid_from TEXT NOT NULL,
        valid_to TEXT,
        source TEXT NOT NULL,
        source_observation_id INTEGER,
        created_at TEXT NOT NULL,
        CHECK (valid_to IS NULL OR valid_to > valid_from),
        UNIQUE (device_id, ip_address, valid_from),
        FOREIGN KEY (device_id) REFERENCES v3_device_profiles(device_id),
        FOREIGN KEY (source_observation_id)
            REFERENCES v3_device_state_observations(observation_id)
    )
    """.strip(),
    """
    CREATE UNIQUE INDEX idx_v3_device_ip_bindings_open_device
        ON v3_device_ip_bindings(device_id)
        WHERE valid_to IS NULL
    """.strip(),
    """
    CREATE INDEX idx_v3_device_ip_bindings_ip_time
        ON v3_device_ip_bindings(ip_address, valid_from, valid_to)
    """.strip(),
    """
    CREATE INDEX idx_v3_device_ip_bindings_device_time
        ON v3_device_ip_bindings(device_id, valid_from, valid_to)
    """.strip(),
    """
    CREATE TABLE v3_device_traffic_minutes (
        device_id TEXT NOT NULL,
        bucket_start TEXT NOT NULL,
        tx_bytes INTEGER NOT NULL DEFAULT 0 CHECK (tx_bytes >= 0),
        rx_bytes INTEGER NOT NULL DEFAULT 0 CHECK (rx_bytes >= 0),
        tx_packets INTEGER NOT NULL DEFAULT 0 CHECK (tx_packets >= 0),
        rx_packets INTEGER NOT NULL DEFAULT 0 CHECK (rx_packets >= 0),
        tx_flow_count INTEGER NOT NULL DEFAULT 0 CHECK (tx_flow_count >= 0),
        rx_flow_count INTEGER NOT NULL DEFAULT 0 CHECK (rx_flow_count >= 0),
        first_sample_at TEXT NOT NULL,
        last_sample_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (device_id, bucket_start),
        FOREIGN KEY (device_id) REFERENCES v3_device_profiles(device_id)
    )
    """.strip(),
    """
    CREATE INDEX idx_v3_device_traffic_minutes_bucket
        ON v3_device_traffic_minutes(bucket_start, device_id)
    """.strip(),
    """
    CREATE TABLE v3_device_traffic_protocol_minutes (
        device_id TEXT NOT NULL,
        bucket_start TEXT NOT NULL,
        direction TEXT NOT NULL CHECK (direction IN ('tx', 'rx')),
        protocol TEXT NOT NULL,
        bytes INTEGER NOT NULL DEFAULT 0 CHECK (bytes >= 0),
        packets INTEGER NOT NULL DEFAULT 0 CHECK (packets >= 0),
        flow_count INTEGER NOT NULL DEFAULT 0 CHECK (flow_count >= 0),
        first_sample_at TEXT NOT NULL,
        last_sample_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (device_id, bucket_start, direction, protocol),
        FOREIGN KEY (device_id) REFERENCES v3_device_profiles(device_id)
    )
    """.strip(),
    """
    CREATE INDEX idx_v3_device_traffic_protocol_bucket
        ON v3_device_traffic_protocol_minutes(bucket_start, protocol, device_id)
    """.strip(),
    """
    CREATE TABLE v3_device_traffic_peer_minutes (
        device_id TEXT NOT NULL,
        bucket_start TEXT NOT NULL,
        direction TEXT NOT NULL CHECK (direction IN ('tx', 'rx')),
        peer_key TEXT NOT NULL,
        peer_device_id TEXT,
        peer_ip TEXT NOT NULL,
        protocol TEXT NOT NULL,
        bytes INTEGER NOT NULL DEFAULT 0 CHECK (bytes >= 0),
        packets INTEGER NOT NULL DEFAULT 0 CHECK (packets >= 0),
        flow_count INTEGER NOT NULL DEFAULT 0 CHECK (flow_count >= 0),
        first_sample_at TEXT NOT NULL,
        last_sample_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (
            device_id, bucket_start, direction, peer_key, protocol
        ),
        FOREIGN KEY (device_id) REFERENCES v3_device_profiles(device_id),
        FOREIGN KEY (peer_device_id) REFERENCES v3_device_profiles(device_id)
    )
    """.strip(),
    """
    CREATE INDEX idx_v3_device_traffic_peer_window
        ON v3_device_traffic_peer_minutes(device_id, bucket_start, direction)
    """.strip(),
    """
    CREATE INDEX idx_v3_device_traffic_peer_sort
        ON v3_device_traffic_peer_minutes(device_id, bytes DESC, packets DESC)
    """.strip(),
    """
    CREATE TABLE v3_traffic_ingest_batches (
        source_id TEXT NOT NULL,
        source_session_id TEXT NOT NULL,
        batch_id TEXT NOT NULL,
        batch_sequence INTEGER NOT NULL CHECK (batch_sequence >= 0),
        received_at TEXT NOT NULL,
        first_sample_at TEXT,
        last_sample_at TEXT,
        accepted_samples INTEGER NOT NULL DEFAULT 0 CHECK (accepted_samples >= 0),
        rejected_samples INTEGER NOT NULL DEFAULT 0 CHECK (rejected_samples >= 0),
        duplicate_samples INTEGER NOT NULL DEFAULT 0 CHECK (duplicate_samples >= 0),
        unassigned_samples INTEGER NOT NULL DEFAULT 0 CHECK (unassigned_samples >= 0),
        status TEXT NOT NULL CHECK (status IN ('committed', 'partial', 'rejected')),
        created_at TEXT NOT NULL,
        PRIMARY KEY (source_id, source_session_id, batch_id)
    )
    """.strip(),
    """
    CREATE UNIQUE INDEX idx_v3_traffic_ingest_batch_sequence
        ON v3_traffic_ingest_batches(
            source_id, source_session_id, batch_sequence
        )
    """.strip(),
    """
    CREATE INDEX idx_v3_traffic_ingest_batches_received
        ON v3_traffic_ingest_batches(received_at)
    """.strip(),
    """
    CREATE TABLE v3_traffic_ingest_samples (
        source_id TEXT NOT NULL,
        source_session_id TEXT NOT NULL,
        sample_id TEXT NOT NULL,
        batch_id TEXT NOT NULL,
        occurred_at TEXT NOT NULL,
        created_at TEXT NOT NULL,
        PRIMARY KEY (source_id, source_session_id, sample_id),
        FOREIGN KEY (source_id, source_session_id, batch_id)
            REFERENCES v3_traffic_ingest_batches(
                source_id, source_session_id, batch_id
            )
    )
    """.strip(),
    """
    CREATE INDEX idx_v3_traffic_ingest_samples_occurred
        ON v3_traffic_ingest_samples(occurred_at)
    """.strip(),
    """
    CREATE TABLE v3_traffic_unassigned_minutes (
        source_id TEXT NOT NULL,
        bucket_start TEXT NOT NULL,
        reason_code TEXT NOT NULL,
        sample_count INTEGER NOT NULL DEFAULT 0 CHECK (sample_count >= 0),
        bytes INTEGER NOT NULL DEFAULT 0 CHECK (bytes >= 0),
        packets INTEGER NOT NULL DEFAULT 0 CHECK (packets >= 0),
        first_sample_at TEXT NOT NULL,
        last_sample_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (source_id, bucket_start, reason_code)
    )
    """.strip(),
    """
    CREATE INDEX idx_v3_traffic_unassigned_bucket
        ON v3_traffic_unassigned_minutes(bucket_start, reason_code)
    """.strip(),
)

V3_DEVICE_TRAFFIC_MIGRATION = SchemaMigration(
    version=5,
    name="device_traffic_aggregation",
    statements=V3_DEVICE_TRAFFIC_STATEMENTS,
)

V3_MOBILE_ACCESS_STATEMENTS = (
    """
    CREATE TABLE v3_mobile_scope_sets (
        user_id INTEGER PRIMARY KEY,
        scope_version INTEGER NOT NULL DEFAULT 0 CHECK (scope_version >= 0),
        updated_by INTEGER NOT NULL,
        updated_at TEXT NOT NULL
    )
    """.strip(),
    """
    CREATE TABLE v3_mobile_user_scopes (
        scope_id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        scope_kind TEXT NOT NULL CHECK (scope_kind IN ('device', 'area')),
        scope_value TEXT NOT NULL,
        scope_version INTEGER NOT NULL CHECK (scope_version > 0),
        created_by INTEGER NOT NULL,
        created_at TEXT NOT NULL,
        revoked_at TEXT
    )
    """.strip(),
    """
    CREATE UNIQUE INDEX idx_v3_mobile_scopes_active_unique
        ON v3_mobile_user_scopes(user_id, scope_kind, scope_value)
        WHERE revoked_at IS NULL
    """.strip(),
    """
    CREATE INDEX idx_v3_mobile_scopes_user_active
        ON v3_mobile_user_scopes(user_id, revoked_at, scope_kind, scope_value)
    """.strip(),
    """
    CREATE TABLE v3_mobile_pairings (
        pairing_id TEXT PRIMARY KEY,
        user_id INTEGER NOT NULL,
        code_selector TEXT NOT NULL UNIQUE,
        code_hash TEXT NOT NULL UNIQUE,
        expires_at TEXT NOT NULL,
        max_attempts INTEGER NOT NULL CHECK (max_attempts > 0),
        attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
        claimed_at TEXT,
        invalidated_at TEXT,
        created_by INTEGER NOT NULL,
        created_at TEXT NOT NULL
    )
    """.strip(),
    """
    CREATE INDEX idx_v3_mobile_pairings_user_active
        ON v3_mobile_pairings(user_id, claimed_at, invalidated_at, expires_at)
    """.strip(),
    """
    CREATE TABLE v3_mobile_sessions (
        session_id TEXT PRIMARY KEY,
        user_id INTEGER NOT NULL,
        client_instance_id TEXT NOT NULL,
        client_display_name TEXT NOT NULL,
        access_token_selector TEXT NOT NULL UNIQUE,
        access_token_hash TEXT NOT NULL UNIQUE,
        refresh_token_selector TEXT NOT NULL UNIQUE,
        refresh_token_hash TEXT NOT NULL UNIQUE,
        created_at TEXT NOT NULL,
        issued_at TEXT NOT NULL,
        access_expires_at TEXT NOT NULL,
        refresh_expires_at TEXT NOT NULL,
        last_seen_at TEXT NOT NULL,
        revoked_at TEXT,
        revoked_reason TEXT,
        token_generation INTEGER NOT NULL DEFAULT 1 CHECK (token_generation > 0)
    )
    """.strip(),
    """
    CREATE INDEX idx_v3_mobile_sessions_user_status
        ON v3_mobile_sessions(user_id, revoked_at, refresh_expires_at, session_id)
    """.strip(),
    """
    CREATE TABLE v3_mobile_refresh_history (
        refresh_token_selector TEXT PRIMARY KEY,
        refresh_token_hash TEXT NOT NULL UNIQUE,
        session_id TEXT NOT NULL,
        token_generation INTEGER NOT NULL CHECK (token_generation > 0),
        rotated_at TEXT NOT NULL,
        replayed_at TEXT
    )
    """.strip(),
    """
    CREATE INDEX idx_v3_mobile_refresh_history_session
        ON v3_mobile_refresh_history(session_id, token_generation)
    """.strip(),
    """
    CREATE TABLE v3_mobile_security_audit (
        audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
        action TEXT NOT NULL,
        user_id INTEGER,
        session_id TEXT,
        pairing_id TEXT,
        actor TEXT NOT NULL,
        occurred_at TEXT NOT NULL,
        request_id TEXT NOT NULL,
        result TEXT NOT NULL CHECK (result IN ('success', 'failure', 'no_op')),
        stable_reason_code TEXT NOT NULL
    )
    """.strip(),
    """
    CREATE INDEX idx_v3_mobile_audit_user_time
        ON v3_mobile_security_audit(user_id, occurred_at, audit_id)
    """.strip(),
    """
    CREATE INDEX idx_v3_mobile_audit_session
        ON v3_mobile_security_audit(session_id, audit_id)
    """.strip(),
    """
    CREATE TABLE v3_mobile_rate_limits (
        action TEXT NOT NULL,
        bucket_key TEXT NOT NULL,
        window_started_at TEXT NOT NULL,
        attempt_count INTEGER NOT NULL CHECK (attempt_count > 0),
        blocked_until TEXT,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (action, bucket_key)
    )
    """.strip(),
    """
    CREATE INDEX idx_v3_mobile_rate_limits_blocked
        ON v3_mobile_rate_limits(blocked_until, updated_at)
    """.strip(),
)

V3_MOBILE_ACCESS_MIGRATION = SchemaMigration(
    version=6,
    name="mobile_pairing_and_scoped_sessions",
    statements=V3_MOBILE_ACCESS_STATEMENTS,
)

V3_MOBILE_USER_ADMIN_STATEMENTS = (
    """
    CREATE TABLE v3_mobile_user_profiles (
        user_id INTEGER PRIMARY KEY,
        display_name TEXT NOT NULL,
        mobile_only INTEGER NOT NULL DEFAULT 0 CHECK (mobile_only IN (0, 1)),
        account_status TEXT NOT NULL DEFAULT 'active'
            CHECK (account_status IN ('active', 'disabled')),
        profile_version INTEGER NOT NULL DEFAULT 1 CHECK (profile_version > 0),
        created_by INTEGER NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        disabled_at TEXT,
        disabled_reason TEXT
    )
    """.strip(),
    """
    CREATE INDEX idx_v3_mobile_user_profiles_status
        ON v3_mobile_user_profiles(account_status, mobile_only, user_id)
    """.strip(),
)

V3_MOBILE_USER_ADMIN_MIGRATION = SchemaMigration(
    version=7,
    name="mobile_user_administration",
    statements=V3_MOBILE_USER_ADMIN_STATEMENTS,
)

V3_INCIDENT_WORKFLOW_STATEMENTS = (
    """
    CREATE TABLE v3_incidents (
        incident_id TEXT PRIMARY KEY,
        incident_type TEXT NOT NULL,
        severity TEXT NOT NULL
            CHECK (severity IN ('info', 'low', 'medium', 'high', 'critical')),
        status TEXT NOT NULL DEFAULT 'open'
            CHECK (status IN (
                'open', 'acknowledged', 'recovering', 'resolved', 'false_positive'
            )),
        source TEXT NOT NULL CHECK (source IN ('manual', 'rule', 'system')),
        admin_title TEXT NOT NULL,
        admin_summary TEXT NOT NULL,
        user_title TEXT NOT NULL,
        user_summary TEXT NOT NULL,
        mobile_published INTEGER NOT NULL DEFAULT 1
            CHECK (mobile_published IN (0, 1)),
        first_seen_at TEXT NOT NULL,
        last_seen_at TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        resolved_at TEXT,
        incident_version INTEGER NOT NULL DEFAULT 1
            CHECK (incident_version > 0),
        created_by INTEGER NOT NULL,
        resolution_summary TEXT,
        false_positive_reason TEXT
    )
    """.strip(),
    """
    CREATE INDEX idx_v3_incidents_status_updated
        ON v3_incidents(status, updated_at DESC, incident_id)
    """.strip(),
    """
    CREATE INDEX idx_v3_incidents_source_severity
        ON v3_incidents(source, severity, first_seen_at DESC)
    """.strip(),
    """
    CREATE TABLE v3_incident_devices (
        incident_id TEXT NOT NULL,
        device_id TEXT NOT NULL,
        incident_role TEXT NOT NULL
            CHECK (incident_role IN (
                'affected', 'suspected_source', 'observer', 'unknown'
            )),
        user_visible INTEGER NOT NULL DEFAULT 0
            CHECK (user_visible IN (0, 1)),
        created_at TEXT NOT NULL,
        PRIMARY KEY (incident_id, device_id, incident_role),
        CHECK (user_visible = 0 OR incident_role = 'affected'),
        FOREIGN KEY (incident_id) REFERENCES v3_incidents(incident_id),
        FOREIGN KEY (device_id) REFERENCES v3_device_profiles(device_id)
    )
    """.strip(),
    """
    CREATE INDEX idx_v3_incident_devices_device
        ON v3_incident_devices(device_id, incident_role, incident_id)
    """.strip(),
    """
    CREATE INDEX idx_v3_incident_devices_mobile
        ON v3_incident_devices(incident_id, user_visible, incident_role, device_id)
    """.strip(),
    """
    CREATE TABLE v3_incident_timeline (
        timeline_id INTEGER PRIMARY KEY AUTOINCREMENT,
        incident_id TEXT NOT NULL,
        action TEXT NOT NULL,
        actor_user_id INTEGER,
        actor_username TEXT NOT NULL,
        actor_role TEXT NOT NULL,
        occurred_at TEXT NOT NULL,
        request_id TEXT NOT NULL,
        public_progress TEXT,
        admin_details TEXT,
        resulting_status TEXT NOT NULL,
        incident_version INTEGER NOT NULL CHECK (incident_version > 0),
        FOREIGN KEY (incident_id) REFERENCES v3_incidents(incident_id)
    )
    """.strip(),
    """
    CREATE INDEX idx_v3_incident_timeline_incident
        ON v3_incident_timeline(incident_id, timeline_id)
    """.strip(),
    """
    CREATE TABLE v3_mobile_notice_acknowledgements (
        incident_id TEXT NOT NULL,
        user_id INTEGER NOT NULL,
        first_read_at TEXT,
        acknowledged_at TEXT,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (incident_id, user_id),
        FOREIGN KEY (incident_id) REFERENCES v3_incidents(incident_id)
    )
    """.strip(),
    """
    CREATE INDEX idx_v3_mobile_notice_ack_user
        ON v3_mobile_notice_acknowledgements(user_id, updated_at, incident_id)
    """.strip(),
    """
    CREATE TABLE v3_mobile_notice_changes (
        change_id INTEGER PRIMARY KEY AUTOINCREMENT,
        incident_id TEXT NOT NULL,
        user_id INTEGER,
        change_kind TEXT NOT NULL
            CHECK (change_kind IN (
                'opened', 'updated', 'recovering', 'resolved',
                'false_positive', 'read', 'acknowledged'
            )),
        incident_version INTEGER NOT NULL CHECK (incident_version > 0),
        changed_at TEXT NOT NULL,
        FOREIGN KEY (incident_id) REFERENCES v3_incidents(incident_id)
    )
    """.strip(),
    """
    CREATE INDEX idx_v3_mobile_notice_changes_cursor
        ON v3_mobile_notice_changes(change_id, incident_id, user_id)
    """.strip(),
    """
    CREATE TABLE v3_help_requests (
        help_request_id TEXT PRIMARY KEY,
        user_id INTEGER NOT NULL,
        mobile_session_id TEXT NOT NULL,
        incident_id TEXT,
        device_id TEXT,
        category TEXT NOT NULL
            CHECK (category IN (
                'device_issue', 'security_question', 'service_problem', 'other'
            )),
        user_message TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'open'
            CHECK (status IN ('open', 'in_progress', 'waiting_for_user', 'closed')),
        public_response TEXT,
        internal_note TEXT,
        assigned_to INTEGER,
        idempotency_key TEXT NOT NULL,
        request_fingerprint TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        closed_at TEXT,
        request_version INTEGER NOT NULL DEFAULT 1 CHECK (request_version > 0),
        UNIQUE (user_id, mobile_session_id, idempotency_key),
        FOREIGN KEY (mobile_session_id) REFERENCES v3_mobile_sessions(session_id),
        FOREIGN KEY (incident_id) REFERENCES v3_incidents(incident_id),
        FOREIGN KEY (device_id) REFERENCES v3_device_profiles(device_id)
    )
    """.strip(),
    """
    CREATE INDEX idx_v3_help_requests_user
        ON v3_help_requests(user_id, updated_at DESC, help_request_id)
    """.strip(),
    """
    CREATE INDEX idx_v3_help_requests_status
        ON v3_help_requests(status, updated_at DESC, help_request_id)
    """.strip(),
    """
    CREATE INDEX idx_v3_help_requests_incident_device
        ON v3_help_requests(incident_id, device_id, help_request_id)
    """.strip(),
    """
    CREATE TABLE v3_help_request_timeline (
        timeline_id INTEGER PRIMARY KEY AUTOINCREMENT,
        help_request_id TEXT NOT NULL,
        action TEXT NOT NULL,
        actor_user_id INTEGER,
        actor_username TEXT NOT NULL,
        actor_role TEXT NOT NULL,
        occurred_at TEXT NOT NULL,
        request_id TEXT NOT NULL,
        public_response TEXT,
        internal_note TEXT,
        resulting_status TEXT NOT NULL,
        request_version INTEGER NOT NULL CHECK (request_version > 0),
        FOREIGN KEY (help_request_id) REFERENCES v3_help_requests(help_request_id)
    )
    """.strip(),
    """
    CREATE INDEX idx_v3_help_request_timeline_request
        ON v3_help_request_timeline(help_request_id, timeline_id)
    """.strip(),
    """
    CREATE TABLE v3_support_contacts (
        contact_id TEXT PRIMARY KEY,
        display_name TEXT NOT NULL,
        phone TEXT,
        email TEXT,
        working_hours TEXT,
        public_note TEXT,
        enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
        config_version INTEGER NOT NULL DEFAULT 1 CHECK (config_version > 0),
        updated_by INTEGER NOT NULL,
        updated_at TEXT NOT NULL
    )
    """.strip(),
    """
    CREATE TABLE v3_incident_workflow_audit (
        audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
        entity_type TEXT NOT NULL
            CHECK (entity_type IN ('incident', 'notice', 'help_request', 'support_contact')),
        entity_id TEXT NOT NULL,
        action TEXT NOT NULL,
        actor_user_id INTEGER,
        actor_username TEXT NOT NULL,
        actor_role TEXT NOT NULL,
        occurred_at TEXT NOT NULL,
        request_id TEXT NOT NULL,
        result TEXT NOT NULL CHECK (result IN ('success', 'no_op'))
    )
    """.strip(),
    """
    CREATE INDEX idx_v3_incident_workflow_audit_entity
        ON v3_incident_workflow_audit(entity_type, entity_id, audit_id)
    """.strip(),
)

V3_INCIDENT_WORKFLOW_MIGRATION = SchemaMigration(
    version=8,
    name="incident_and_mobile_notice_workflow",
    statements=V3_INCIDENT_WORKFLOW_STATEMENTS,
)

V3_DEVICE_DISCOVERY_STATEMENTS = (
    """
    CREATE TABLE v3_discovered_device_candidates (
        candidate_id TEXT PRIMARY KEY,
        identity_kind TEXT NOT NULL CHECK (identity_kind = 'mac'),
        identity_value TEXT NOT NULL,
        proposed_device_id TEXT,
        latest_ip TEXT,
        status TEXT NOT NULL DEFAULT 'pending'
            CHECK (status IN ('pending', 'ignored', 'claimed', 'conflict')),
        conflict_reason TEXT
            CHECK (conflict_reason IS NULL OR conflict_reason IN (
                'mac_already_bound', 'multiple_proposed_device_ids', 'deduplication_key_reused'
            )),
        first_seen_at TEXT NOT NULL,
        last_seen_at TEXT NOT NULL,
        source_count INTEGER NOT NULL DEFAULT 0 CHECK (source_count >= 0),
        observation_count INTEGER NOT NULL DEFAULT 0 CHECK (observation_count >= 0),
        claimed_device_id TEXT,
        claimed_at TEXT,
        ignored_at TEXT,
        ignored_reason TEXT,
        candidate_version INTEGER NOT NULL DEFAULT 1 CHECK (candidate_version > 0),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE (identity_kind, identity_value),
        FOREIGN KEY (claimed_device_id)
            REFERENCES v3_device_profiles(device_id)
    )
    """.strip(),
    """
    CREATE TABLE v3_discovery_observations (
        observation_id INTEGER PRIMARY KEY AUTOINCREMENT,
        candidate_id TEXT NOT NULL,
        source TEXT NOT NULL CHECK (source IN (
            'mqtt_unknown', 'dhcp', 'arp', 'probe', 'other'
        )),
        observed_at TEXT NOT NULL,
        received_at TEXT NOT NULL,
        proposed_device_id TEXT,
        ip_address TEXT,
        evidence_hash TEXT NOT NULL CHECK (length(evidence_hash) = 64),
        sanitized_metadata_json TEXT NOT NULL,
        deduplication_key TEXT NOT NULL CHECK (length(deduplication_key) = 64),
        UNIQUE (candidate_id, deduplication_key),
        FOREIGN KEY (candidate_id)
            REFERENCES v3_discovered_device_candidates(candidate_id)
    )
    """.strip(),
    """
    CREATE TABLE v3_discovery_actions (
        action_id INTEGER PRIMARY KEY AUTOINCREMENT,
        candidate_id TEXT NOT NULL,
        action TEXT NOT NULL CHECK (action IN ('claimed', 'ignored', 'restored')),
        actor_user_id INTEGER NOT NULL,
        actor_username TEXT NOT NULL,
        actor_role TEXT NOT NULL CHECK (actor_role IN ('admin', 'operator', 'user')),
        occurred_at TEXT NOT NULL,
        request_id TEXT NOT NULL,
        before_json TEXT,
        after_json TEXT,
        reason TEXT,
        FOREIGN KEY (candidate_id)
            REFERENCES v3_discovered_device_candidates(candidate_id)
    )
    """.strip(),
    "CREATE INDEX idx_v3_discovery_candidates_status_seen "
    "ON v3_discovered_device_candidates(status, last_seen_at DESC, candidate_id)",
    "CREATE INDEX idx_v3_discovery_candidates_first_seen "
    "ON v3_discovered_device_candidates(first_seen_at, candidate_id)",
    "CREATE INDEX idx_v3_discovery_candidates_identity "
    "ON v3_discovered_device_candidates(identity_kind, identity_value)",
    "CREATE INDEX idx_v3_discovery_candidates_claimed_device "
    "ON v3_discovered_device_candidates(claimed_device_id)",
    "CREATE INDEX idx_v3_discovery_observations_candidate_time "
    "ON v3_discovery_observations(candidate_id, received_at DESC, observation_id)",
    "CREATE INDEX idx_v3_discovery_observations_source_time "
    "ON v3_discovery_observations(source, received_at DESC, candidate_id)",
    "CREATE UNIQUE INDEX idx_v3_discovery_observations_dedup "
    "ON v3_discovery_observations(candidate_id, deduplication_key)",
    "CREATE INDEX idx_v3_discovery_actions_candidate "
    "ON v3_discovery_actions(candidate_id, action_id)",
)

V3_DEVICE_DISCOVERY_MIGRATION = SchemaMigration(
    version=9,
    name="unknown_device_discovery",
    statements=V3_DEVICE_DISCOVERY_STATEMENTS,
)

V3_MIGRATIONS = (
    V3_DEVICE_STATE_MIGRATION,
    V3_MQTT_HEARTBEAT_MIGRATION,
    V3_REALTIME_EVENT_MIGRATION,
    V3_DEVICE_LIFECYCLE_MIGRATION,
    V3_DEVICE_TRAFFIC_MIGRATION,
    V3_MOBILE_ACCESS_MIGRATION,
    V3_MOBILE_USER_ADMIN_MIGRATION,
    V3_INCIDENT_WORKFLOW_MIGRATION,
    V3_DEVICE_DISCOVERY_MIGRATION,
)

V3_DEVICE_STATE_TABLES = frozenset(
    {
        "v3_device_profiles",
        "v3_device_current_state",
        "v3_device_state_observations",
        "v3_system_component_health",
    }
)

V3_DEVICE_STATE_INDEXES = frozenset(
    {
        "idx_v3_observations_device_received",
        "idx_v3_profiles_area",
        "idx_v3_current_connection",
    }
)

V3_MQTT_HEARTBEAT_TABLES = frozenset(
    {
        "v3_mqtt_boot_sessions",
        "v3_mqtt_device_cursors",
    }
)

V3_MQTT_HEARTBEAT_INDEXES = frozenset(
    {
        "idx_v3_mqtt_observation_sequence",
        "idx_v3_mqtt_sessions_last_received",
    }
)

V3_REALTIME_EVENT_TABLES = frozenset({"v3_realtime_events"})

V3_REALTIME_EVENT_INDEXES = frozenset(
    {
        "idx_v3_realtime_events_type",
        "idx_v3_realtime_events_device",
    }
)

V3_DEVICE_LIFECYCLE_TABLES = frozenset({"v3_device_management_audit"})

V3_DEVICE_LIFECYCLE_INDEXES = frozenset(
    {
        "idx_v3_device_management_audit_device",
        "idx_v3_profiles_lifecycle",
    }
)

V3_DEVICE_TRAFFIC_TABLES = frozenset(
    {
        "v3_device_ip_bindings",
        "v3_device_traffic_minutes",
        "v3_device_traffic_protocol_minutes",
        "v3_device_traffic_peer_minutes",
        "v3_traffic_ingest_batches",
        "v3_traffic_ingest_samples",
        "v3_traffic_unassigned_minutes",
    }
)

V3_DEVICE_TRAFFIC_INDEXES = frozenset(
    {
        "idx_v3_device_ip_bindings_open_device",
        "idx_v3_device_ip_bindings_ip_time",
        "idx_v3_device_ip_bindings_device_time",
        "idx_v3_device_traffic_minutes_bucket",
        "idx_v3_device_traffic_protocol_bucket",
        "idx_v3_device_traffic_peer_window",
        "idx_v3_device_traffic_peer_sort",
        "idx_v3_traffic_ingest_batch_sequence",
        "idx_v3_traffic_ingest_batches_received",
        "idx_v3_traffic_ingest_samples_occurred",
        "idx_v3_traffic_unassigned_bucket",
    }
)

V3_MOBILE_ACCESS_TABLES = frozenset(
    {
        "v3_mobile_scope_sets",
        "v3_mobile_user_scopes",
        "v3_mobile_pairings",
        "v3_mobile_sessions",
        "v3_mobile_refresh_history",
        "v3_mobile_security_audit",
        "v3_mobile_rate_limits",
    }
)

V3_MOBILE_ACCESS_INDEXES = frozenset(
    {
        "idx_v3_mobile_scopes_active_unique",
        "idx_v3_mobile_scopes_user_active",
        "idx_v3_mobile_pairings_user_active",
        "idx_v3_mobile_sessions_user_status",
        "idx_v3_mobile_refresh_history_session",
        "idx_v3_mobile_audit_user_time",
        "idx_v3_mobile_audit_session",
        "idx_v3_mobile_rate_limits_blocked",
    }
)

V3_MOBILE_USER_ADMIN_TABLES = frozenset({"v3_mobile_user_profiles"})
V3_MOBILE_USER_ADMIN_INDEXES = frozenset(
    {"idx_v3_mobile_user_profiles_status"}
)

V3_INCIDENT_WORKFLOW_TABLES = frozenset(
    {
        "v3_incidents",
        "v3_incident_devices",
        "v3_incident_timeline",
        "v3_mobile_notice_acknowledgements",
        "v3_mobile_notice_changes",
        "v3_help_requests",
        "v3_help_request_timeline",
        "v3_support_contacts",
        "v3_incident_workflow_audit",
    }
)

V3_INCIDENT_WORKFLOW_INDEXES = frozenset(
    {
        "idx_v3_incidents_status_updated",
        "idx_v3_incidents_source_severity",
        "idx_v3_incident_devices_device",
        "idx_v3_incident_devices_mobile",
        "idx_v3_incident_timeline_incident",
        "idx_v3_mobile_notice_ack_user",
        "idx_v3_mobile_notice_changes_cursor",
        "idx_v3_help_requests_user",
        "idx_v3_help_requests_status",
        "idx_v3_help_requests_incident_device",
        "idx_v3_help_request_timeline_request",
        "idx_v3_incident_workflow_audit_entity",
    }
)

V3_DEVICE_DISCOVERY_TABLES = frozenset(
    {
        "v3_discovered_device_candidates",
        "v3_discovery_observations",
        "v3_discovery_actions",
    }
)

V3_DEVICE_DISCOVERY_INDEXES = frozenset(
    {
        "idx_v3_discovery_candidates_status_seen",
        "idx_v3_discovery_candidates_first_seen",
        "idx_v3_discovery_candidates_identity",
        "idx_v3_discovery_candidates_claimed_device",
        "idx_v3_discovery_observations_candidate_time",
        "idx_v3_discovery_observations_source_time",
        "idx_v3_discovery_observations_dedup",
        "idx_v3_discovery_actions_candidate",
    }
)

V3_EXPECTED_OBJECTS = {
    MIGRATION_TABLE: "table",
    **{name: "table" for name in V3_DEVICE_STATE_TABLES},
    **{name: "table" for name in V3_MQTT_HEARTBEAT_TABLES},
    **{name: "index" for name in V3_DEVICE_STATE_INDEXES},
    **{name: "index" for name in V3_MQTT_HEARTBEAT_INDEXES},
    **{name: "table" for name in V3_REALTIME_EVENT_TABLES},
    **{name: "index" for name in V3_REALTIME_EVENT_INDEXES},
    **{name: "table" for name in V3_DEVICE_LIFECYCLE_TABLES},
    **{name: "index" for name in V3_DEVICE_LIFECYCLE_INDEXES},
    **{name: "table" for name in V3_DEVICE_TRAFFIC_TABLES},
    **{name: "index" for name in V3_DEVICE_TRAFFIC_INDEXES},
    **{name: "table" for name in V3_MOBILE_ACCESS_TABLES},
    **{name: "index" for name in V3_MOBILE_ACCESS_INDEXES},
    **{name: "table" for name in V3_MOBILE_USER_ADMIN_TABLES},
    **{name: "index" for name in V3_MOBILE_USER_ADMIN_INDEXES},
    **{name: "table" for name in V3_INCIDENT_WORKFLOW_TABLES},
    **{name: "index" for name in V3_INCIDENT_WORKFLOW_INDEXES},
    **{name: "table" for name in V3_DEVICE_DISCOVERY_TABLES},
    **{name: "index" for name in V3_DEVICE_DISCOVERY_INDEXES},
}

V3_EXPECTED_OBJECT_VERSIONS = {
    MIGRATION_TABLE: 1,
    **{name: 1 for name in V3_DEVICE_STATE_TABLES},
    **{name: 1 for name in V3_DEVICE_STATE_INDEXES},
    **{name: 2 for name in V3_MQTT_HEARTBEAT_TABLES},
    **{name: 2 for name in V3_MQTT_HEARTBEAT_INDEXES},
    **{name: 3 for name in V3_REALTIME_EVENT_TABLES},
    **{name: 3 for name in V3_REALTIME_EVENT_INDEXES},
    **{name: 4 for name in V3_DEVICE_LIFECYCLE_TABLES},
    **{name: 4 for name in V3_DEVICE_LIFECYCLE_INDEXES},
    **{name: 5 for name in V3_DEVICE_TRAFFIC_TABLES},
    **{name: 5 for name in V3_DEVICE_TRAFFIC_INDEXES},
    **{name: 6 for name in V3_MOBILE_ACCESS_TABLES},
    **{name: 6 for name in V3_MOBILE_ACCESS_INDEXES},
    **{name: 7 for name in V3_MOBILE_USER_ADMIN_TABLES},
    **{name: 7 for name in V3_MOBILE_USER_ADMIN_INDEXES},
    **{name: 8 for name in V3_INCIDENT_WORKFLOW_TABLES},
    **{name: 8 for name in V3_INCIDENT_WORKFLOW_INDEXES},
    **{name: 9 for name in V3_DEVICE_DISCOVERY_TABLES},
    **{name: 9 for name in V3_DEVICE_DISCOVERY_INDEXES},
}


class MigrationError(RuntimeError):
    """Raised when a migration cannot be applied atomically."""


def _utc_text(value: datetime | None = None) -> str:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise MigrationError("migration timestamp must be timezone-aware")
    return current.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _migration_table_exists(connection: sqlite3.Connection) -> bool:
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (MIGRATION_TABLE,),
    ).fetchone() is not None


def read_applied_migrations(connection: sqlite3.Connection) -> list[dict]:
    """Return migration ledger rows without creating the ledger."""
    if not _migration_table_exists(connection):
        return []
    rows = connection.execute(
        "SELECT version, name, checksum, applied_at "
        "FROM v3_schema_migrations ORDER BY version"
    ).fetchall()
    return [
        {
            "version": row[0],
            "name": row[1],
            "checksum": row[2],
            "applied_at": row[3],
        }
        for row in rows
    ]


def current_v3_schema_version(connection: sqlite3.Connection) -> int:
    applied = read_applied_migrations(connection)
    return max((row["version"] for row in applied), default=0)


def _integrity_results(connection: sqlite3.Connection) -> list[str]:
    return [str(row[0]) for row in connection.execute("PRAGMA integrity_check")]


def apply_v3_migrations(
    connection: sqlite3.Connection,
    migrations: Iterable[SchemaMigration] = V3_MIGRATIONS,
    *,
    applied_at: datetime | None = None,
) -> dict:
    """Apply pending migrations in one transaction and record their checksums."""
    ordered = tuple(sorted(migrations, key=lambda item: item.version))
    if len({item.version for item in ordered}) != len(ordered):
        raise MigrationError("migration versions must be unique")
    timestamp = _utc_text(applied_at)
    applied_versions: list[int] = []
    skipped_versions: list[int] = []

    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(MIGRATION_TABLE_SQL)
        ledger = {
            row["version"]: row
            for row in read_applied_migrations(connection)
        }

        for migration in ordered:
            existing = ledger.get(migration.version)
            if existing:
                if (
                    existing["name"] != migration.name
                    or existing["checksum"] != migration.checksum
                ):
                    raise MigrationError(
                        f"migration {migration.version} ledger checksum/name mismatch"
                    )
                skipped_versions.append(migration.version)
                continue

            for statement in migration.statements:
                connection.execute(statement)
            connection.execute(
                "INSERT INTO v3_schema_migrations "
                "(version, name, checksum, applied_at) VALUES (?, ?, ?, ?)",
                (
                    migration.version,
                    migration.name,
                    migration.checksum,
                    timestamp,
                ),
            )
            applied_versions.append(migration.version)

        transaction_integrity = _integrity_results(connection)
        if transaction_integrity != ["ok"]:
            raise MigrationError(
                "post-migration integrity_check failed inside transaction: "
                + "; ".join(transaction_integrity)
            )
        connection.commit()
    except Exception as exc:
        connection.rollback()
        if isinstance(exc, MigrationError):
            raise
        raise MigrationError(f"v3 migration transaction rolled back: {exc}") from exc

    return {
        "applied_versions": applied_versions,
        "skipped_versions": skipped_versions,
        "schema_version": current_v3_schema_version(connection),
        "transaction_integrity_check": transaction_integrity,
    }


def connect_v3(database_path: str | Path) -> sqlite3.Connection:
    """Open an explicit SQLite database with v3 safety settings enabled."""
    connection = sqlite3.connect(str(database_path))
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


def connect_v3_existing(database_path: str | Path) -> sqlite3.Connection:
    """Open an existing SQLite database read/write without implicit creation."""
    path = Path(database_path)
    if not path.is_file():
        raise FileNotFoundError(f"v3 database does not exist: {path}")
    connection = sqlite3.connect(f"{path.resolve().as_uri()}?mode=rw", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


def initialize_v3_database(database_path: str | Path) -> None:
    """Create and register additive v3 schema; safe to call repeatedly."""
    connection = connect_v3(database_path)
    try:
        apply_v3_migrations(connection)
    finally:
        connection.close()
