from __future__ import annotations

from uuid import UUID

from psycopg import AsyncConnection

_known: set[UUID] = set()


async def ensure_user(conn: AsyncConnection, user_id: UUID) -> None:
    """Make sure profile and preferences rows exist.

    The sign-up trigger normally creates them; this covers accounts made before the trigger
    existed. It runs once per user per process.
    """
    if user_id in _known:
        return
    async with conn.transaction():
        await conn.execute("insert into profiles (id) values (%s) on conflict do nothing", (user_id,))
        await conn.execute("insert into user_preferences (user_id) values (%s) on conflict do nothing", (user_id,))
    _known.add(user_id)


async def get_profile(conn: AsyncConnection, user_id: UUID) -> dict:
    cur = await conn.execute("select id, display_name, avatar_url, created_at from profiles where id = %s", (user_id,))
    return await cur.fetchone()


PROFILE_FIELDS = {"display_name", "avatar_url"}


async def update_profile(conn: AsyncConnection, user_id: UUID, fields: dict) -> dict:
    if set(fields) - PROFILE_FIELDS:
        raise ValueError(f"cannot update {set(fields) - PROFILE_FIELDS}")
    if fields:
        sets = ", ".join(f"{k} = %({k})s" for k in fields)
        await conn.execute(f"update profiles set {sets} where id = %(user_id)s", {**fields, "user_id": user_id})
    return await get_profile(conn, user_id)


async def get_preferences(conn: AsyncConnection, user_id: UUID) -> dict:
    cur = await conn.execute(
        """select dietary_tags::text[] as dietary_tags, excluded_allergens::text[] as excluded_allergens,
                  nutrition_goal::text as nutrition_goal, daily_kcal_target, assume_staples
           from user_preferences where user_id = %s""", (user_id,))
    return await cur.fetchone()


async def put_preferences(conn: AsyncConnection, user_id: UUID, prefs: dict) -> dict:
    await conn.execute(
        """update user_preferences
           set dietary_tags = %(dietary_tags)s::dietary_tag[], excluded_allergens = %(excluded_allergens)s::allergen[],
               nutrition_goal = %(nutrition_goal)s::nutrition_goal, daily_kcal_target = %(daily_kcal_target)s,
               assume_staples = %(assume_staples)s
           where user_id = %(user_id)s""", {**prefs, "user_id": user_id})
    return await get_preferences(conn, user_id)
