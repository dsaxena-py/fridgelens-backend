"""API tests run against a real Postgres loaded with the migrations and seed data.

Set PGHOST / PGPORT / PGUSER (and PGPASSWORD) for a local Postgres 15+ where the test user may
create databases. Tests are skipped when no server is reachable.
"""

from __future__ import annotations

import io
import os
import subprocess
import time
import uuid
from pathlib import Path

import jwt
import psycopg
import pytest
from httpx import ASGITransport, AsyncClient
from PIL import Image

from app.config import Settings
from app.main import create_app
from app.storage import MemoryStorage
from app.vision.detector import FakeDetector

ROOT = Path(__file__).resolve().parent.parent
TEST_DB = "meallens_api_test"
SECRET = "test-jwt-secret-that-is-long-enough-for-hs256"


def _psql(sql_or_file: str, db: str, file: bool = False, check: bool = True) -> None:
    args = ["psql", "-q", "-d", db] + (["-v", "ON_ERROR_STOP=1"] if check else [])
    args += ["-f", sql_or_file] if file else ["-c", sql_or_file]
    subprocess.run(args, check=check, capture_output=True, text=True)


@pytest.fixture(scope="session")
def database_url() -> str:
    try:
        subprocess.run(["psql", "-d", "postgres", "-Atc", "select 1"], check=True, capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        pytest.skip("no local Postgres; set PGHOST/PGPORT/PGUSER")
    _psql(f"drop database if exists {TEST_DB} with (force)", "postgres")
    _psql(f"create database {TEST_DB}", "postgres")
    _psql(str(ROOT / "supabase/tests/supabase_stubs.sql"), TEST_DB, file=True, check=False)  # roles may exist
    for f in sorted((ROOT / "supabase/migrations").glob("*.sql")) + sorted((ROOT / "supabase/seed").glob("*.sql")):
        _psql(str(f), TEST_DB, file=True)
    host = os.environ.get("PGHOST", "localhost")
    parts = [f"dbname={TEST_DB}", f"user={os.environ.get('PGUSER', 'postgres')}"]
    parts.append(f"host={host}")
    if os.environ.get("PGPORT"):
        parts.append(f"port={os.environ['PGPORT']}")
    if os.environ.get("PGPASSWORD"):
        parts.append(f"password={os.environ['PGPASSWORD']}")
    return " ".join(parts)


@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"


@pytest.fixture
def settings(database_url) -> Settings:
    return Settings(database_url=database_url, supabase_url="http://supabase.invalid", supabase_jwt_secret=SECRET,
                    storage="memory", detector="fake", max_upload_mb=2, _env_file=None)


@pytest.fixture
def storage() -> MemoryStorage:
    return MemoryStorage()


@pytest.fixture
def detector() -> FakeDetector:
    return FakeDetector("egg:0.92,tomato:0.88,cheese:0.81,onion:0.42")


@pytest.fixture
async def client(settings, storage, detector):
    app = create_app(settings, storage=storage, detector=detector)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            c.app = app
            yield c


def make_user(database_url: str, email: str | None = None) -> tuple[uuid.UUID, dict]:
    """Create an auth user (the sign-up trigger adds profile + preferences) and a bearer header."""
    uid = uuid.uuid4()
    with psycopg.connect(database_url, autocommit=True) as conn:
        conn.execute("insert into auth.users (id, raw_user_meta_data) values (%s, %s)",
                     (uid, psycopg.types.json.Jsonb({"display_name": "Tester"})))
    return uid, auth_header(uid, email or f"{uid.hex[:8]}@example.com")


def auth_header(uid: uuid.UUID, email: str = "t@example.com", exp_in: int = 3600, aud: str = "authenticated",
                secret: str = SECRET) -> dict:
    token = jwt.encode({"sub": str(uid), "email": email, "aud": aud, "role": "authenticated",
                        "exp": int(time.time()) + exp_in}, secret, algorithm="HS256")
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def user(database_url):
    return make_user(database_url)


@pytest.fixture
def other_user(database_url):
    return make_user(database_url)


def jpeg(size=(320, 240), orientation: int | None = None) -> bytes:
    img = Image.new("RGB", size, (180, 60, 60))
    buf = io.BytesIO()
    exif = Image.Exif()
    if orientation:
        exif[0x0112] = orientation
    img.save(buf, format="JPEG", exif=exif.tobytes())
    return buf.getvalue()


def ingredient_id(database_url: str, slug: str) -> int:
    with psycopg.connect(database_url) as conn:
        return conn.execute("select id from ingredients where slug = %s", (slug,)).fetchone()[0]
