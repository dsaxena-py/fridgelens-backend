"""Scan photo storage: Supabase Storage in production, in memory for tests and local runs."""

from __future__ import annotations

from typing import Protocol

import httpx


class Storage(Protocol):
    async def upload(self, path: str, data: bytes, content_type: str) -> None: ...
    async def signed_url(self, path: str, seconds: int) -> str | None: ...
    async def delete(self, path: str) -> None: ...
    async def aclose(self) -> None: ...


class SupabaseStorage:
    """Supabase Storage REST API, authorised with the service-role key (never sent to the app)."""

    def __init__(self, supabase_url: str, service_role_key: str, bucket: str):
        if not service_role_key:
            raise ValueError("SUPABASE_SERVICE_ROLE_KEY is required when STORAGE=supabase")
        self.base = f"{supabase_url.rstrip('/')}/storage/v1"
        self.bucket = bucket
        self.client = httpx.AsyncClient(timeout=30, headers={"Authorization": f"Bearer {service_role_key}",
                                                             "apikey": service_role_key})

    async def upload(self, path: str, data: bytes, content_type: str) -> None:
        r = await self.client.post(f"{self.base}/object/{self.bucket}/{path}", content=data,
                                   headers={"Content-Type": content_type, "x-upsert": "true"})
        r.raise_for_status()

    async def signed_url(self, path: str, seconds: int) -> str | None:
        r = await self.client.post(f"{self.base}/object/sign/{self.bucket}/{path}", json={"expiresIn": seconds})
        if r.status_code == 404:
            return None
        r.raise_for_status()
        signed = r.json().get("signedURL") or r.json().get("signedUrl")
        return f"{self.base}{signed}" if signed and signed.startswith("/") else signed

    async def delete(self, path: str) -> None:
        r = await self.client.request("DELETE", f"{self.base}/object/{self.bucket}", json={"prefixes": [path]})
        r.raise_for_status()

    async def aclose(self) -> None:
        await self.client.aclose()


class MemoryStorage:
    def __init__(self):
        self.objects: dict[str, tuple[bytes, str]] = {}

    async def upload(self, path: str, data: bytes, content_type: str) -> None:
        self.objects[path] = (data, content_type)

    async def signed_url(self, path: str, seconds: int) -> str | None:
        return f"memory://{path}?expires={seconds}" if path in self.objects else None

    async def delete(self, path: str) -> None:
        self.objects.pop(path, None)

    async def aclose(self) -> None:
        pass
