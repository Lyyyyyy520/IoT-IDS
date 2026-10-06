from flask import Flask, jsonify, session

from services.auth import effective_role, require_admin, require_operator


def _app():
    app = Flask(__name__)
    app.secret_key = "test-only-secret"

    @app.get("/operator")
    @require_operator
    def operator_route():
        return jsonify({"ok": True})

    @app.get("/admin")
    @require_admin
    def admin_route():
        return jsonify({"ok": True})

    return app


def _login(client, username: str, role: str):
    with client.session_transaction() as state:
        state["user_id"] = 1
        state["username"] = username
        state["role"] = role


def test_effective_role_uses_stored_role_without_username_escalation():
    assert effective_role("admin", "user") == "user"
    assert effective_role("admin", "admin") == "admin"
    assert effective_role("on-duty", "operator") == "operator"
    assert effective_role("resident", "unexpected") == "user"


def test_operator_can_handle_incidents_but_cannot_manage_configuration():
    client = _app().test_client()
    _login(client, "on-duty", "operator")
    assert client.get("/operator").status_code == 200
    assert client.get("/admin").status_code == 403


def test_user_cannot_enter_web_operator_routes():
    client = _app().test_client()
    _login(client, "resident", "user")
    assert client.get("/operator").status_code == 403


def test_admin_can_enter_both_route_classes():
    client = _app().test_client()
    _login(client, "admin", "admin")
    assert client.get("/operator").status_code == 200
    assert client.get("/admin").status_code == 200
