"""ACRCloud Music Recognition API (Audio Fingerprinting). SPEC section 7.

Protocol (docs.acrcloud.com, identification API): POST https://<host>/v1/identify, multipart,
signature = base64(HMAC-SHA1(secret, "POST\\n/v1/identify\\n<key>\\naudio\\n1\\n<timestamp>")).
Status codes: 0 success, 1001 no result, 2004 cannot generate fingerprint,
3001 wrong key, 3003/3015 limits, 3014 invalid signature.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import time
from dataclasses import dataclass

import httpx

from app.risk.rules import Recognition

URI = "/v1/identify"


class ACRCloudError(RuntimeError):
    def __init__(self, message: str, code: int | None = None):
        super().__init__(message)
        self.code = code


@dataclass
class ACRResult:
    recognition: Recognition
    isrc: str | None
    raw: dict
    no_fingerprint: bool = False


def sign(secret: str, access_key: str, timestamp: str) -> str:
    to_sign = "\n".join(["POST", URI, access_key, "audio", "1", timestamp])
    return base64.b64encode(hmac.new(secret.encode(), to_sign.encode(), hashlib.sha1).digest()).decode()


def parse(body: dict) -> ACRResult:
    code = (body.get("status") or {}).get("code")
    if code == 0:
        music = ((body.get("metadata") or {}).get("music") or [{}])[0]
        artists = ", ".join(a.get("name", "") for a in music.get("artists") or [] if isinstance(a, dict)) or None
        score = music.get("score")
        rec = Recognition(status="match", title=music.get("title"), artists=artists, label=music.get("label"),
                          score=int(float(score)) if score is not None else None)
        return ACRResult(rec, (music.get("external_ids") or {}).get("isrc"), body)
    if code == 1001:
        return ACRResult(Recognition(status="no_match"), None, body)
    if code == 2004:
        return ACRResult(Recognition(status="error"), None, body, no_fingerprint=True)
    msg = (body.get("status") or {}).get("msg", "unknown error")
    raise ACRCloudError(f"ACRCloud {code}: {msg}", code)


class ACRCloud:
    def __init__(self, host: str, access_key: str, secret: str, transport: httpx.BaseTransport | None = None):
        host = host.strip().removeprefix("https://").removeprefix("http://").strip("/")
        if not host.endswith(".acrcloud.com"):
            raise ValueError("host must be <region>.acrcloud.com")
        self.host, self.key, self.secret = host, access_key.strip(), secret.strip()
        self._http = httpx.Client(timeout=30, transport=transport)

    def identify(self, sample: bytes) -> ACRResult:
        ts = str(int(time.time()))
        data = {"access_key": self.key, "sample_bytes": str(len(sample)), "timestamp": ts,
                "signature": sign(self.secret, self.key, ts), "data_type": "audio", "signature_version": "1"}
        try:
            resp = self._http.post(f"https://{self.host}{URI}", data=data,
                                   files={"sample": ("sample.mp3", sample, "audio/mpeg")})
            body = resp.json()
        except (httpx.HTTPError, ValueError) as e:
            raise ACRCloudError(f"ACRCloud request failed: {type(e).__name__}") from None
        return parse(body)
