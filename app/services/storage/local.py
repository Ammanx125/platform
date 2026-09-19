# app/services/storage/local.py
from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import BinaryIO

from app.core.config import settings


class LocalStorage:
    """
    Filesystem-backed Storage implementation.

    Keys are POSIX-style relative paths (e.g. 'tenant_id/dataset_id/uuid.csv').
    The implementation refuses keys that escape the configured root.
    """

    def __init__(self, root: str | None = None) -> None:
        self.root = Path(root or settings.upload_dir).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        # Reject anything that could escape the root.
        candidate = (self.root / key).resolve()
        if not str(candidate).startswith(str(self.root)):
            raise ValueError("storage key escapes root")
        return candidate

    async def put(self, *, key: str, content: bytes) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Write off the event loop
        await asyncio.to_thread(path.write_bytes, content)

    async def get(self, *, key: str) -> bytes:
        path = self._path(key)
        return await asyncio.to_thread(path.read_bytes)

    async def open(self, *, key: str) -> BinaryIO:
        path = self._path(key)
        # Note: returns a sync handle. Callers reading in chunks must
        # wrap the read in asyncio.to_thread. For 4a we accept this.
        return await asyncio.to_thread(open, path, "rb")

    async def delete(self, *, key: str) -> None:
        path = self._path(key)
        if path.exists():
            await asyncio.to_thread(os.remove, path)

    async def exists(self, *, key: str) -> bool:
        path = self._path(key)
        return await asyncio.to_thread(path.exists)


storage: LocalStorage = LocalStorage()