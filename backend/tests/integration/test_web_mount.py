"""Serving the Vite build from the API process — and not breaking the API by doing so.

`frontend/src/lib/api.js` derives both its REST base and its WebSocket URL from
`window.location.origin`, so on a deployment with no nginx in front the bundle has to be
served by this process. Mounting `StaticFiles` at `/` is the one change in this deploy
that can silently break every existing endpoint, because `/` matches everything: if the
mount is registered before the routers, `StaticFiles` answers `/api/v1/...`,
`/healthz` and `/ws/fleet` with a 404 for a missing file and the app looks merely
broken rather than misconfigured.

So the tests below pin the ordering, not just the presence of the mount.
"""
from __future__ import annotations

import pytest
from fastapi.routing import APIWebSocketRoute, Mount
from fastapi.staticfiles import StaticFiles
from fastapi.testclient import TestClient
from starlette.routing import Route  # APIRoute subclasses this

from app.core.config import Settings
from app.main import _mount_frontend, create_app


def _flatten(routes) -> list:
    """Every route in the tree, however it is nested.

    This FastAPI version represents `include_router` as an `_IncludedRouter` wrapper
    holding the child under `original_router`, rather than splicing its routes into
    `app.routes`. So a flat scan of `app.routes` finds only `/docs`, `/redoc` and
    friends — not `/healthz`, not `/ws/fleet`. Anything that inspects the route table
    has to recurse through both shapes.
    """
    out = []
    for route in routes:
        out.append(route)
        out.extend(_flatten(getattr(route, "routes", []) or []))
        original = getattr(route, "original_router", None)
        if original is not None:
            out.extend(_flatten(original.routes))
    return out


def _static_mounts(routes) -> list[str]:
    """Paths of every mounted StaticFiles, at any depth.

    `app.mount()` wraps the target in a `Mount`, so the `StaticFiles` instance is never
    itself a route — checking `isinstance(route, StaticFiles)` finds nothing and the test
    silently asserts against an always-empty list.
    """
    return [
        route.path for route in routes
        if isinstance(route, Mount) and isinstance(route.app, StaticFiles)
    ]


@pytest.fixture
def dist(tmp_path):
    """A stand-in for `frontend/dist` with the two things that matter: a shell and a
    hashed asset under /models, which is where the 3D binaries live."""
    root = tmp_path / "dist"
    (root / "models").mkdir(parents=True)
    (root / "index.html").write_text("<!doctype html><div id=root>shell</div>")
    (root / "assets").mkdir()
    (root / "assets" / "index-a1b2c3.js").write_text("console.log(1)")
    (root / "models" / "rafale.glb").write_bytes(b"\x00glTF-fake")
    return root


@pytest.fixture
def web_client(dist, database, monkeypatch):
    """`create_app()` with the bundle present — the single-service deployment shape.

    `database` migrates and seeds; without it the login assertion below would hit a
    schema that does not exist.
    """
    monkeypatch.setattr(
        "app.main.get_settings", lambda: Settings(web_dist=dist, demo_mode=False)
    )
    with TestClient(create_app()) as client:
        yield client


@pytest.fixture
def bare_client(tmp_path, database, monkeypatch):
    """The identical app with no bundle — the Compose shape, and the control case."""
    monkeypatch.setattr(
        "app.main.get_settings",
        lambda: Settings(web_dist=tmp_path / "absent", demo_mode=False),
    )
    with TestClient(create_app()) as client:
        yield client


# ── the bundle is served ───────────────────────────────────────────────────────
def test_root_serves_the_shell(web_client):
    response = web_client.get("/")
    assert response.status_code == 200
    assert "id=root" in response.text


def test_hashed_assets_are_served(web_client):
    response = web_client.get("/assets/index-a1b2c3.js")
    assert response.status_code == 200
    assert response.text == "console.log(1)"


def test_3d_assets_are_served(web_client):
    """The digital twin's binaries. A mount that dropped /models would 404 the scene."""
    response = web_client.get("/models/rafale.glb")
    assert response.status_code == 200
    assert response.content.startswith(b"\x00glTF")


# ── the API still wins ─────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "path",
    [
        "/healthz",          # Render's healthCheckPath
        "/readyz",
        "/openapi.json",     # registered in FastAPI.__init__, before any mount
        "/docs",
        "/api/v1/auth/login",
        "/api/v1/fleet",
    ],
)
def test_api_routes_are_not_shadowed(web_client, bare_client, path):
    """The mount must not change how any API path answers.

    Compared against the *same app without the bundle* rather than against expected
    status codes: this framework answers a GET to a POST-only route with 404 rather
    than 405, so hardcoding "not 404" would have been asserting an accident. Equality
    with the unmounted app is the actual invariant — whatever the route does, the mount
    does not change it.
    """
    with_bundle = web_client.get(path)
    without_bundle = bare_client.get(path)

    assert with_bundle.status_code == without_bundle.status_code, (
        f"{path}: {with_bundle.status_code} with the web mount vs "
        f"{without_bundle.status_code} without it"
    )
    # And it is not the static handler's 404 either.
    assert with_bundle.status_code != 404 or without_bundle.status_code == 404


