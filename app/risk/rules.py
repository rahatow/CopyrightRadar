"""Risk rules: pure functions, no I/O except loading the TOML file.

See docs/SPEC.md, section 8. A risk level is an indicator, not legal advice.
"""
from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

# Worst first. no_audio is special: it short-circuits the other signals.
LEVELS = ["high", "medium", "unknown", "low", "no_music", "no_audio"]
_RANK = {"high": 0, "medium": 1, "unknown": 2, "low": 3, "no_music": 4}


@dataclass
class RiskRules:
    version: str = "default"
    commercial_labels: list[str] = field(default_factory=list)
    safe_sources: list[str] = field(default_factory=list)
    stock_libraries: list[str] = field(default_factory=list)
    licensed_sources: list[str] = field(default_factory=list)
    min_score: int = 70
    unrecognized_level: str = "low"

    @classmethod
    def load(cls, path: str | Path) -> "RiskRules":
        data = tomllib.loads(Path(path).read_text(encoding="utf-8"))
        return cls(**data)


@dataclass
class MetaMatch:
    author: str | None = None
    owner_copyright_policy: str | None = None


@dataclass
class Recognition:
    status: str  # "match" | "no_match" | "error"
    title: str | None = None
    artists: str | None = None
    label: str | None = None
    score: int | None = None
    is_cover: bool = False


@dataclass
class MediaSignals:
    has_audio_stream: bool | None  # None = audio not available to us
    music_detected: bool | None = None
    meta_status: str | None = None  # raw Meta status, kept as is
    meta_matches: list[MetaMatch] = field(default_factory=list)
    recognitions: list[Recognition] = field(default_factory=list)
    manual_verdict: str | None = None  # "confirmed_risk" | "cleared" | "no_music"


@dataclass
class Assessment:
    level: str
    reasons: list[dict]
    rules_version: str
    automatic_level: str


def _contains_any(value: str | None, needles: list[str]) -> bool:
    if not value:
        return False
    # Whole words only: "EMI" must not match "Epidemic Sound".
    return any(re.search(rf"\b{re.escape(n)}\b", value, re.IGNORECASE) for n in needles)


def _worst(levels: list[str]) -> str:
    return min(levels, key=lambda lvl: _RANK[lvl])


def _track(r: Recognition) -> dict:
    return {"title": r.title, "artists": r.artists, "label": r.label, "score": r.score}


def _meta_level(m: MetaMatch, rules: RiskRules) -> tuple[str, dict]:
    src = m.owner_copyright_policy or m.author or "?"
    if _contains_any(m.owner_copyright_policy, rules.safe_sources) or _contains_any(m.author, rules.safe_sources):
        return "low", {"k": "meta_safe", "src": src}
    return "high", {"k": "meta_holder", "src": src}


def _recognition_level(r: Recognition, rules: RiskRules) -> tuple[str, dict]:
    if r.status == "error":
        return "unknown", {"k": "acr_error"}
    if r.status == "no_match":
        return rules.unrecognized_level, {"k": "acr_no_match"}
    t = _track(r)
    if r.score is not None and r.score < rules.min_score:
        return "unknown", {"k": "acr_weak", **t}
    if r.is_cover:
        return "high", {"k": "acr_cover", **t}
    if _contains_any(r.label, rules.licensed_sources):
        return "low", {"k": "acr_licensed", **t}
    if _contains_any(r.label, rules.safe_sources):
        return "low", {"k": "acr_safe", **t}
    if _contains_any(r.label, rules.commercial_labels):
        return "high", {"k": "acr_commercial", **t}
    if _contains_any(r.label, rules.stock_libraries):
        return "medium", {"k": "acr_stock", **t}
    return "medium", {"k": "acr_independent", **t}


def assess(signals: MediaSignals, rules: RiskRules) -> Assessment:
    """Return the risk level and the reasons for one media item.

    Reasons are dicts {"k": <key>, ...}; the UI translates them (DE/EN/RU).
    """
    reasons: list[dict] = []

    if signals.has_audio_stream is False:
        auto = "no_audio"
        reasons.append({"k": "no_audio_track"})
        reasons += [_meta_level(m, rules)[1] for m in signals.meta_matches]
    else:
        levels: list[str] = []
        for m in signals.meta_matches:
            lvl, why = _meta_level(m, rules)
            levels.append(lvl)
            reasons.append(why)
        for r in signals.recognitions:
            lvl, why = _recognition_level(r, rules)
            levels.append(lvl)
            reasons.append(why)
        if signals.music_detected is False and not levels:
            levels.append("no_music")
            reasons.append({"k": "no_music_detected"})
        if not levels and signals.has_audio_stream:
            levels.append("unknown")
            reasons.append({"k": "not_recognized_yet", "meta_status": signals.meta_status})
        elif not levels:
            # No audio for recognition and no Meta match. Meta "no match" never lowers risk.
            levels.append("unknown")
            reasons.append({"k": "no_audio_for_recognition", "meta_status": signals.meta_status})
        elif not signals.meta_matches and signals.meta_status:
            reasons.append({"k": "meta_status", "meta_status": signals.meta_status})
        auto = _worst(levels)

    level = auto
    manual = {"confirmed_risk": "high", "cleared": "low", "no_music": "no_music"}
    if signals.manual_verdict in manual:
        level = manual[signals.manual_verdict]
        reasons.insert(0, {"k": f"manual_{signals.manual_verdict}"})

    return Assessment(level=level, reasons=reasons, rules_version=rules.version, automatic_level=auto)
