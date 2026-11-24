import httpx
import pytest

from app.instagram.client import InstagramClient, WriteNotAllowed


def test_only_get_is_allowed():
    seen = []
    transport = httpx.MockTransport(lambda req: seen.append(req.method) or httpx.Response(200, json={"id": "1"}))
    c = InstagramClient("TEST", pause_s=0, transport=transport)
    assert c.get("me") == {"id": "1"}
    for method in ("POST", "DELETE", "PUT"):
        with pytest.raises(WriteNotAllowed):
            c.request(method, "me")
    assert seen == ["GET"]