def test_login_endpoint_still_works_end_to_end(web_client):
    response = web_client.post(
        "/api/v1/auth/login",
        json={"username": "commander", "password": "commander123"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["token_type"] == "bearer"


def test_websocket_route_is_still_registered(web_client):
    """`/ws/fleet` is a websocket route and cannot be reached by TestClient.get.

    Assert on the router instead: the point is that the web build did not come to own
    `/ws`, and a WebSocketRoute still in the tree is the proof.
    """
    paths = {
        route.path for route in _flatten(web_client.app.routes)
        if isinstance(route, APIWebSocketRoute)
    }
    assert "/ws/fleet" in paths


def test_api_routes_reachable_in_the_route_tree(web_client):
    """Every API path survives in the route table alongside the web build."""
    paths = {
        route.path for route in _flatten(web_client.app.routes) if isinstance(route, Route)
    }
    for expected in ("/healthz", "/readyz", "/api/v1/auth/login", "/openapi.json"):
        assert expected in paths, f"{expected} is missing from the route tree"


def test_web_mounts_never_claim_a_bare_root(web_client):
    """The structural guarantee: no StaticFiles is mounted at "/".

    A mount at "/" matches every path, and because Starlette takes the first *complete*
    match, an API request whose path matches but whose method does not falls through
    into it and is answered 404 for a missing file instead of 405 for a bad method. The
    build is mounted only where Vite actually emits it.
    """
    mounts = _static_mounts(web_client.app.routes)
    assert mounts == ["/assets", "/models"], (
        f"unexpected static mounts: {mounts}; a mount at '/' shadows the API"
    )
    assert "" not in mounts and "/" not in mounts


def test_method_mismatch_still_reports_405(web_client, bare_client):
    """Regression: the web build must not turn a method error into a 404.

    This is the exact failure a `StaticFiles` mount at "/" causes — a GET to the
    POST-only login partially matches the router, falls through to the mount, and comes
    back as "Not Found". Asserted against the unmounted app so it cannot pass by
    coincidence if the framework's own 405 behaviour ever changes.
    """
    response = web_client.get("/api/v1/auth/login")
    assert response.status_code == bare_client.get("/api/v1/auth/login").status_code
    assert response.status_code == 405, (
        f"expected 405 Method Not Allowed, got {response.status_code}"
    )


def test_index_is_not_cacheable(web_client):
    """A cached index.html points clients at asset hashes a deploy has removed."""
    response = web_client.get("/")
    assert "no-store" in response.headers.get("Cache-Control", "")


def test_shell_route_follows_the_api_routers(web_client):
    """`/` is an ordinary route, so it must be registered after the API routers.

    It does not have to be the very last route: the `/assets` and `/models` mounts
    cannot collide with anything, because the API defines no such paths.
    """
    routes = list(web_client.app.routes)
    shell = next(
        (i for i, r in enumerate(routes)
         if isinstance(r, Route) and r.path == "/" and r.name == "web_index"),
        None,
    )
    assert shell is not None, "the web shell route is missing"

    last_router = max(
        (i for i, r in enumerate(routes) if type(r).__name__ == "_IncludedRouter"),
        default=-1,
    )
    assert shell > last_router, (
        f"the web shell is at {shell}, at or before the API routers (last at "
        f"{last_router})"
    )


# ── absent bundle is not an error ──────────────────────────────────────────────
def test_missing_bundle_leaves_the_api_untouched(tmp_path):
    """The Compose path has no bundle in the image; that must stay a clean no-op."""
    import app.main as main_module

    settings = Settings(web_dist=tmp_path / "does-not-exist", demo_mode=False)
    original = main_module.get_settings
    main_module.get_settings = lambda: settings
    try:
        app = main_module.create_app()
    finally:
        main_module.get_settings = original

    assert _static_mounts(app.routes) == []
    with TestClient(app) as client:
        assert client.get("/healthz").status_code == 200


def test_empty_bundle_directory_is_not_mounted(tmp_path):
    """An empty dist/ is what an interrupted `npm run build` looks like.

    Mounting it would 404 every path, including the platform's health check.
    """
    from fastapi import FastAPI

    empty = tmp_path / "empty"
    empty.mkdir()
    app = FastAPI()
    _mount_frontend(app, Settings(web_dist=empty))
    assert _static_mounts(app.routes) == []


def test_healthz_reports_retention_state(web_client):
    """The pruner is how a small Postgres fills up silently; /healthz must show it."""
    body = web_client.get("/healthz").json()
    assert body["status"] in {"ok", "degraded"}
    retention = body["retention"]
    assert {"enabled", "running", "passes", "retention_hours"} <= set(retention)