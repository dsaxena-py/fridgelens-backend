"""MealLens API. Run with `uvicorn app.main:create_app --factory`."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from .config import Settings, get_settings
from .db import create_pool
from .repositories import scans
from .routers import ingredients, inventory, me, recipes
from .routers import scans as scans_router
from .storage import MemoryStorage, Storage, SupabaseStorage
from .vision.detector import Detector, load_detector

log = logging.getLogger("meallens")


def create_app(settings: Settings | None = None, storage: Storage | None = None,
               detector: Detector | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.pool = create_pool(settings.database_url, settings.db_pool_min, settings.db_pool_max)
        await app.state.pool.open(wait=True, timeout=30)
        app.state.storage = storage or (
            MemoryStorage() if settings.storage == "memory"
            else SupabaseStorage(settings.supabase_url, settings.supabase_service_role_key, settings.storage_bucket))
        app.state.detector = detector or load_detector(settings.detector, settings.model_path, settings.fake_detections)
        async with app.state.pool.connection() as conn:
            app.state.model_version_id = await scans.model_version_id(conn, app.state.detector.name)
        if app.state.model_version_id is None and app.state.detector.name != "fake":
            log.warning("model %s is not in model_versions; run its register_model.sql", app.state.detector.name)
        log.info("detector: %s (model_versions.id %s)", app.state.detector.name, app.state.model_version_id)
        yield
        await app.state.storage.aclose()
        await app.state.pool.close()

    app = FastAPI(title="MealLens API", version="0.1.0", lifespan=lifespan,
                  description="Fridge scans, virtual fridge and recipe recommendations for the MealLens app.")
    app.state.settings = settings
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_methods=["*"], allow_headers=["*"])
    for router in (me.router, ingredients.router, scans_router.router, inventory.router, recipes.router):
        app.include_router(router, prefix="/v1")

    @app.get("/health", tags=["health"])
    async def health(request: Request):
        async with request.app.state.pool.connection() as conn:
            await conn.execute("select 1")
        return {"status": "ok", "detector": request.app.state.detector.name,
                "model_version_id": request.app.state.model_version_id}

    return app
