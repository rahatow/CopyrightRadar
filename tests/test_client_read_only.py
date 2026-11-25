import httpx
import pytest

from app.instagram.client import InstagramClient, MetaError, WriteNotAllowed

TOKEN = "IGTESTTOKEN123"


def test_only_get_is_allowed():
    seen = []
    transport = httpx.MockTransport(lambda req: seen.append(req.method) or httpx.Response(200, json={"id": "1"}))
    c = InstagramClient(TOKEN, pause_s=0, transport=transport)
    assert c.get("me") == {"id": "1"}
    for method in ("POST", "DELETE", "PUT"):
        with pytest.raises(WriteNotAllowed):
            c.request(method, "me")
    assert seen == ["GET"]


def test_paging_strips_token_and_pins_host():
    urls = []

    def handler(req):
        urls.append(req.url)
        if "after" not in req.url.params:
            return httpx.Response(200, json={"data": [{"id": "1", "permalink": "p"}], "paging": {
                "next": f"https://graph.instagram.com/v23.0/me/media?after=X&access_token=LEAKED&fields=id"}})
        return httpx.Response(200, json={"data": [{"id": "2", "permalink": "p"}]})

    c = InstagramClient(TOKEN, pause_s=0, transport=httpx.MockTransport(handler))
    page = c.list_media(with_copyright=False)
    assert page.complete and [i["id"] for i in page.items] == ["1", "2"]
    assert urls[1].params.get_list("access_token") == [TOKEN]


def test_foreign_paging_host_is_rejected():
    def handler(req):
        return httpx.Response(200, json={"data": [], "paging": {"next": "https://evil.example/steal"}})

    page = InstagramClient(TOKEN, pause_s=0, transport=httpx.MockTransport(handler)).list_media(False)
    assert not page.complete and "another host" in page.error


def test_error_text_is_redacted():
    def handler(req):
        return httpx.Response(400, json={"error": {"message": f"bad token {TOKEN}", "code": 190}})

    with pytest.raises(MetaError) as e:
        InstagramClient(TOKEN, pause_s=0, transport=httpx.MockTransport(handler)).me()
    assert TOKEN not in str(e.value) and e.value.code == 190
