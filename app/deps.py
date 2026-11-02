"""Shared FastAPI dependencies."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends
from psycopg import AsyncConnection

from .auth import CurrentUser, current_user
from .db import get_conn
from .repositories import users

Conn = Annotated[AsyncConnection, Depends(get_conn)]


async def signed_in_user(conn: Conn, user: CurrentUser = Depends(current_user)) -> CurrentUser:
    """The caller, with their profile and preferences rows guaranteed to exist."""
    await users.ensure_user(conn, user.id)
    return user


User = Annotated[CurrentUser, Depends(signed_in_user)]
