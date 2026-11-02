from __future__ import annotations

from uuid import UUID

from psycopg import AsyncConnection

from . import ingredient_ref, ingredients, inventory


class ScanStateError(Exception):
    pass


async def create(conn: AsyncConnection, user_id: UUID, scan_id: UUID, image_path: str, width: int, height: int,
                 model_version_id: int | None) -> None:
    await conn.execute(
        """insert into scans (id, user_id, model_version_id, image_path, image_width, image_height)
           values (%s, %s, %s, %s, %s, %s)""",
        (scan_id, user_id, model_version_id, image_path, width, height))


async def save_detections(conn: AsyncConnection, scan_id: UUID, detections: list[dict], inference_ms: int) -> None:
    """Store detections (already mapped to ingredient ids) and mark the scan ready for review."""
    async with conn.transaction():
        if detections:
            async with conn.cursor() as cur:
                await cur.executemany(
                    """insert into detections (scan_id, class_index, ingredient_id, confidence, x_min, y_min, x_max, y_max)
                       values (%(scan_id)s, %(class_index)s, %(ingredient_id)s, %(confidence)s,
                               %(x_min)s, %(y_min)s, %(x_max)s, %(y_max)s)""",
                    [{**d, "scan_id": scan_id} for d in detections])
        await conn.execute("update scans set status = 'awaiting_review', inference_ms = %s where id = %s",
                           (inference_ms, scan_id))


async def fail(conn: AsyncConnection, scan_id: UUID, message: str) -> None:
    await conn.execute("update scans set status = 'failed', error_message = %s where id = %s", (message[:500], scan_id))


async def get(conn: AsyncConnection, user_id: UUID, scan_id: UUID) -> dict | None:
    cur = await conn.execute(
        f"""select s.id, s.status::text as status, s.image_path, s.image_width, s.image_height, s.inference_ms,
                   s.error_message, s.created_at, s.confirmed_at, mv.name as model,
                   coalesce((select jsonb_agg(jsonb_build_object(
                                 'id', d.id, 'ingredient', {ingredient_ref('i')}, 'confidence', d.confidence,
                                 'box', jsonb_build_object('x_min', d.x_min, 'y_min', d.y_min,
                                                           'x_max', d.x_max, 'y_max', d.y_max),
                                 'review', d.review,
                                 'corrected_ingredient', case when c.id is null then null else {ingredient_ref('c')} end)
                               order by d.confidence desc)
                             from detections d
                             join ingredients i on i.id = d.ingredient_id
                             left join ingredients c on c.id = d.corrected_ingredient_id
                             where d.scan_id = s.id), '[]'::jsonb) as detections
            from scans s left join model_versions mv on mv.id = s.model_version_id
            where s.user_id = %s and s.id = %s""", (user_id, scan_id))
    return await cur.fetchone()


async def list_recent(conn: AsyncConnection, user_id: UUID, limit: int) -> list[dict]:
    cur = await conn.execute(
        """select s.id, s.status::text as status, s.created_at, s.confirmed_at,
                  (select count(*) from detections d where d.scan_id = s.id) as detection_count
           from scans s where s.user_id = %s order by s.created_at desc limit %s""", (user_id, limit))
    return await cur.fetchall()


async def confirm(conn: AsyncConnection, user_id: UUID, scan_id: UUID, decisions: list[dict],
                  additions: list[dict]) -> list[UUID]:
    """Apply the user's review and add the confirmed ingredients to the fridge, all or nothing."""
    async with conn.transaction():
        cur = await conn.execute("select status::text as status from scans where user_id = %s and id = %s for update",
                                 (user_id, scan_id))
        scan = await cur.fetchone()
        if scan is None:
            raise LookupError("scan not found")
        if scan["status"] != "awaiting_review":
            raise ScanStateError(f"scan is {scan['status']}, not awaiting review")

        cur = await conn.execute("select id, ingredient_id from detections where scan_id = %s", (scan_id,))
        detected = {r["id"]: r["ingredient_id"] for r in await cur.fetchall()}
        decided = {d["detection_id"] for d in decisions}
        if decided != set(detected) or len(decided) != len(decisions):
            missing, unknown = set(detected) - decided, decided - set(detected)
            raise ValueError(f"need exactly one decision per detection; missing {sorted(missing)}, "
                             f"unknown {sorted(unknown)}")

        wanted = {a["ingredient_id"] for a in additions} | {d["ingredient_id"] for d in decisions
                                                              if d["action"] == "relabel" and d.get("ingredient_id")}
        unknown_ids = wanted - await ingredients.existing_ids(conn, wanted)
        if unknown_ids:
            raise ValueError(f"unknown ingredient_id {sorted(unknown_ids)}")

        confirmed: list[int] = []
        for d in decisions:
            if d["action"] == "relabel":
                if d.get("ingredient_id") is None:
                    raise ValueError(f"detection {d['detection_id']}: relabel needs ingredient_id")
                review, corrected = "relabeled", d["ingredient_id"]
                confirmed.append(corrected)
            elif d["action"] == "accept":
                review, corrected = "accepted", None
                confirmed.append(detected[d["detection_id"]])
            else:
                review, corrected = "rejected", None
            await conn.execute("update detections set review = %s, corrected_ingredient_id = %s where id = %s",
                               (review, corrected, d["detection_id"]))

        item_ids = []
        for ingredient_id in dict.fromkeys(confirmed):  # one per ingredient, first-seen order
            item_ids.append(await inventory.upsert(conn, user_id, ingredient_id, "scan", source_scan_id=scan_id))
        for a in additions:
            item_ids.append(await inventory.upsert(conn, user_id, a["ingredient_id"], "manual",
                                                   a.get("quantity"), a.get("unit"), source_scan_id=scan_id))
        await conn.execute("update scans set status = 'confirmed', confirmed_at = now() where id = %s", (scan_id,))
    return item_ids


async def model_version_id(conn: AsyncConnection, name: str) -> int | None:
    cur = await conn.execute("select id from model_versions where name = %s", (name,))
    row = await cur.fetchone()
    return row["id"] if row else None
