"""Encrypted secrets (Fernet). SPEC section 12.

Key: APP_SECRET_KEY env var, or data/secret.key (created on first start, mode 600).
"""
from __future__ import annotations

import base64
import hashlib
import os
from pathlib import Path

from cryptography.fernet import Fernet

from app.storage.db import DB, now


def load_master_key(data_dir: Path) -> bytes:
    env = os.environ.get("APP_SECRET_KEY", "").strip()
    if env:
        return env.encode()
    path = data_dir / "secret.key"
    if not path.exists():
        data_dir.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(base64.urlsafe_b64encode(os.urandom(32)))
    return path.read_bytes().strip()


class SecretStore:
    def __init__(self, db: DB, master_key: bytes):
        self.db = db
        fernet_key = base64.urlsafe_b64encode(hashlib.sha256(b"secrets:" + master_key).digest())
        self._f = Fernet(fernet_key)
        self.signing_key = hashlib.sha256(b"session:" + master_key).digest()

    def put(self, name: str, value: str) -> None:
        self.db.x(
            "INSERT INTO secret(name,ciphertext,created_at) VALUES(?,?,?) "
            "ON CONFLICT(name) DO UPDATE SET ciphertext=excluded.ciphertext, created_at=excluded.created_at",
            (name, self._f.encrypt(value.encode()), now()),
        )

    def get(self, name: str) -> str | None:
        row = self.db.one("SELECT ciphertext FROM secret WHERE name=?", (name,))
        return self._f.decrypt(row["ciphertext"]).decode() if row else None

    def created_at(self, name: str) -> str | None:
        row = self.db.one("SELECT created_at FROM secret WHERE name=?", (name,))
        return row["created_at"] if row else None
