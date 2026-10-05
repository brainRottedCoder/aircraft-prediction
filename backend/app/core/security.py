"""Password hashing and JWT issue/verify (HS256)."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from passlib.context import CryptContext

from .config import Settings
from .errors import AuthenticationError

_pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(raw: str, settings: Settings | None = None) -> str:
    """bcrypt, cost 12 (CryptContext default). `settings` is accepted for symmetry."""
    hashed: str = _pwd.hash(raw)
    return hashed


def verify_password(raw: str, hashed: str) -> bool:
    try:
        verified: bool = _pwd.verify(raw, hashed)
        return verified
    except ValueError:
        return False


def create_access_token(
    *, subject: str, role: str, full_name: str, settings: Settings
) -> tuple[str, int]:
    ttl = timedelta(minutes=settings.jwt_ttl_minutes)
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": subject,
        "role": role,
        "name": full_name,
        "iat": int(now.timestamp()),
        "exp": int((now + ttl).timestamp()),
    }
    token = jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    return token, int(ttl.total_seconds())


def decode_access_token(token: str, settings: Settings) -> dict[str, Any]:
    try:
        return jwt.decode(
            token, settings.jwt_secret, algorithms=[settings.jwt_algorithm]
        )
    except jwt.ExpiredSignatureError as exc:
        raise AuthenticationError("Session expired. Please sign in again.") from exc
    except jwt.PyJWTError as exc:
        raise AuthenticationError("Invalid authentication token.") from exc


def decode_ws_token(token: str | None, settings: Settings) -> dict[str, Any]:
    """WebSocket tokens arrive as a query parameter (docs/09 §3.1)."""
    if not token:
        raise AuthenticationError("Missing token.")
    return decode_access_token(token, settings)