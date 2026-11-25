"""«Aktualisieren» / Refresh: compare the Meta media list with the database. SPEC section 10.

decide_sync() is pure. run_refresh() applies it with a persisted lock and cooldown.
Guards: nothing changes on an incomplete answer, on a post without permalink, or when more
than 25 % of the feed disappears without a confirmation that names the exact number.
Manually archived posts never count toward the threshold and are never auto-returned.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field

from app.storage.db import DB, now

LOCK_STALE_S = 600


@dataclass
class SyncDecision:
    outcome: str  # "apply" | "abort" | "needs_confirmation"
    to_missing: list[str] = field(default_factory=list)
    to_in_feed: list[str] = field(default_factory=list)
    new_ids: list[str] = field(default_factory=list)
    still_visible_manual: list[str] = field(default_factory=list)
    reason: str = ""
    missing_ratio: float = 0.0


def decide_sync(
    in_feed_ids: set[str],
    missing_ids: set[str],
    fetched: list[dict],
    complete: bool,
    confirm_count: int | None = None,
    max_missing_ratio: float = 0.25,
    manual_ids: set[str] | None = None,
    known_ids: set[str] | None = None,
) -> SyncDecision:
    manual_ids = manual_ids or set()
    known_ids = known_ids if known_ids is not None else in_feed_ids | missing_ids | manual_ids
    if not complete:
        return SyncDecision("abort", reason="graph_failed")
    if any(not item.get("permalink") for item in fetched):
        return SyncDecision("abort", reason="graph_failed")

    fetched_ids = {str(item["id"]) for item in fetched}
    to_missing = sorted(in_feed_ids - fetched_ids)
    to_in_feed = sorted(missing_ids & fetched_ids)
    new_ids = sorted(fetched_ids - known_ids)
    still_visible = sorted(manual_ids & fetched_ids)
    ratio = len(to_missing) / len(in_feed_ids) if in_feed_ids else 0.0

    d = SyncDecision("apply", to_missing, to_in_feed, new_ids, still_visible, missing_ratio=ratio)
    if ratio > max_missing_ratio and confirm_count != len(to_missing):
        d.outcome, d.reason = "needs_confirmation", "too_many_missing"
    return d


class RefreshError(Exception):
    def __init__(self, code: str, **extra):
        super().__init__(code)
        self.code, self.extra = code, extra


def _parse_copyright(info) -> tuple[str | None, list[dict]]:
    """Tolerant: real answers differ from the docs (complete vs completed, TRACK action, ISRC segments)."""
    if not isinstance(info, dict):
        return None, []
    status = info.get("status")
    if isinstance(status, dict):
        status = status.get("status") or status.get("value")
    matches = []
    raw_list = next((v for v in (info.get("matches_found"), info.get("matches")) if isinstance(v, list)), [])
    for m in raw_list:
        if not isinstance(m, dict):
            continue
        owner = m.get("owner")
        author = m.get("author") or (owner.get("name") if isinstance(owner, dict) else owner)
        policy = m.get("owner_copyright_policy")
        if isinstance(policy, dict):
            policy = policy.get("name") or policy.get("value")
        matches.append({"author": author, "owner_copyright_policy": policy,
                        "title": m.get("content_title") or m.get("title"), "raw": m})
    return (str(status) if status is not None else None), matches


def run_refresh(db: DB, client, *, confirm_count: int | None = None, cooldown_s: int = 60,
                max_missing_ratio: float = 0.25) -> dict:
    t = time.time()
    lock = db.get("refresh_lock")
    if lock and t - lock < LOCK_STALE_S:
        raise RefreshError("refresh_running")
    last = db.get("refresh_last_started")
    if last and t - last < cooldown_s and confirm_count is None:
        raise RefreshError("too_soon")
    db.set("refresh_lock", t)
    db.set("refresh_last_started", t)
    run_id = db.x("INSERT INTO sync_run(started_at) VALUES(?)", (now(),))
    try:
        with_cr = db.get("meta_copyright_supported", False)
        page = client.list_media(with_copyright=with_cr)
        rows = db.q("SELECT m.id, p.state FROM media m JOIN presence p ON p.media_id=m.id WHERE m.in_api=1")
        in_feed = {r["id"] for r in rows if r["state"] == "in_feed"}
        missing = {r["id"] for r in rows if r["state"] == "missing"}
        manual = {r["id"] for r in rows if r["state"] == "archived_manual"}
        known = {r["id"] for r in db.q("SELECT id FROM media")}
        d = decide_sync(in_feed, missing, page.items, page.complete, confirm_count, max_missing_ratio, manual, known)
        if d.outcome != "apply":
            db.x("UPDATE sync_run SET finished_at=?, fetched_count=?, complete=?, outcome=?, detail=? WHERE id=?",
                 (now(), len(page.items), int(page.complete), d.reason, page.error or "", run_id))
            extra = {"missing": len(d.to_missing), "feed": len(in_feed)} if d.reason == "too_many_missing" else {}
            raise RefreshError(d.reason, **extra)

        ts = now()
        with db.tx() as c:
            for item in page.items:
                mid = str(item["id"])
                kind = "photo" if item.get("media_type") == "IMAGE" else ("album" if item.get("media_type") == "CAROUSEL_ALBUM" else "video")
                caption = (item.get("caption") or "")[:280]
                thumb_url = item.get("thumbnail_url") or (item.get("media_url") if kind != "video" else None)
                c.execute(
                    "INSERT INTO media(id,shortcode,permalink,kind,media_product_type,timestamp,caption_excerpt,in_api,thumb_url,media_url,created_at) "
                    "VALUES(?,?,?,?,?,?,?,1,?,?,?) ON CONFLICT(id) DO UPDATE SET permalink=excluded.permalink, "
                    "shortcode=excluded.shortcode, in_api=1, thumb_url=excluded.thumb_url, media_url=excluded.media_url, "
                    "caption_excerpt=excluded.caption_excerpt",
                    (mid, item.get("shortcode"), item.get("permalink"), kind, item.get("media_product_type"),
                     item.get("timestamp"), caption, thumb_url, item.get("media_url") if kind == "video" else None, ts),
                )
                if mid in d.new_ids:
                    c.execute("INSERT OR REPLACE INTO presence(media_id,state,since,sync_run_id) VALUES(?,?,?,?)",
                              (mid, "in_feed", ts, run_id))
                if "copyright_check_information" in item:
                    status, matches = _parse_copyright(item["copyright_check_information"])
                    c.execute("INSERT INTO meta_check(media_id,status,matches_json,raw_json,fetched_at) VALUES(?,?,?,?,?)",
                              (mid, status, json.dumps(matches), json.dumps(item["copyright_check_information"]), ts))
            for mid in d.to_missing:
                c.execute("UPDATE presence SET state='missing', since=?, sync_run_id=? WHERE media_id=?", (ts, run_id, mid))
            for mid in d.to_in_feed:
                c.execute("UPDATE presence SET state='in_feed', since=?, sync_run_id=? WHERE media_id=?", (ts, run_id, mid))
            c.execute("UPDATE sync_run SET finished_at=?, fetched_count=?, complete=1, outcome='applied' WHERE id=?",
                      (ts, len(page.items), run_id))
        n_feed = db.one("SELECT COUNT(*) n FROM presence WHERE state='in_feed'")["n"]
        n_missing = db.one("SELECT COUNT(*) n FROM presence WHERE state='missing'")["n"]
        result = {"active": n_feed, "missing": n_missing, "newly_missing": len(d.to_missing),
                  "returned": len(d.to_in_feed), "new_posts": len(d.new_ids),
                  "still_visible_manual": len(d.still_visible_manual), "at": ts}
        db.set("last_refresh", result)
        db.audit("refresh", result)
        return {**result, "new_ids": d.new_ids}
    finally:
        db.set("refresh_lock", None)
