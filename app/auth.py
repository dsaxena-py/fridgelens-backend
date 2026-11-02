"""Verify Supabase access tokens.

The Flutter app signs in with Supabase Auth and sends the session's access token as
`Authorization: Bearer <token>`. We verify its signature (JWKS, or the legacy HS256 secret),
expiry and audience, and use its `sub` claim as the user id.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from uuid import UUID

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .config import Settings, app_settings

bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class CurrentUser:
    id: UUID
    email: str | None


@lru_cache
def _jwks_client(supabase_url: str) -> jwt.PyJWKClient:
    return jwt.PyJWKClient(f"{supabase_url.rstrip('/')}/auth/v1/.well-known/jwks.json", cache_keys=True)


def decode_token(token: str, settings: Settings) -> dict:
    header = jwt.get_unverified_header(token)
    options = {"require": ["exp", "sub"]}
    if header.get("alg") == "HS256":
        if not settings.supabase_jwt_secret:
            raise jwt.InvalidTokenError("HS256 token but SUPABASE_JWT_SECRET is not set")
        return jwt.decode(token, settings.supabase_jwt_secret, algorithms=["HS256"],
                          audience=settings.jwt_audience, options=options)
    key = _jwks_client(settings.supabase_url).get_signing_key_from_jwt(token)
    return jwt.decode(token, key.key, algorithms=["ES256", "RS256"], audience=settings.jwt_audience, options=options)


def current_user(credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
                 settings: Settings = Depends(app_settings)) -> CurrentUser:
    if credentials is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing bearer token",
                            headers={"WWW-Authenticate": "Bearer"})
    try:
        claims = decode_token(credentials.credentials, settings)
        return CurrentUser(id=UUID(claims["sub"]), email=claims.get("email"))
    except (jwt.PyJWTError, ValueError) as e:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"invalid token: {e}",
                            headers={"WWW-Authenticate": "Bearer"}) from e
