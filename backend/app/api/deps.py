"""FastAPI dependencies — db session, current user, role guards (docs/02 §4)."""
from __future__ import annotations

from collections.abc import Callable
from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from ..core.config import Settings, get_settings
from ..core.errors import AuthenticationError, ForbiddenError
from ..core.security import decode_access_token
from ..db.session import get_db
from ..models.auth import User, UserRole
from ..repositories import fleet_repo as repo

bearer = HTTPBearer(auto_error=False)

# Viewer is read-only; mutation requires officer or commander.
CAN_MUTATE = {UserRole.maintenance_officer, UserRole.commander}
IS_COMMANDER = {UserRole.commander}


DbSession = Annotated[Session, Depends(get_db)]
AppSettings = Annotated[Settings, Depends(get_settings)]


def current_user(
    db: DbSession,
    settings: AppSettings,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)] = None,
) -> User:
    if credentials is None or not credentials.credentials:
        raise AuthenticationError("Missing bearer token.")
    claims = decode_access_token(credentials.credentials, settings)
    user = repo.get_user(db, int(claims["sub"]))
    if user is None:
        raise AuthenticationError("Unknown user.")
    return user


CurrentUser = Annotated[User, Depends(current_user)]


def require_roles(allowed: set[UserRole]) -> Callable[..., User]:
    """Router-level RBAC — viewer is denied structurally, not by per-handler ifs."""

    def guard(user: CurrentUser) -> User:
        if user.role not in allowed:
            raise ForbiddenError(
                f"Role {user.role.value} may not perform this action."
            )
        return user

    return guard


CanMutate = Annotated[User, Depends(require_roles(CAN_MUTATE))]
CommanderOnly = Annotated[User, Depends(require_roles(IS_COMMANDER))]