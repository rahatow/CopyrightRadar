"""Read-only client for graph.instagram.com (Instagram API with Instagram Login). SPEC section 6.

- Only GET. Any other method raises WriteNotAllowed.
- One request at a time, with a pause.
- The token travels as the documented `access_token` query parameter. It is stripped from
  `paging.next` links, the httpx logger is set to WARNING (at INFO it logs full URLs), and
  every error text is redacted.
- `paging.next` must stay on graph.instagram.com (no SSRF through a forged link).
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from urllib.parse import parse_qsl, urlsplit

import httpx

logging.getLogger("httpx").setLevel(logging.WARNING)

HOST = "graph.instagram.com"
BASE_URL = f"https://{HOST}"
API_VERSION = "v23.0"
MEDIA_FIELDS = "id,permalink,shortcode,media_type,media_product_type,timestamp,thumbnail_url,media_url,caption"
COPYRIGHT_FIELD = "copyright_check_information"
# Throttling codes. TODO: verify against Meta docs (SPEC 6.8).
RATE_LIMIT_CODES = {4, 17, 32, 613}
MAX_PAGES = 200


class WriteNotAllowed(RuntimeError):
    """Raised for any HTTP method other than GET."""


class MetaError(RuntimeError):
    def __init__(self, message: str, code: int | None = None, subcode: int | None = None, http_status: int | None = None):
        super().__init__(message)
        self.code, self.subcode, self.http_status = code, subcode, http_status

    @property
    def rate_limited(self) -> bool:
        return self.code in RATE_LIMIT_CODES


@dataclass
class MediaPage:
    items: list[dict] = field(default_factory=list)
    complete: bool = False
    error: str | None = None


class InstagramClient:
    def __init__(self, token: str, pause_s: float = 0.8, transport: httpx.BaseTransport | None = None):
        self._token = token
        self._pause_s = pause_s
        self._http = httpx.Client(base_url=BASE_URL, timeout=30, transport=transport)

    def _redact(self, text: str) -> str:
        return text.replace(self._token, "<TOKEN>") if self._token else text

    def request(self, method: str, path: str, params: dict | None = None) -> dict:
        if method.upper() != "GET":
            raise WriteNotAllowed(f"{method} is not allowed: this service is read-only")
        q = {k: v for k, v in (params or {}).items() if k != "access_token"}
        q["access_token"] = self._token
        url = path if path.startswith("/") else f"/{API_VERSION}/{path}"
        try:
            resp = self._http.get(url, params=q)
        except httpx.HTTPError as e:
            raise MetaError(self._redact(f"network error: {e}")) from None
        finally:
            if self._pause_s:
                time.sleep(self._pause_s)
        try:
            body = resp.json()
        except ValueError:
            raise MetaError(f"HTTP {resp.status_code}: not JSON", http_status=resp.status_code) from None
        if resp.status_code >= 400 or "error" in body:
            err = body.get("error", {}) if isinstance(body, dict) else {}
            raise MetaError(
                self._redact(str(err.get("message", f"HTTP {resp.status_code}"))),
                code=err.get("code"), subcode=err.get("error_subcode"), http_status=resp.status_code,
            )
        return body

    def get(self, path: str, params: dict | None = None) -> dict:
        return self.request("GET", path, params)

    def me(self) -> dict:
        return self.get("me", {"fields": "user_id,username,account_type"})

    def probe_copyright(self) -> bool:
        """True if the API returns copyright_check_information on at least one video. SPEC open question 1."""
        page = self.get("me/media", {"fields": f"id,media_type,{COPYRIGHT_FIELD}", "limit": 25})
        return any(COPYRIGHT_FIELD in item for item in page.get("data", []))

    def refresh_token(self) -> dict:
        return self.get("/refresh_access_token", {"grant_type": "ig_refresh_token"})

    def list_media(self, with_copyright: bool) -> MediaPage:
        """All media of the account. complete=False on any error or broken paging."""
        fields = MEDIA_FIELDS + (f",{COPYRIGHT_FIELD}" if with_copyright else "")
        result = MediaPage()
        path, params = "me/media", {"fields": fields, "limit": 50}
        for _ in range(MAX_PAGES):
            try:
                body = self.get(path, params)
            except MetaError as e:
                result.error = str(e)
                return result
            data = body.get("data")
            if not isinstance(data, list):
                result.error = "response has no data list"
                return result
            result.items.extend(data)
            nxt = (body.get("paging") or {}).get("next")
            if not nxt:
                result.complete = True
                return result
            parts = urlsplit(nxt)
            if parts.scheme != "https" or parts.netloc != HOST:
                result.error = "paging.next points to another host"
                return result
            path = parts.path
            params = {k: v for k, v in parse_qsl(parts.query) if k != "access_token"}
        result.error = "too many pages"
        return result
