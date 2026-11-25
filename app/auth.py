"""Single-password login, signed session cookie, same-origin check for writes. SPEC section 12."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time

from fastapi import HTTPException, Request

COOKIE = "cr_session"
SESSION_DAYS = 14


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
    return f"scrypt${base64.b64encode(salt).decode()}${base64.b64encode(digest).decode()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, salt_b64, digest_b64 = stored.split("$")
        salt, digest = base64.b64decode(salt_b64), base64.b64decode(digest_b64)
    except ValueError:
        return False
    test = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
    return hmac.compare_digest(test, digest)


def make_session(key: bytes, pw_version: int) -> str:
    body = base64.urlsafe_b64encode(json.dumps({"exp": int(time.time()) + SESSION_DAYS * 86400, "v": pw_version}).encode())
    sig = hmac.new(key, body, hashlib.sha256).hexdigest()
    return f"{body.decode()}.{sig}"


def check_session(key: bytes, token: str | None, pw_version: int) -> bool:
    if not token or "." not in token:
        return False
    body, sig = token.rsplit(".", 1)
    if not hmac.compare_digest(hmac.new(key, body.encode(), hashlib.sha256).hexdigest(), sig):
        return False
    try:
        data = json.loads(base64.urlsafe_b64decode(body))
    except ValueError:
        return False
    return data.get("exp", 0) > time.time() and data.get("v") == pw_version


def same_origin(request: Request) -> bool:
    """POST must come from this site: Origin (or Referer) host equals Host."""
    host = request.headers.get("x-forwarded-host") or request.headers.get("host", "")
    src = request.headers.get("origin") or request.headers.get("referer")
    if not src:
        return False
    try:
        from urllib.parse import urlsplit
        return urlsplit(src).netloc == host
    except ValueError:
        return False


def require_same_origin(request: Request) -> None:
    if not same_origin(request):
        raise HTTPException(403, detail={"error": "cross_origin_rejected"})
