"""ffmpeg / ffprobe helpers. SPEC section 7."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

FRAGMENT_S = 15


def probe(path: Path) -> dict:
    """{'duration': float|None, 'has_audio': bool}"""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type:format=duration", "-of", "json", str(path)],
        capture_output=True, text=True, timeout=60,
    )
    if out.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {out.stderr.strip()[:200]}")
    data = json.loads(out.stdout or "{}")
    streams = data.get("streams", [])
    dur = (data.get("format") or {}).get("duration")
    return {"duration": float(dur) if dur else None, "has_audio": any(s.get("codec_type") == "audio" for s in streams)}


def fragment_starts(duration: float | None) -> list[float]:
    """Start, middle and end; one fragment for short videos."""
    if not duration or duration <= FRAGMENT_S + 5:
        return [0.0]
    starts = [0.0, max(0.0, duration / 2 - FRAGMENT_S / 2), max(0.0, duration - FRAGMENT_S)]
    out: list[float] = []
    for s in starts:
        if all(abs(s - o) >= FRAGMENT_S / 2 for o in out):
            out.append(round(s, 1))
    return out


def cut_fragment(path: Path, start: float, length: int = FRAGMENT_S) -> bytes:
    """Mono 16 kHz MP3 fragment, well below ACRCloud's 5 MB limit."""
    out = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", str(start), "-t", str(length), "-i", str(path),
         "-vn", "-ac", "1", "-ar", "16000", "-b:a", "64k", "-f", "mp3", "pipe:1"],
        capture_output=True, timeout=120,
    )
    if out.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {out.stderr.decode(errors='replace').strip()[:200]}")
    return out.stdout


def thumbnail(path: Path, dest: Path, at_s: float = 1.0) -> bool:
    dest.parent.mkdir(parents=True, exist_ok=True)
    out = subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-ss", str(at_s), "-i", str(path), "-frames:v", "1",
         "-vf", "scale=480:-2", str(dest)],
        capture_output=True, timeout=60,
    )
    if out.returncode != 0 or not dest.exists():
        out = subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(path), "-frames:v", "1",
                              "-vf", "scale=480:-2", str(dest)], capture_output=True, timeout=60)
    return dest.exists()


def silence_mp3(seconds: int = 3) -> bytes:
    """Test sample for the setup wizard (expects 'no result' from ACRCloud)."""
    out = subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", f"anullsrc=r=16000:cl=mono", "-t", str(seconds),
         "-b:a", "32k", "-f", "mp3", "pipe:1"], capture_output=True, timeout=30)
    return out.stdout
