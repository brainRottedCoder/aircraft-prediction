"""Authentication endpoints."""
from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, status

from ...core.errors import AuthenticationError
from ...core.security import create_access_token, verify_password
from ...repositories import fleet_repo as repo
from ...schemas.ops import LoginRequest, LoginResponse, MeResponse
from ..deps import AppSettings, CurrentUser, DbSession

router = APIRouter(tags=["auth"], prefix="/api/v1/auth")

GENERIC_ERROR = "Invalid username or password"


@router.post("/login", response_model=LoginResponse, status_code=status.HTTP_200_OK)
def login(db: DbSession, settings: AppSettings, payload: LoginRequest):
    user = repo.get_user_by_username(db, payload.username)
    # Identical failure for unknown user and wrong password — no enumeration.
    if user is None or not verify_password(payload.password, user.password_hash):
        raise AuthenticationError(GENERIC_ERROR)

    token, expires_in = create_access_token(
        subject=str(user.id), role=user.role.value, full_name=user.full_name,
        settings=settings,
    )
    user.last_login_at = datetime.now(UTC)
    db.commit()
    return {
        "access_token": token, "token_type": "bearer", "expires_in": expires_in,
        "user": {"id": user.id, "username": user.username,
                 "full_name": user.full_name, "role": user.role.value},
    }


@router.get("/me", response_model=MeResponse)
def me(user: CurrentUser):
    return {
        "id": user.id, "username": user.username, "full_name": user.full_name,
        "role": user.role.value,
        "permissions": {
            "can_mutate": user.role.value != "viewer",
            "can_manage_agencies": user.role.value == "commander",
        },
    }