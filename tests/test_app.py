"""End-to-end through HTTP with a fake Instagram API. No network."""
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import ROOT, Settings
from app.instagram.client import InstagramClient
from app.main import create_app
from app.service import Service

ORIGIN = {"Origin": "http://testserver"}


class FakeMeta:
    def __init__(self, n=8):
        self.ids = [str(1000 + i) for i in range(n)]

    def handler(self, req: httpx.Request):
        path = req.url.path
        if path.endswith("/me"):
            return httpx.Response(200, json={"user_id": "42", "username": "test_studio", "account_type": "BUSINESS"})
        if path.endswith("/me/media"):
            data = [{"id": i, "permalink": f"https://www.instagram.com/reel/S{i}/", "shortcode": f"S{i}",
                     "media_type": "VIDEO", "media_product_type": "REELS", "timestamp": "2026-10-01T10:00:00+0000",
                     "copyright_check_information": {"status": {"status": "complete", "matches_found": i == "1000"},
                                                     "matches_found": [{"author": "Big Label", "owner_copyright_policy": {"name": "Big Label"}}] if i == "1000" else []}}
                    for i in self.ids]
            return httpx.Response(200, json={"data": data})
        return httpx.Response(404, json={"error": {"message": "nope", "code": 100}})


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.delenv("APP_SECRET_KEY", raising=False)
    meta = FakeMeta()
    settings = Settings(data_dir=tmp_path, rules_path=ROOT / "config" / "risk_rules.example.toml", refresh_cooldown_s=0)
    svc = Service(settings, ig_factory=lambda tok, ig_user_id=None: InstagramClient(tok, pause_s=0, transport=httpx.MockTransport(meta.handler)),
                  start_worker=False)
    client = TestClient(create_app(settings, svc))
    return client, svc, meta


def setup_all(client):
    r = client.post("/setup/password", data={"password": "correct horse 1", "password2": "correct horse 1"}, headers=ORIGIN,
                    follow_redirects=False)
    assert r.status_code == 303 and "msg=password_saved" in r.headers["location"]
    r = client.post("/setup/instagram", data={"token": "IGTOKEN"}, headers=ORIGIN, follow_redirects=False)
    assert "msg=instagram_saved" in r.headers["location"]


def test_first_start_redirects_to_setup(env):
    client, *_ = env
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/setup"


def test_api_needs_login_and_same_origin(env):
    client, *_ = env
    assert client.get("/api/state").status_code == 401
    setup_all(client)
    assert client.get("/api/state").status_code == 200
    r = client.post("/api/refresh", json={}, headers={"Origin": "https://evil.example"})
    assert r.status_code == 403 and r.json()["error"] == "cross_origin_rejected"
    r = client.post("/api/refresh", json={})  # no Origin at all
    assert r.status_code == 403


def test_token_is_encrypted_at_rest(env):
    client, svc, _ = env
    setup_all(client)
    raw = Path(svc.settings.data_dir / "copyright-radar.db").read_bytes()
    assert b"IGTOKEN" not in raw
    assert svc.secrets.get("instagram_token") == "IGTOKEN"


def test_refresh_mark_review_flow(env):
    client, svc, meta = env
    setup_all(client)
    r = client.post("/api/refresh", json={}, headers=ORIGIN)
    assert r.status_code == 200 and r.json()["new_posts"] == 8
    state = client.get("/api/state").json()
    levels = {m["id"]: m["level"] for m in state["media"]}
    assert levels["1000"] == "high"            # Meta matched a rights holder
    assert levels["1001"] == "unknown"         # Meta "no match" never lowers risk

    # mark by hand, then Meta still lists it: stays archived, reported as still visible
    assert client.post("/api/mark", json={"id": "1000", "archived": True}, headers=ORIGIN).status_code == 200
    r = client.post("/api/refresh", json={}, headers=ORIGIN).json()
    assert r["still_visible_manual"] == 1
    assert {m["id"]: m["state"] for m in client.get("/api/state").json()["media"]}["1000"] == "archived_manual"

    # one post disappears -> missing with date; it comes back -> in feed again
    meta.ids.remove("1001")
    assert client.post("/api/refresh", json={}, headers=ORIGIN).json()["newly_missing"] == 1
    meta.ids.append("1001")
    assert client.post("/api/refresh", json={}, headers=ORIGIN).json()["returned"] == 1

    # manual review overrides the automatic level
    assert client.post("/api/review", json={"id": "1001", "verdict": "no_music"}, headers=ORIGIN).status_code == 200
    m = {m["id"]: m for m in client.get("/api/state").json()["media"]}["1001"]
    assert m["level"] == "no_music" and m["auto_level"] == "unknown"


