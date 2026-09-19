from flask import Flask

import api.probe as probe_api


def _app():
    app = Flask(__name__)
    app.register_blueprint(probe_api.probe_bp)
    return app


def test_probe_route_fails_closed_when_server_token_is_missing(monkeypatch):
    monkeypatch.delenv("IOT_IDS_PROBE_TOKEN", raising=False)
    response = _app().test_client().post("/api/probe/register", json={"name": "Pi-001"})
    assert response.status_code == 503


def test_probe_route_rejects_missing_and_invalid_tokens(monkeypatch):
    monkeypatch.setenv("IOT_IDS_PROBE_TOKEN", "expected-token")
    client = _app().test_client()
    assert client.post("/api/probe/register", json={"name": "Pi-001"}).status_code == 401
    assert client.post(
        "/api/probe/register",
        json={"name": "Pi-001"},
        headers={"X-Probe-Token": "wrong-token"},
    ).status_code == 403


def test_probe_route_accepts_independent_probe_token(monkeypatch):
    monkeypatch.setenv("IOT_IDS_PROBE_TOKEN", "expected-token")
    monkeypatch.setattr(probe_api, "query_one", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(probe_api, "execute", lambda *_args, **_kwargs: 42)

    response = _app().test_client().post(
        "/api/probe/register",
        json={"name": "Pi-001"},
        headers={"Authorization": "Bearer expected-token"},
    )
    assert response.status_code == 200
    assert response.get_json()["probe_id"] == 42
