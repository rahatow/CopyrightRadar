"""DEMO=1: synthetic posts so the UI can be tried without Meta or ACRCloud. No real data."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from app.storage.db import now

COLORS = ["#e76f51", "#2a9d8f", "#e9c46a", "#264653", "#f4a261", "#8ab17d", "#b56576", "#6d597a", "#355070"]

DEMO = [
    # (state, kind, meta matches, acr (title, artists, label, score) or status, audio, review)
    ("in_feed", "video", [{"author": "Big Label Group", "owner_copyright_policy": "Big Label Group"}], ("Neon Night", "Fictional Star", "Sony Music Entertainment", 100), True, None),
    ("in_feed", "video", [], ("Summer Loop", "Stock Composer", "Epidemic Sound", 96), True, None),
    ("in_feed", "video", [{"author": "Facebook Sound Collection", "owner_copyright_policy": "Facebook Sound Collection"}], None, None, None),
    ("in_feed", "video", [], "no_match", True, None),
    ("in_feed", "video", [], ("Bedroom Tapes", "Indie Person", "Small Records", 88), True, None),
    ("in_feed", "video", [], None, False, None),
    ("in_feed", "video", [], ("Famous Song (Cover)", "Cover Band", "Warner Records", 62), True, None),
    ("in_feed", "video", [], "no_match", True, "no_music"),
    ("missing", "video", [], ("Old Hit", "Legend", "Universal Music", 100), True, None),
    ("archived_manual", "video", [], ("Club Track", "DJ Example", "Atlantic Records", 99), True, None),
    ("archived_export", "photo", [], None, None, None),
    ("in_feed", "video", [], None, None, None),
]


def _svg(i: int, label: str) -> str:
    c1, c2 = COLORS[i % len(COLORS)], COLORS[(i + 3) % len(COLORS)]
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="480" height="640" viewBox="0 0 480 640">'
            f'<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="{c1}"/>'
            f'<stop offset="1" stop-color="{c2}"/></linearGradient></defs><rect width="480" height="640" fill="url(#g)"/>'
            f'<text x="240" y="330" font-family="sans-serif" font-size="44" fill="#fff" text-anchor="middle">{label}</text></svg>')


def seed(svc) -> None:
    db = svc.db
    base = datetime(2026, 9, 1, tzinfo=timezone.utc)
    thumbs = svc.data / "thumbs"
    thumbs.mkdir(parents=True, exist_ok=True)
    ts = now()
    for i, (state, kind, matches, acr, audio, review) in enumerate(DEMO):
        mid = f"demo{i:02d}"
        path = thumbs / f"{mid}.svg"
        path.write_text(_svg(i, f"Demo {i + 1}"), encoding="utf-8")
        db.x("INSERT OR IGNORE INTO media(id,shortcode,permalink,kind,media_product_type,timestamp,caption_excerpt,in_api,thumb_path,created_at) "
             "VALUES(?,?,?,?,?,?,?,?,?,?)",
             (mid, f"DEMO{i:02d}", None if state == "archived_export" else f"https://www.instagram.com/reel/DEMO{i:02d}/",
              kind, "REELS" if kind == "video" else "FEED", (base - timedelta(days=9 * i)).isoformat(),
              f"Demo post {i + 1}", int(state != "archived_export"), str(path), ts))
        db.x("INSERT OR IGNORE INTO presence(media_id,state,since) VALUES(?,?,?)", (mid, state, ts))
        if kind == "video":
            db.x("INSERT INTO meta_check(media_id,status,matches_json,raw_json,fetched_at) VALUES(?,?,?,?,?)",
                 (mid, "complete" if (matches or i % 3) else "not_started", json.dumps(matches), "{}", ts))
        if audio is not None:
            db.x("INSERT OR IGNORE INTO audio_source(media_id,kind,file_path,duration_s,has_audio_stream) VALUES(?,?,?,?,?)",
                 (mid, "export", "/nonexistent/demo.mp4", 24.0, int(audio)))
        if isinstance(acr, tuple):
            title, artists, label, score = acr
            db.x("INSERT INTO recognition(media_id,provider,segment_start_s,status,title,artists,label,score,raw_json,created_at) "
                 "VALUES(?,?,?,?,?,?,?,?,?,?)", (mid, "acrcloud", 0, "match", title, artists, label, score, "{}", ts))
        elif acr == "no_match":
            db.x("INSERT INTO recognition(media_id,provider,segment_start_s,status,raw_json,created_at) VALUES(?,?,?,?,?,?)",
                 (mid, "acrcloud", 0, "no_match", "{}", ts))
        if review:
            db.x("INSERT OR IGNORE INTO manual_review(media_id,verdict,note,reviewed_at) VALUES(?,?,?,?)", (mid, review, "", ts))
        svc.reassess(mid)
    db.set("account", {"username": "demo_studio", "user_id": "0", "account_type": "BUSINESS"})
    db.set("meta_copyright_supported", True)
    db.set("demo_seeded", True)
