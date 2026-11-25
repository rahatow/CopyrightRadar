"""Import an Instagram data export ("Download your information", ZIP, JSON format). SPEC section 3.

Gives audio for Reels the API will not let us download (copyright-flagged videos have no
media_url) and shows posts that are already in the Instagram archive.

The export layout changes over time. The parser is tolerant: it reads every JSON file whose
name contains posts / reels / archived, walks it for objects with "uri" and
"creation_timestamp", and repairs the double-encoded UTF-8 that exports contain.
TODO: verify on a fresh real export.
"""
from __future__ import annotations

import hashlib
import json
import re
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

VIDEO_EXT = {".mp4", ".mov", ".m4v"}
PHOTO_EXT = {".jpg", ".jpeg", ".png", ".webp", ".heic"}
MATCH_TOLERANCE_S = 600


def repair_text(s: str | None) -> str | None:
    """'RÃ¼ckenhaare' -> 'Rückenhaare' (exports store UTF-8 bytes as Latin-1 code points)."""
    if not s:
        return s
    try:
        fixed = s.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return s
    return fixed


@dataclass
class ExportItem:
    uri: str
    kind: str  # video | photo
    timestamp: int | None
    caption: str | None
    source: str  # posts | reels | archived
    local_path: Path | None = None


@dataclass
class ImportResult:
    items: list[ExportItem] = field(default_factory=list)
    skipped: int = 0


def _category(name: str) -> str | None:
    n = PurePosixPath(name).name.lower()
    if not n.endswith(".json"):
        return None
    if "archived" in n:
        return "archived"
    if "reels" in n:
        return "reels"
    if re.match(r"posts_\d+\.json$", n) or n == "posts.json":
        return "posts"
    return None


def _walk(node, caption: str | None, out: list[tuple[str, int | None, str | None]]):
    if isinstance(node, dict):
        cap = node.get("title") if isinstance(node.get("title"), str) and node.get("title") else caption
        uri = node.get("uri")
        if isinstance(uri, str) and ("creation_timestamp" in node or "media" not in node):
            ts = node.get("creation_timestamp")
            out.append((uri, int(ts) if isinstance(ts, (int, float)) else None, cap))
        for v in node.values():
            _walk(v, cap, out)
    elif isinstance(node, list):
        for v in node:
            _walk(v, caption, out)


def read_export(zip_path: Path, extract_to: Path) -> ImportResult:
    res = ImportResult()
    seen: set[str] = set()
    extract_to = extract_to.resolve()
    with zipfile.ZipFile(zip_path) as z:
        names = set(z.namelist())
        for name in sorted(names):
            cat = _category(name)
            if not cat:
                continue
            try:
                data = json.loads(z.read(name).decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                res.skipped += 1
                continue
            found: list[tuple[str, int | None, str | None]] = []
            _walk(data, None, found)
            for uri, ts, cap in found:
                ext = PurePosixPath(uri).suffix.lower()
                kind = "video" if ext in VIDEO_EXT else "photo" if ext in PHOTO_EXT else None
                if not kind:
                    continue
                key = f"{cat}:{uri}"
                if key in seen:
                    continue
                seen.add(key)
                item = ExportItem(uri=uri, kind=kind, timestamp=ts, caption=repair_text(cap), source=cat)
                member = next((n for n in (uri, uri.lstrip("/")) if n in names), None)
                if member is None:  # exports put media next to a top-level folder
                    member = next((n for n in names if n.endswith("/" + uri.lstrip("/"))), None)
                if member:
                    dest = (extract_to / hashlib.sha1(member.encode()).hexdigest()[:16]).with_suffix(ext)
                    if not str(dest.resolve()).startswith(str(extract_to)):
                        continue  # zip-slip guard (should not happen with hashed names)
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    with z.open(member) as src, open(dest, "wb") as out:
                        while chunk := src.read(1 << 20):
                            out.write(chunk)
                    item.local_path = dest
                res.items.append(item)
    return res


def _epoch(iso: str | None) -> float | None:
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00").replace("+0000", "+00:00")).timestamp()
    except ValueError:
        return None


def match_items(items: list[ExportItem], api_media: list[dict]) -> dict[int, str]:
    """Map export item index -> API media id by kind and nearest timestamp (± 10 min, one-to-one)."""
    pairs = []
    for i, it in enumerate(items):
        if it.timestamp is None or it.source == "archived":
            continue
        for m in api_media:
            if m["kind"] != it.kind:
                continue
            t = _epoch(m["timestamp"])
            if t is not None and abs(t - it.timestamp) <= MATCH_TOLERANCE_S:
                pairs.append((abs(t - it.timestamp), i, m["id"]))
    pairs.sort()
    used_i, used_m, out = set(), set(), {}
    for _, i, mid in pairs:
        if i in used_i or mid in used_m:
            continue
        used_i.add(i)
        used_m.add(mid)
        out[i] = mid
    return out


def export_id(item: ExportItem) -> str:
    return "export:" + hashlib.sha1(item.uri.encode()).hexdigest()[:16]


def iso_from_epoch(ts: int | None) -> str | None:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat() if ts else None