def test_mass_disappearance_needs_exact_confirmation(env):
    client, svc, meta = env
    setup_all(client)
    client.post("/api/refresh", json={}, headers=ORIGIN)
    del meta.ids[:4]  # 4 of 8 gone = 50 %
    r = client.post("/api/refresh", json={}, headers=ORIGIN)
    assert r.status_code == 409 and r.json() == {"error": "too_many_missing", "missing": 4, "feed": 8}
    assert all(m["state"] == "in_feed" for m in client.get("/api/state").json()["media"])
    r = client.post("/api/refresh", json={"confirm": 4}, headers=ORIGIN)
    assert r.status_code == 200 and r.json()["newly_missing"] == 4


def test_incomplete_meta_answer_changes_nothing(env):
    client, svc, meta = env
    setup_all(client)
    client.post("/api/refresh", json={}, headers=ORIGIN)
    meta.handler = lambda req: httpx.Response(500, json={"error": {"message": "down", "code": 2}})
    r = client.post("/api/refresh", json={}, headers=ORIGIN)
    assert r.status_code == 409 and r.json()["error"] == "graph_failed"
    assert all(m["state"] == "in_feed" for m in client.get("/api/state").json()["media"])


def test_csv_export_has_disclaimer(env):
    client, *_ = env
    setup_all(client)
    client.post("/api/refresh", json={}, headers=ORIGIN)
    r = client.get("/export.csv?lang=en")
    assert r.status_code == 200 and "not legal advice" in r.text


def test_demo_mode(tmp_path, monkeypatch):
    monkeypatch.delenv("APP_SECRET_KEY", raising=False)
    settings = Settings(data_dir=tmp_path, rules_path=ROOT / "config" / "risk_rules.example.toml", demo=True)
    client = TestClient(create_app(settings, Service(settings, start_worker=False)))
    setup_all_password_only(client)
    media = client.get("/api/state").json()["media"]
    assert len(media) == 12 and {m["level"] for m in media} >= {"high", "medium", "low", "unknown", "no_audio", "no_music"}


def setup_all_password_only(client):
    client.post("/setup/password", data={"password": "correct horse 1", "password2": "correct horse 1"}, headers=ORIGIN)


def test_pages_render_in_three_languages(env):
    client, *_ = env
    setup_all(client)
    for lang, word in (("de", "Aktualisieren"), ("en", "Refresh"), ("ru", "Актуализировать")):
        assert word in client.get(f"/?lang={lang}").text


def test_export_before_sync_then_merge_after_sync(env, tmp_path):
    """Export-only use works without Meta; a later sync merges export items into API posts."""
    import json, zipfile
    client, svc, meta = env
    setup_all(client)
    zp = tmp_path / "ex.zip"
    with zipfile.ZipFile(zp, "w") as z:
        # 2026-10-01T10:00:00Z = 1790848800 matches the fake API posts; second one has no API post
        z.writestr("c/reels.json", json.dumps({"ig_reels_media": [
            {"media": [{"uri": "media/reels/a.mp4", "creation_timestamp": 1790848830, "title": "A"}]},
            {"media": [{"uri": "media/reels/b.mp4", "creation_timestamp": 1600000000, "title": "B"}]}]}))
    r = client.post("/api/import", files={"file": ("ex.zip", zp.read_bytes(), "application/zip")}, headers=ORIGIN)
    assert r.status_code == 200 and r.json()["added"] == 2
    assert all(m["state"] == "in_feed" for m in client.get("/api/state").json()["media"])
    r = client.post("/api/refresh", json={}, headers=ORIGIN).json()
    assert r["export_merged"] == 1
    media = client.get("/api/state").json()["media"]
    export_left = [m for m in media if m["id"].startswith("export:")]
    assert len(export_left) == 1 and export_left[0]["state"] == "missing"
