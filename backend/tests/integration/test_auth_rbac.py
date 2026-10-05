"""Login, /auth/me and the role matrix (spec 5, 6)."""
from __future__ import annotations

import pytest

MUTATIONS = [
    ("post", "/api/v1/work-orders", {"aircraft": "Fighter-01", "part": "engine",
                                     "due_date": "2026-12-01"}),
    ("post", "/api/v1/telemetry", {"aircraft": "Fighter-01", "rows": []}),
    ("post", "/api/v1/internal/ml/predict", {"window": []}),
    ("post", "/api/v1/demo/pause", None),
    ("post", "/api/v1/demo/resume", None),
    ("post", "/api/v1/demo/tick", None),
    ("post", "/api/v1/seed/run", None),
]

READS = [
    "/api/v1/fleet/summary",
    "/api/v1/aircraft",
    "/api/v1/aircraft/Fighter-01",
    "/api/v1/aircraft/Fighter-01/engine",
    "/api/v1/aircraft/Fighter-01/parts/engine",
    "/api/v1/fleet/heatmap",
    "/api/v1/fleet/actions",
    "/api/v1/maintenance/schedule",
    "/api/v1/spares",
    "/api/v1/agencies",
    "/api/v1/alerts",
]


@pytest.mark.parametrize("username,password,role", [
    ("commander", "commander123", "commander"),
    ("officer", "officer123", "maintenance_officer"),
    ("viewer", "viewer123", "viewer"),
])
def test_login_succeeds_for_each_demo_user(client, username, password, role):
    response = client.post("/api/v1/auth/login",
                           json={"username": username, "password": password})
    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["user"]["role"] == role
    assert body["user"]["username"] == username
    assert body["access_token"]


def test_login_rejects_a_wrong_password(client):
    response = client.post("/api/v1/auth/login",
                           json={"username": "commander", "password": "nope"})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHORIZED"


def test_login_message_does_not_leak_whether_the_user_exists(client):
    unknown = client.post("/api/v1/auth/login",
                          json={"username": "nobody", "password": "x"})
    wrong = client.post("/api/v1/auth/login",
                        json={"username": "commander", "password": "x"})
    assert unknown.json()["error"]["message"] == wrong.json()["error"]["message"]


def test_me_reports_the_role_and_permissions(client, auth):
    response = client.get("/api/v1/auth/me", headers=auth("viewer"))
    body = response.json()
    assert body["role"] == "viewer"
    assert body["permissions"]["can_mutate"] is False
    assert body["permissions"]["can_manage_agencies"] is False


def test_officer_can_mutate_but_not_manage_agencies(client, auth):
    body = client.get("/api/v1/auth/me", headers=auth("officer")).json()
    assert body["permissions"]["can_mutate"] is True
    assert body["permissions"]["can_manage_agencies"] is False


@pytest.mark.parametrize("path", READS)
@pytest.mark.parametrize("role", ["commander", "officer", "viewer"])
def test_all_roles_can_read(client, auth, path, role):
    assert client.get(path, headers=auth(role)).status_code == 200


def test_missing_token_is_401_not_403(client):
    response = client.get("/api/v1/fleet/summary")
    assert response.status_code == 401


def test_malformed_token_is_401(client):
    response = client.get("/api/v1/fleet/summary",
                          headers={"Authorization": "Bearer not-a-jwt"})
    assert response.status_code == 401


def test_expired_token_is_401(client):
    import jwt
    response = client.get("/api/v1/fleet/summary", headers={
        "Authorization": "Bearer " + jwt.encode(
            {"sub": "1", "role": "commander", "exp": 1}, "test-secret-0123456789abcdefghijklmnop",
            algorithm="HS256",
        )
    })
    assert response.status_code == 401


@pytest.mark.parametrize("method,path,payload", MUTATIONS)
def test_viewer_is_forbidden_from_every_mutation(client, auth, method, path, payload):
    response = getattr(client, method)(path, json=payload, headers=auth("viewer"))
    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == "FORBIDDEN"


def test_viewer_403_is_distinguishable_from_401(client, auth):
    forbidden = client.post("/api/v1/demo/pause", json=None, headers=auth("viewer"))
    unauthenticated = client.post("/api/v1/demo/pause", json=None)
    assert forbidden.status_code == 403
    assert unauthenticated.status_code == 401


def test_only_commander_reaches_the_audit_trail(client, auth):
    assert client.get("/api/v1/audit", headers=auth("commander")).status_code == 200
    assert client.get("/api/v1/audit", headers=auth("officer")).status_code == 403
    assert client.get("/api/v1/audit", headers=auth("viewer")).status_code == 403


def test_every_response_carries_a_request_id(client, auth):
    response = client.get("/api/v1/fleet/summary", headers=auth("viewer"))
    assert response.headers.get("X-Request-ID")


def test_inbound_request_id_is_echoed(client, auth):
    given = "ABC123TRACKME"
    response = client.get("/api/v1/fleet/summary",
                          headers={**auth("viewer"), "X-Request-ID": given})
    assert response.headers["X-Request-ID"] == given


def test_error_envelope_shape(client):
    body = client.get("/api/v1/fleet/summary").json()
    assert set(body["error"]) == {"code", "message", "request_id", "detail"}


def test_openapi_is_served(client):
    spec = client.get("/openapi.json").json()
    assert len(spec["paths"]) >= 27
