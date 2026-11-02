from __future__ import annotations

import logging
import time
import uuid
from uuid import UUID

from fastapi import APIRouter, File, HTTPException, Query, Request, UploadFile, status
from fastapi.concurrency import run_in_threadpool

from ..deps import Conn, User
from ..images import InvalidImage, prepare
from ..repositories import ingredients, inventory, scans
from ..schemas import ConfirmScan, InventoryItem, ScanOut, ScanSummary

log = logging.getLogger(__name__)
router = APIRouter(prefix="/scans", tags=["scans"])


async def _scan_out(request: Request, row: dict) -> dict:
    settings = request.app.state.settings
    url = await request.app.state.storage.signed_url(row["image_path"], settings.signed_url_seconds)
    detections = [{**d, "needs_check": d["confidence"] < settings.review_confidence} for d in row["detections"]]
    return {**row, "image_url": url, "detections": detections}


@router.post("", response_model=ScanOut, status_code=status.HTTP_201_CREATED)
async def create_scan(request: Request, user: User, conn: Conn, image: UploadFile = File(...)):
    """Upload a fridge photo; returns the detected ingredients for the user to review."""
    settings = request.app.state.settings
    data = await image.read(settings.max_upload_mb * 1024 * 1024 + 1)
    if len(data) > settings.max_upload_mb * 1024 * 1024:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, f"image is over {settings.max_upload_mb} MB")
    try:
        prepared = await run_in_threadpool(prepare, data, settings.max_image_side)
    except InvalidImage as e:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, str(e)) from e

    scan_id = uuid.uuid4()
    path = f"{user.id}/{scan_id}.jpg"
    state = request.app.state
    try:
        await state.storage.upload(path, prepared.jpeg, "image/jpeg")
    except Exception as e:
        log.exception("storing scan photo %s failed", path)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "could not store the photo; try again") from e
    await scans.create(conn, user.id, scan_id, path, prepared.width, prepared.height, state.model_version_id)

    try:
        t0 = time.perf_counter()
        found = await run_in_threadpool(state.detector.detect, prepared.image)
        inference_ms = round((time.perf_counter() - t0) * 1000)
        slug_ids = await ingredients.ids_by_slug(conn, {d.label for d in found})
        rows = [{"class_index": d.class_index, "ingredient_id": slug_ids[d.label], "confidence": d.confidence,
                 "x_min": d.x_min, "y_min": d.y_min, "x_max": d.x_max, "y_max": d.y_max}
                for d in found if d.label in slug_ids]
        await scans.save_detections(conn, scan_id, rows, inference_ms)
    except Exception as e:
        log.exception("detection failed for scan %s", scan_id)
        await scans.fail(conn, scan_id, f"detection failed: {e}")
    return await _scan_out(request, await scans.get(conn, user.id, scan_id))


@router.get("", response_model=list[ScanSummary])
async def list_scans(user: User, conn: Conn, limit: int = Query(default=20, ge=1, le=100)):
    return await scans.list_recent(conn, user.id, limit)


@router.get("/{scan_id}", response_model=ScanOut)
async def get_scan(scan_id: UUID, request: Request, user: User, conn: Conn):
    row = await scans.get(conn, user.id, scan_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "scan not found")
    return await _scan_out(request, row)


@router.post("/{scan_id}/confirm", response_model=list[InventoryItem])
async def confirm_scan(scan_id: UUID, body: ConfirmScan, user: User, conn: Conn):
    """Accept, reject or relabel every detection and add missed items. Returns the whole fridge."""
    try:
        await scans.confirm(conn, user.id, scan_id, [d.model_dump() for d in body.decisions],
                            [a.model_dump() for a in body.additions])
    except LookupError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e)) from e
    except scans.ScanStateError as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from e
    except ValueError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(e)) from e
    return await inventory.list_items(conn, user.id)
