from __future__ import annotations

from fastapi import APIRouter

from ..deps import Conn, User
from ..repositories import users
from ..schemas import Preferences, Profile, ProfileUpdate

router = APIRouter(prefix="/me", tags=["account"])


@router.get("", response_model=Profile)
async def get_me(user: User, conn: Conn):
    return {**await users.get_profile(conn, user.id), "email": user.email}


@router.patch("", response_model=Profile)
async def update_me(body: ProfileUpdate, user: User, conn: Conn):
    profile = await users.update_profile(conn, user.id, body.model_dump(exclude_unset=True))
    return {**profile, "email": user.email}


@router.get("/preferences", response_model=Preferences)
async def get_preferences(user: User, conn: Conn):
    return await users.get_preferences(conn, user.id)


@router.put("/preferences", response_model=Preferences)
async def put_preferences(body: Preferences, user: User, conn: Conn):
    return await users.put_preferences(conn, user.id, body.model_dump())
