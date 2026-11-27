"""Application service: ties storage, Instagram, export import, audio, ACRCloud and risk rules.

Long work (thumbnails, downloads, recognition) runs in one background worker thread, one
item at a time, so Meta and ACRCloud never get parallel requests.
"""
from __future__ import annotations

import json
import logging
import queue
import shutil
import threading
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from app.audio import extract
from app.config import Settings
from app.importer import export_zip
from app.instagram.client import InstagramClient
from app.recognition.acrcloud import ACRCloud, ACRCloudError
from app.risk.rules import MediaSignals, MetaMatch, Recognition, RiskRules, assess
from app.storage.db import DB, now
from app.storage.secrets import SecretStore, load_master_key

log = logging.getLogger("copyright_radar")
CDN_SUFFIXES = (".cdninstagram.com", ".fbcdn.net")
MAX_DOWNLOAD = 300 * 1024 * 1024


class Service:
    def __init__(self, settings: Settings, ig_factory=None, acr_factory=None, start_worker: bool = True):
        self.settings = settings
        self.data = settings.data_dir
        self.data.mkdir(parents=True, exist_ok=True)
        self.db = DB(self.data / "copyright-radar.db")
        self.secrets = SecretStore(self.db, load_master_key(self.data))
        self.rules = RiskRules.load(settings.rules_path)
        self._ig_factory = ig_factory or (lambda token, ig_user_id=None: InstagramClient(
            token, pause_s=settings.meta_pause_s, ig_user_id=ig_user_id))
        self._acr_factory = acr_factory or (lambda cfg: ACRCloud(cfg["host"], cfg["key"], cfg["secret"]))
        self.jobs: queue.Queue = queue.Queue()
        self.job_state = {"pending": 0, "current": None, "done": 0, "errors": []}
        if start_worker:
            threading.Thread(target=self._worker, daemon=True).start()

    # ---------- configuration ----------
    def ig(self) -> InstagramClient | None:
        token = self.secrets.get("instagram_token")
        if not token:
            return None
        account = self.db.get("account") or {}
        return self._ig_factory(token, ig_user_id=account.get("user_id"))

    def acr(self):
        raw = self.secrets.get("acrcloud")
        return self._acr_factory(json.loads(raw)) if raw else None

    # ---------- background jobs ----------
    def enqueue(self, kind: str, media_id: str | None = None) -> None:
        self.job_state["pending"] += 1
        self.jobs.put((kind, media_id))

    def _worker(self) -> None:
        while True:
            kind, mid = self.jobs.get()
            self.job_state["current"] = f"{kind}:{mid or ''}"
            try:
                if kind == "analyze":
                    self.analyze(mid)
                elif kind == "thumb":
                    self.cache_thumb(mid)
            except Exception as e:  # keep the worker alive; show the error in the UI
                log.warning("job %s %s failed: %s", kind, mid, type(e).__name__)
                self.job_state["errors"] = (self.job_state["errors"] + [f"{kind} {mid}: {e}"])[-20:]
            finally:
                self.job_state["pending"] -= 1
                self.job_state["done"] += 1
                self.job_state["current"] = None

    def run_jobs_now(self) -> None:
        """Tests: drain the queue in this thread."""
        while not self.jobs.empty():
            kind, mid = self.jobs.get()
            self.job_state["pending"] -= 1
            (self.analyze if kind == "analyze" else self.cache_thumb)(mid)

    # ---------- files ----------
    def _download(self, url: str, dest: Path) -> bool:
        host = urlsplit(url).hostname or ""
        if urlsplit(url).scheme != "https" or not host.endswith(CDN_SUFFIXES):
            return False
        dest.parent.mkdir(parents=True, exist_ok=True)
        size = 0
        with httpx.stream("GET", url, timeout=60, follow_redirects=False) as r:
            if r.status_code != 200:
                return False
            with open(dest, "wb") as f:
                for chunk in r.iter_bytes():
                    size += len(chunk)
                    if size > MAX_DOWNLOAD:
                        f.close()
                        dest.unlink(missing_ok=True)
                        return False
                    f.write(chunk)
        return True

    def cache_thumb(self, media_id: str) -> None:
        m = self.db.one("SELECT * FROM media WHERE id=?", (media_id,))
        if not m or m["thumb_path"]:
            return
        dest = self.data / "thumbs" / f"{_safe(media_id)}.jpg"
        dest.parent.mkdir(parents=True, exist_ok=True)
        src = self.db.one("SELECT file_path FROM audio_source WHERE media_id=?", (media_id,))
        ok = False
        if m["thumb_url"]:
            try:
                ok = self._download(m["thumb_url"], dest)
            except httpx.HTTPError:
                ok = False
        if not ok and src and Path(src["file_path"]).exists():
            ok = extract.thumbnail(Path(src["file_path"]), dest)
        if not ok and m["kind"] == "photo":
            ex = self.db.one("SELECT value FROM kv WHERE key=?", (f"export_photo:{media_id}",))
            if ex:
                shutil.copyfile(json.loads(ex["value"]), dest)
                ok = True
        if ok:
            self.db.x("UPDATE media SET thumb_path=? WHERE id=?", (str(dest), media_id))

    # ---------- analysis ----------
    def analyze(self, media_id: str) -> None:
        m = self.db.one("SELECT * FROM media WHERE id=?", (media_id,))
        if not m:
            return
        if m["kind"] == "video":
            src = self.db.one("SELECT * FROM audio_source WHERE media_id=?", (media_id,))
            if not src and m["media_url"]:
                dest = self.data / "media" / f"{_safe(media_id)}.mp4"
                try:
                    if self._download(m["media_url"], dest):
                        self._set_audio_source(media_id, "api", dest)
                except httpx.HTTPError:
                    pass
                src = self.db.one("SELECT * FROM audio_source WHERE media_id=?", (media_id,))
            if src and src["has_audio_stream"] and Path(src["file_path"]).exists():
                acr = self.acr()
                if acr:
                    self._recognize(media_id, Path(src["file_path"]), src["duration_s"], acr)
        if not m["thumb_path"]:
            self.cache_thumb(media_id)
        self.reassess(media_id)

    def _set_audio_source(self, media_id: str, kind: str, path: Path) -> None:
        info = extract.probe(path)
        self.db.x(
            "INSERT INTO audio_source(media_id,kind,file_path,duration_s,has_audio_stream) VALUES(?,?,?,?,?) "
            "ON CONFLICT(media_id) DO UPDATE SET kind=excluded.kind, file_path=excluded.file_path, "
            "duration_s=excluded.duration_s, has_audio_stream=excluded.has_audio_stream",
            (media_id, kind, str(path), info["duration"], int(info["has_audio"])),
        )

    def _recognize(self, media_id: str, path: Path, duration: float | None, acr) -> None:
        rows = []
        for start in extract.fragment_starts(duration):
            try:
                res = acr.identify(extract.cut_fragment(path, start))
                if res.no_fingerprint and duration and start + 20 < duration:
                    start += 5
                    res = acr.identify(extract.cut_fragment(path, start))
            except ACRCloudError as e:
                if e.code in (3001, 3003, 3014, 3015):
                    raise  # key or quota problem: stop, do not burn the quota
                rows.append((start, Recognition(status="error"), None, {"error": str(e)}))
                continue
            rows.append((start, res.recognition, res.isrc, res.raw))
        with self.db.tx() as c:
            c.execute("DELETE FROM recognition WHERE media_id=? AND provider='acrcloud'", (media_id,))
            for start, rec, isrc, raw in rows:
                c.execute(
                    "INSERT INTO recognition(media_id,provider,segment_start_s,status,title,artists,label,isrc,score,raw_json,created_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (media_id, "acrcloud", start, rec.status, rec.title, rec.artists, rec.label, isrc, rec.score,
                     json.dumps(raw)[:20000], now()),
                )

    def signals(self, media_id: str) -> MediaSignals:
        src = self.db.one("SELECT * FROM audio_source WHERE media_id=?", (media_id,))
        meta = self.db.one("SELECT * FROM meta_check WHERE media_id=? ORDER BY fetched_at DESC, rowid DESC LIMIT 1", (media_id,))
        recs = self.db.q("SELECT * FROM recognition WHERE media_id=?", (media_id,))
        review = self.db.one("SELECT verdict FROM manual_review WHERE media_id=?", (media_id,))
        matches = [MetaMatch(author=x.get("author"), owner_copyright_policy=x.get("owner_copyright_policy"))
                   for x in json.loads(meta["matches_json"] or "[]")] if meta else []
        return MediaSignals(
            has_audio_stream=(bool(src["has_audio_stream"]) if src else None),
            meta_status=meta["status"] if meta else None,
            meta_matches=matches,
            recognitions=[Recognition(status=r["status"], title=r["title"], artists=r["artists"], label=r["label"],
                                      score=r["score"]) for r in recs],
            manual_verdict=review["verdict"] if review else None,
        )

    def reassess(self, media_id: str) -> None:
        m = self.db.one("SELECT kind FROM media WHERE id=?", (media_id,))
        if not m:
            return
        if m["kind"] == "photo":
            a_level, reasons, version = "no_audio", [{"k": "photo"}], self.rules.version
            auto = a_level
            review = self.db.one("SELECT verdict FROM manual_review WHERE media_id=?", (media_id,))
            if review:
                a_level = {"confirmed_risk": "high", "cleared": "low", "no_music": "no_music"}[review["verdict"]]
                reasons.insert(0, {"k": f"manual_{review['verdict']}"})
        else:
            a = assess(self.signals(media_id), self.rules)
            a_level, auto, reasons, version = a.level, a.automatic_level, a.reasons, a.rules_version
        self.db.x("INSERT INTO assessment(media_id,level,automatic_level,reasons_json,rules_version,assessed_at) "
                  "VALUES(?,?,?,?,?,?)", (media_id, a_level, auto, json.dumps(reasons), version, now()))

    # ---------- export import ----------
    def import_export(self, zip_path: Path) -> dict:
        target = self.data / "export"
        res = export_zip.read_export(zip_path, target)
        api_media = [dict(r) for r in self.db.q("SELECT id, kind, timestamp FROM media WHERE in_api=1")]
        matched = export_zip.match_items(res.items, api_media)
        counts = {"files": len(res.items), "matched": 0, "added": 0, "archived": 0, "without_file": 0, "skipped_json": res.skipped}
        ts = now()
        for i, it in enumerate(res.items):
            if it.local_path is None:
                counts["without_file"] += 1
            mid = matched.get(i)
            if mid:
                counts["matched"] += 1
            else:
                mid = export_zip.export_id(it)
                # Without a Meta sync we cannot know what is still in the feed: treat it as in feed
                # (as of the export). After a sync, an unmatched post is not in the feed.
                synced = bool(self.db.get("last_refresh"))
                state = "archived_export" if it.source == "archived" else ("missing" if synced else "in_feed")
                counts["archived" if state == "archived_export" else "added"] += 1
                self.db.x(
                    "INSERT INTO media(id,kind,timestamp,caption_excerpt,in_api,created_at) VALUES(?,?,?,?,0,?) "
                    "ON CONFLICT(id) DO NOTHING",
                    (mid, it.kind, export_zip.iso_from_epoch(it.timestamp), (it.caption or "")[:280], ts),
                )
                self.db.x("INSERT INTO presence(media_id,state,since) VALUES(?,?,?) ON CONFLICT(media_id) DO NOTHING",
                          (mid, state, ts))
            if it.local_path is not None:
                if it.kind == "video":
                    existing = self.db.one("SELECT kind FROM audio_source WHERE media_id=?", (mid,))
                    if not existing or existing["kind"] != "upload":
                        self._set_audio_source(mid, "export", it.local_path)
                else:
                    self.db.set(f"export_photo:{mid}", str(it.local_path))
            self.enqueue("analyze", mid)
        self.db.audit("import_export", counts)
        self.db.set("last_import", {**counts, "at": ts})
        return counts

    def reconcile_export(self) -> dict:
        """After a Meta sync: merge export-only items into the API posts they belong to.

        Export-only rows that still have no API post are then marked as not in the feed.
        """
        api_media = [dict(r) for r in self.db.q(
            "SELECT m.id, m.kind, m.timestamp FROM media m WHERE m.in_api=1 "
            "AND NOT EXISTS (SELECT 1 FROM audio_source a WHERE a.media_id=m.id)")]
        rows = self.db.q("SELECT m.*, p.state FROM media m JOIN presence p ON p.media_id=m.id "
                         "WHERE m.in_api=0 AND m.id LIKE 'export:%' AND p.state='in_feed'")
        items = [export_zip.ExportItem(uri=r["id"], kind=r["kind"], timestamp=int(export_zip._epoch(r["timestamp"]) or 0) or None,
                                       caption=None, source="posts") for r in rows]
        matched = export_zip.match_items(items, api_media)
        merged = missing = 0
        ts = now()
        with self.db.tx() as c:
            for i, r in enumerate(rows):
                old_id, new_id = r["id"], matched.get(i)
                if new_id:
                    c.execute("UPDATE audio_source SET media_id=? WHERE media_id=?", (new_id, old_id))
                    c.execute("UPDATE kv SET key=? WHERE key=?", (f"export_photo:{new_id}", f"export_photo:{old_id}"))
                    if r["thumb_path"]:
                        c.execute("UPDATE media SET thumb_path=COALESCE(thumb_path, ?) WHERE id=?", (r["thumb_path"], new_id))
                    for table in ("recognition", "manual_review", "assessment", "presence"):
                        if table in ("recognition", "manual_review"):
                            c.execute(f"UPDATE OR IGNORE {table} SET media_id=? WHERE media_id=?", (new_id, old_id))
                        c.execute(f"DELETE FROM {table} WHERE media_id=?", (old_id,))
                    c.execute("DELETE FROM media WHERE id=?", (old_id,))
                    merged += 1
                else:
                    c.execute("UPDATE presence SET state='missing', since=? WHERE media_id=?", (ts, old_id))
                    missing += 1
        for mid in set(matched.values()):
            self.reassess(mid)
        return {"merged": merged, "missing": missing}

    def upload_audio(self, media_id: str, path: Path) -> None:
        self._set_audio_source(media_id, "upload", path)
        self.enqueue("analyze", media_id)

    # ---------- read model for the UI ----------
    def media_list(self) -> list[dict]:
        rows = self.db.q("""
            SELECT m.*, p.state, p.since,
              (SELECT level FROM assessment a WHERE a.media_id=m.id ORDER BY a.rowid DESC LIMIT 1) AS level,
              (SELECT reasons_json FROM assessment a WHERE a.media_id=m.id ORDER BY a.rowid DESC LIMIT 1) AS reasons,
              (SELECT automatic_level FROM assessment a WHERE a.media_id=m.id ORDER BY a.rowid DESC LIMIT 1) AS auto_level
            FROM media m LEFT JOIN presence p ON p.media_id=m.id
            ORDER BY COALESCE(m.timestamp, m.created_at) DESC""")
        out = []
        for r in rows:
            mid = r["id"]
            meta = self.db.q("SELECT status, matches_json, fetched_at FROM meta_check WHERE media_id=? ORDER BY rowid DESC LIMIT 3", (mid,))
            recs = self.db.q("SELECT segment_start_s, status, title, artists, label, isrc, score FROM recognition WHERE media_id=? ORDER BY segment_start_s", (mid,))
            src = self.db.one("SELECT kind, duration_s, has_audio_stream FROM audio_source WHERE media_id=?", (mid,))
            review = self.db.one("SELECT verdict, note, reviewed_at FROM manual_review WHERE media_id=?", (mid,))
            out.append({
                "manual_check": manual_check_reasons(r["kind"], src, recs, review, r["media_url"], r["in_api"]),
                "id": mid, "shortcode": r["shortcode"], "permalink": r["permalink"], "kind": r["kind"],
                "product": r["media_product_type"], "timestamp": r["timestamp"], "caption": r["caption_excerpt"],
                "in_api": bool(r["in_api"]), "state": r["state"] or "in_feed", "since": r["since"],
                "thumb": f"/media/thumb/{mid}" if r["thumb_path"] else None,
                "level": r["level"] or "unknown", "auto_level": r["auto_level"],
                "reasons": json.loads(r["reasons"]) if r["reasons"] else [{"k": "not_analyzed"}],
                "meta": [{"status": x["status"], "matches": [{k: v for k, v in mm.items() if k != "raw"} for mm in json.loads(x["matches_json"] or "[]")],
                          "at": x["fetched_at"]} for x in meta],
                "acr": [dict(x) for x in recs],
                "audio": ({**dict(src), "playable": Path(src_path["file_path"]).exists()} if src and (src_path := self.db.one(
                    "SELECT file_path FROM audio_source WHERE media_id=?", (mid,))) else None),
                "review": dict(review) if review else None,
            })
        return out


def manual_check_reasons(kind, src, recs, review, media_url, in_api) -> list[str]:
    """Why a person should check this post by hand. Empty list = nothing to do.

    No new risk level is invented here: the automatic level stays as it is, the post is only
    put on the manual-check list (SPEC 9, «Проверить вручную»).
    """
    if review or kind != "video":
        return []
    out = []
    if src is None:
        # Meta omits media_url for videos with copyrighted or licensed audio (Meta docs).
        out.append("no_file_meta" if in_api and not media_url else "no_file")
        return out
    if not src["has_audio_stream"]:
        return []
    statuses = [r["status"] for r in recs]
    if not statuses:
        out.append("not_recognized_yet")
    elif all(s == "no_match" for s in statuses):
        out.append("not_recognized")
    elif any(s == "error" for s in statuses):
        out.append("recognition_error")
    if any(r["status"] == "match" and r["score"] is not None and r["score"] < 70 for r in recs):
        out.append("weak_match")
    return out


def _safe(media_id: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in media_id)
