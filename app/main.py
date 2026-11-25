"""FastAPI app: pages, JSON API, setup wizard. See docs/SPEC.md."""
from __future__ import annotations

import csv
import io
import json
import shutil
import tempfile
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app import auth
from app.audio import extract
from app.config import Settings
from app.instagram.client import MetaError
from app.instagram.sync import RefreshError, run_refresh
from app.recognition.acrcloud import ACRCloudError
from app.service import Service, _safe
from app.storage.db import now

WEB = Path(__file__).parent / "web"
LANGS = ("de", "en", "ru")
DISCLAIMER = "Risk levels are indicators, not legal advice."
MAX_UPLOAD = 4 * 1024 * 1024 * 1024  # 4 GB export


def _i18n() -> dict:
    return {lang: json.loads((WEB / "i18n" / f"{lang}.json").read_text(encoding="utf-8")) for lang in LANGS}


def create_app(settings: Settings | None = None, service: Service | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    svc = service or Service(settings)
    texts = _i18n()
    app = FastAPI(title="CopyrightRadar", version="0.1.0", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.svc = svc
    app.mount("/static", StaticFiles(directory=WEB / "static"), name="static")
    templates = Jinja2Templates(directory=WEB / "templates")

    if settings.demo and not svc.db.get("demo_seeded"):
        from app.demo import seed
        seed(svc)

    # ---------- helpers ----------
    def lang_of(request: Request) -> str:
        q = request.query_params.get("lang")
        if q in LANGS:
            return q
        c = request.cookies.get("lang")
        return c if c in LANGS else "de"

    def page(request: Request, name: str, **ctx) -> HTMLResponse:
        lang = lang_of(request)
        resp = templates.TemplateResponse(request, name, {"t": texts[lang], "lang": lang, "langs": LANGS, **ctx})
        if request.query_params.get("lang") in LANGS:
            resp.set_cookie("lang", lang, max_age=365 * 86400, samesite="strict")
        return resp

    def logged_in(request: Request) -> bool:
        pw = svc.db.get("password_hash")
        return bool(pw) and auth.check_session(svc.secrets.signing_key, request.cookies.get(auth.COOKIE),
                                               svc.db.get("password_version", 1))

    def require_login(request: Request) -> None:
        if not logged_in(request):
            raise HTTPException(401, detail={"error": "forbidden"})

    def require_write(request: Request) -> None:
        require_login(request)
        auth.require_same_origin(request)

    @app.exception_handler(HTTPException)
    async def _http_exc(request: Request, exc: HTTPException):
        body = exc.detail if isinstance(exc.detail, dict) else {"error": str(exc.detail)}
        return JSONResponse(body, status_code=exc.status_code)

    @app.middleware("http")
    async def _headers(request: Request, call_next):
        resp = await call_next(request)
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "same-origin"
        resp.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data:; media-src 'self'; style-src 'self'; script-src 'self'; "
            "frame-ancestors 'none'; form-action 'self'")
        return resp

    def set_session(resp: Response, request: Request) -> None:
        resp.set_cookie(auth.COOKIE, auth.make_session(svc.secrets.signing_key, svc.db.get("password_version", 1)),
                        httponly=True, samesite="strict", secure=request.url.scheme == "https",
                        max_age=auth.SESSION_DAYS * 86400)

    # ---------- pages ----------
    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "disclaimer": DISCLAIMER}

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request):
        if not svc.db.get("password_hash"):
            return RedirectResponse("/setup", 303)
        if not logged_in(request):
            return RedirectResponse("/login", 303)
        return page(request, "index.html")

    @app.get("/login", response_class=HTMLResponse)
    def login_page(request: Request):
        return page(request, "login.html", error=None)

    @app.post("/login")
    def login(request: Request, password: str = Form(...)):
        auth.require_same_origin(request)
        stored = svc.db.get("password_hash")
        if not stored or not auth.verify_password(password, stored):
            svc.db.audit("login_failed")
            return page(request, "login.html", error=True)
        resp = RedirectResponse("/", 303)
        set_session(resp, request)
        return resp

    @app.post("/logout")
    def logout(request: Request):
        auth.require_same_origin(request)
        resp = RedirectResponse("/login", 303)
        resp.delete_cookie(auth.COOKIE)
        return resp

    # ---------- setup wizard ----------
    def setup_ctx() -> dict:
        return {
            "has_password": bool(svc.db.get("password_hash")),
            "account": svc.db.get("account"),
            "copyright_supported": svc.db.get("meta_copyright_supported"),
            "token_saved_at": svc.secrets.created_at("instagram_token"),
            "acr_saved": bool(svc.secrets.get("acrcloud")),
            "synced": bool(svc.db.get("last_refresh")),
        }

    @app.get("/setup", response_class=HTMLResponse)
    def setup_page(request: Request):
        if svc.db.get("password_hash") and not logged_in(request):
            return RedirectResponse("/login", 303)
        return page(request, "setup.html", s=setup_ctx(), msg=request.query_params.get("msg"),
                    err=request.query_params.get("err"))

    def back(msg: str | None = None, err: str | None = None) -> RedirectResponse:
        q = f"?msg={msg}" if msg else f"?err={err}" if err else ""
        return RedirectResponse(f"/setup{q}#step", 303)

    @app.post("/setup/password")
    def setup_password(request: Request, password: str = Form(...), password2: str = Form(...)):
        auth.require_same_origin(request)
        if svc.db.get("password_hash") and not logged_in(request):
            raise HTTPException(403, detail={"error": "forbidden"})
        if len(password) < 10 or password != password2:
            return back(err="password_rules")
        svc.db.set("password_hash", auth.hash_password(password))
        svc.db.set("password_version", (svc.db.get("password_version", 0) or 0) + 1)
        svc.db.audit("password_set")
        resp = back(msg="password_saved")
        set_session(resp, request)
        return resp

    @app.post("/setup/instagram")
    def setup_instagram(request: Request, token: str = Form(...), _=Depends(require_write)):
        token = token.strip()
        client = svc._ig_factory(token)
        try:
            me = client.me()
        except MetaError:
            return back(err="token_invalid")
        if me.get("account_type") not in ("BUSINESS", "MEDIA_CREATOR", "CREATOR", None):
            return back(err="account_type")
        try:
            supported = client.probe_copyright()
        except MetaError:
            supported = False
        svc.secrets.put("instagram_token", token)
        svc.db.set("account", {"username": me.get("username"), "user_id": str(me.get("user_id") or me.get("id")),
                               "account_type": me.get("account_type")})
        svc.db.set("meta_copyright_supported", supported)
        svc.db.audit("instagram_connected", {"copyright_field": supported})
        return back(msg="instagram_saved")

    @app.post("/setup/acrcloud")
    def setup_acrcloud(request: Request, host: str = Form(...), key: str = Form(...), secret: str = Form(...),
                       _=Depends(require_write)):
        cfg = {"host": host.strip(), "key": key.strip(), "secret": secret.strip()}
        try:
            client = svc._acr_factory(cfg)
            client.identify(extract.silence_mp3())
        except ValueError:
            return back(err="acr_host")
        except ACRCloudError as e:
            return back(err="acr_key" if e.code in (3001, 3014) else "acr_failed")
        svc.secrets.put("acrcloud", json.dumps(cfg))
        svc.db.audit("acrcloud_connected")
        return back(msg="acr_saved")

    # ---------- JSON API ----------
    @app.get("/api/state")
    def state(_=Depends(require_login)):
        items = svc.media_list()
        return {
            "account": svc.db.get("account"),
            "last_refresh": svc.db.get("last_refresh"),
            "copyright_supported": svc.db.get("meta_copyright_supported"),
            "acr_configured": bool(svc.secrets.get("acrcloud")),
            "instagram_configured": bool(svc.secrets.get("instagram_token")),
            "jobs": {k: v for k, v in svc.job_state.items()},
            "demo": settings.demo,
            "media": items,
        }

    @app.post("/api/refresh")
    async def refresh(request: Request, _=Depends(require_write)):
        body = await _json(request)
        client = svc.ig()
        if not client:
            raise HTTPException(409, detail={"error": "no_token"})
        confirm = body.get("confirm")
        try:
            result = run_refresh(svc.db, client, confirm_count=confirm if isinstance(confirm, int) else None,
                                 cooldown_s=settings.refresh_cooldown_s, max_missing_ratio=settings.max_missing_ratio)
        except RefreshError as e:
            raise HTTPException(409, detail={"error": e.code, **e.extra})
        result["export_merged"] = svc.reconcile_export()["merged"]
        for mid in result.pop("new_ids"):
            svc.enqueue("analyze", mid)
        for row in svc.db.q("SELECT id FROM media WHERE in_api=1"):
            svc.reassess(row["id"])  # new Meta checks may change levels
            svc.enqueue("thumb", row["id"])
        return result

    @app.post("/api/mark")
    async def mark(request: Request, _=Depends(require_write)):
        body = await _json(request)
        mid, archived = body.get("id"), body.get("archived")
        row = svc.db.one("SELECT p.state FROM presence p WHERE p.media_id=?", (mid,)) if isinstance(mid, str) else None
        if not row or not isinstance(archived, bool):
            raise HTTPException(400, detail={"error": "unknown_id"})
        if archived and row["state"] != "in_feed":
            raise HTTPException(409, detail={"error": "not_in_feed"})
        if not archived and row["state"] != "archived_manual":
            raise HTTPException(409, detail={"error": "not_marked"})
        svc.db.x("UPDATE presence SET state=?, since=? WHERE media_id=?",
                 ("archived_manual" if archived else "in_feed", now(), mid))
        svc.db.audit("mark", {"id": mid, "archived": archived})
        return {"ok": True}

    @app.post("/api/review")
    async def review(request: Request, _=Depends(require_write)):
        body = await _json(request)
        mid, verdict, note = body.get("id"), body.get("verdict"), (body.get("note") or "")[:1000]
        if not isinstance(mid, str) or not svc.db.one("SELECT 1 FROM media WHERE id=?", (mid,)):
            raise HTTPException(400, detail={"error": "unknown_id"})
        if verdict is None:
            svc.db.x("DELETE FROM manual_review WHERE media_id=?", (mid,))
        elif verdict in ("confirmed_risk", "cleared", "no_music"):
            svc.db.x("INSERT INTO manual_review(media_id,verdict,note,reviewed_at) VALUES(?,?,?,?) "
                     "ON CONFLICT(media_id) DO UPDATE SET verdict=excluded.verdict, note=excluded.note, reviewed_at=excluded.reviewed_at",
                     (mid, verdict, note, now()))
        else:
            raise HTTPException(400, detail={"error": "bad_verdict"})
        svc.reassess(mid)
        svc.db.audit("review", {"id": mid, "verdict": verdict})
        return {"ok": True}

    @app.post("/api/analyze")
    async def analyze(request: Request, _=Depends(require_write)):
        body = await _json(request)
        ids = body.get("ids")
        if ids == "all":
            ids = [r["id"] for r in svc.db.q("SELECT id FROM media WHERE kind='video'")]
        if not isinstance(ids, list):
            raise HTTPException(400, detail={"error": "bad_ids"})
        for mid in ids[:2000]:
            svc.enqueue("analyze", str(mid))
        return {"queued": len(ids[:2000])}

    @app.post("/api/import")
    async def import_zip(request: Request, file: UploadFile = File(...), _=Depends(require_write)):
        with tempfile.NamedTemporaryFile(suffix=".zip", dir=settings.data_dir, delete=False) as tmp:
            size = 0
            while chunk := await file.read(1 << 20):
                size += len(chunk)
                if size > MAX_UPLOAD:
                    Path(tmp.name).unlink(missing_ok=True)
                    raise HTTPException(413, detail={"error": "too_large"})
                tmp.write(chunk)
        try:
            return svc.import_export(Path(tmp.name))
        except Exception:
            raise HTTPException(400, detail={"error": "bad_export"})
        finally:
            Path(tmp.name).unlink(missing_ok=True)

    @app.post("/api/upload/{media_id}")
    async def upload(media_id: str, request: Request, file: UploadFile = File(...), _=Depends(require_write)):
        if not svc.db.one("SELECT 1 FROM media WHERE id=? AND kind='video'", (media_id,)):
            raise HTTPException(400, detail={"error": "unknown_id"})
        ext = Path(file.filename or "").suffix.lower()
        if ext not in (".mp4", ".mov", ".m4v", ".mp3", ".m4a", ".wav", ".aac"):
            raise HTTPException(400, detail={"error": "bad_file"})
        dest = settings.data_dir / "uploads" / f"{_safe(media_id)}{ext}"
        dest.parent.mkdir(parents=True, exist_ok=True)
        with open(dest, "wb") as f:
            shutil.copyfileobj(file.file, f)
        try:
            svc.upload_audio(media_id, dest)
        except RuntimeError:
            raise HTTPException(400, detail={"error": "bad_file"})
        return {"ok": True}

    @app.get("/media/thumb/{media_id}")
    def thumb(media_id: str, _=Depends(require_login)):
        row = svc.db.one("SELECT thumb_path FROM media WHERE id=?", (media_id,))
        if not row or not row["thumb_path"] or not Path(row["thumb_path"]).exists():
            raise HTTPException(404, detail={"error": "no_thumb"})
        return FileResponse(row["thumb_path"], headers={"Cache-Control": "private, max-age=86400"})

    @app.get("/media/audio/{media_id}")
    def audio(media_id: str, _=Depends(require_login)):
        row = svc.db.one("SELECT file_path FROM audio_source WHERE media_id=?", (media_id,))
        if not row or not Path(row["file_path"]).exists():
            raise HTTPException(404, detail={"error": "no_audio"})
        return FileResponse(row["file_path"])

    @app.get("/export.csv")
    def export_csv(request: Request, _=Depends(require_login)):
        lang = lang_of(request)
        buf = io.StringIO()
        w = csv.writer(buf, delimiter=";")
        w.writerow(["id", "permalink", "date", "kind", "state", "state_since", "level", "auto_level",
                    "reasons", "acrcloud", "meta_status", "manual_review", "note"])
        for m in svc.media_list():
            acr = " | ".join(f"{a['artists'] or '?'} - {a['title'] or '?'} [{a['label'] or ''}] {a['score'] or ''}"
                             for a in m["acr"] if a["status"] == "match")
            w.writerow([m["id"], m["permalink"] or "", m["timestamp"] or "", m["kind"], m["state"], m["since"] or "",
                        m["level"], m["auto_level"] or "", json.dumps(m["reasons"], ensure_ascii=False), acr,
                        m["meta"][0]["status"] if m["meta"] else "",
                        (m["review"] or {}).get("verdict", ""), (m["review"] or {}).get("note", "")])
        w.writerow([])
        w.writerow([texts[lang]["disclaimer"]])
        return Response("﻿" + buf.getvalue(), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": "attachment; filename=copyright-radar.csv"})

    @app.get("/i18n/{lang}.json")
    def i18n(lang: str):
        if lang not in LANGS:
            raise HTTPException(404, detail={"error": "no_lang"})
        return texts[lang]

    return app


async def _json(request: Request) -> dict:
    try:
        body = await request.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def app_factory() -> FastAPI:
    return create_app()
