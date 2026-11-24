"""Read-only client for graph.instagram.com (Instagram API with Instagram Login).

Only GET is allowed. Requests run one at a time with a pause. See SPEC, section 6.
"""
from __future__ import annotations

import time

import httpx

BASE_URL = "https://graph.instagram.com"
API_VERSION = "v23.0"
MEDIA_FIELDS = "id,permalink,media_type,media_product_type,timestamp,thumbnail_url,media_url"
COPYRIGHT_FIELD = "copyright_check_information"
RATE_LIMIT_CODES = {4, 17, 32, 613}  # TODO: verify against Meta docs (SPEC 6.8)


class WriteNotAllowed(RuntimeError):
    """Raised for any HTTP method other than GET."""


class InstagramClient:
    def __init__(self, token: str, pause_s: float = 0.8, transport: httpx.BaseTransport | None = None):
        self._token = token
        self._pause_s = pause_s
        self._http = httpx.Client(base_url=BASE_URL, timeout=30, transport=transport)

    def request(self, method: str, path: str, params: dict | None = None) -> dict:
        if method.upper() != "GET":
            raise WriteNotAllowed(f"{method} is not allowed: this service is read-only")
        q = dict(params or {})
        q["access_token"] = self._token
        resp = self._http.get(f"/{API_VERSION}/{path.lstrip('/')}", params=q)
        time.sleep(self._pause_s)
        return resp.json()

    def get(self, path: str, params: dict | None = None) -> dict:
        return self.request("GET", path, params)

    # TODO v0.1: set logging.getLogger("httpx") to WARNING: at INFO it logs URLs with access_token.
    # TODO v0.1: list_media (paging, complete flag), get_copyright_info, probe_copyright_field,
    # refresh_token. Never log the token.
