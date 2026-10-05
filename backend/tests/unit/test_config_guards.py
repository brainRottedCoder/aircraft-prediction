"""Configuration guards — the settings that refuse to serve real traffic badly.

These are the checks between "someone deployed this" and "someone's fleet telemetry is
signed with a secret published in the repository".
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import DEV_JWT_SECRET, Settings

REAL_SECRET = "a-real-generated-secret-value-0123456789abcdef"


def test_production_refuses_the_committed_development_secret():
    """The whole point of the guard.

    `jwt_secret` defaults to a value in the source, so a deployment that forgets to set it
    would otherwise mint tokens anyone could forge from this repository.
    """
    with pytest.raises(ValidationError) as exc:
        Settings(environment="production", jwt_secret=DEV_JWT_SECRET)

    message = str(exc.value)
    assert "FDT_JWT_SECRET" in message
    assert "openssl rand -hex 32" in message, "the error must say how to fix it"


def test_production_accepts_a_generated_secret():
    settings = Settings(environment="production", jwt_secret=REAL_SECRET)
    assert settings.environment == "production"
    assert settings.jwt_secret == REAL_SECRET


def test_development_tolerates_the_placeholder():
    """Otherwise the guard would be unusable locally."""
    settings = Settings(environment="development", jwt_secret=DEV_JWT_SECRET)
    assert settings.environment == "development"


def test_production_allows_demo_mode():
    """`demo_mode` picks a telemetry source; it is not a security control.

    It used to be rejected under `production`. Since the replay engine only runs when
    `demo_mode` is true, that made every combination unusable: production produced a static
    console with no telemetry, and the only working setting was misreporting itself as
    `staging`. docker-compose.prod.yml and render.yaml now both use `production`.
    """
    settings = Settings(environment="production", jwt_secret=REAL_SECRET, demo_mode=True)
    assert settings.demo_mode is True
    assert settings.environment == "production"


def test_cors_origins_accepts_a_comma_separated_string():
    """Operators set this as one env var; it must not arrive as one long string."""
    settings = Settings(
        cors_origins="https://a.example, https://b.example ,",
    )
    assert settings.cors_origins == ["https://a.example", "https://b.example"]


# ── database URL ───────────────────────────────────────────────────────────────
#
# Render, Aiven, Neon, Supabase and Heroku all hand out a bare libpq URL. Handing that to
# SQLAlchemy 2 with only `psycopg` (v3) installed fails at startup with a `ModuleNotFoundError`
# or "Can't load plugin" that reads like a broken build, not a copied-and-pasted URL. The
# operator should be able to paste what the provider gave them.
@pytest.mark.parametrize(
    "given",
    [
        "postgres://u:p@host/db",
        "postgresql://u:p@host/db",
        "postgres://u:p@host:5432/db?sslmode=require",
    ],
)
def test_a_bare_provider_url_gets_the_pinned_driver(given):
    assert Settings(database_url=given).database_url.startswith("postgresql+psycopg://")


def test_the_explicit_pinned_url_is_left_alone():
    given = "postgresql+psycopg://u:p@host/db?sslmode=require"
    assert Settings(database_url=given).database_url == given


def test_another_driver_is_respected():
    """Only the bare libpq schemes are rewritten; a deliberate choice is not overridden."""
    given = "postgresql+asyncpg://u:p@host/db"
    assert Settings(database_url=given).database_url == given


def test_the_development_default_is_unchanged():
    assert Settings().database_url.startswith("postgresql+psycopg://")