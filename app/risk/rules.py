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
    manual_verdict: str | None = None  # "confirmed_risk" | "cleared"


@dataclass
class Assessment:
    level: str
    reasons: list[str]
    rules_version: str
    automatic_level: str


def _contains_any(value: str | None, needles: list[str]) -> bool:
    if not value:
        return False
    # Whole words only: "EMI" must not match "Epidemic Sound".
    return any(re.search(rf"\b{re.escape(n)}\b", value, re.IGNORECASE) for n in needles)


def _worst(levels: list[str]) -> str:
    return min(levels, key=lambda lvl: _RANK[lvl])


def _meta_level(m: MetaMatch, rules: RiskRules) -> tuple[str, str]:
    src = m.owner_copyright_policy or m.author or "?"
    if _contains_any(m.owner_copyright_policy, rules.safe_sources) or _contains_any(
        m.author, rules.safe_sources
    ):
        return "low", f"Meta: match in safe source ({src})"
    return "high", f"Meta: match with rights holder ({src})"


def _recognition_level(r: Recognition, rules: RiskRules) -> tuple[str, str]:
    if r.status == "error":
        return "unknown", "ACRCloud: recognition error"
    if r.status == "no_match":
        return (
            rules.unrecognized_level,
            "ACRCloud: not recognized; fingerprinting misses covers, remixes "
            "and music under voice - listen to it",
        )
    track = f"{r.artists or '?'} - {r.title or '?'} [{r.label or 'no label'}], score {r.score}"
    if r.score is not None and r.score < rules.min_score:
        return "unknown", f"ACRCloud: weak match ({track})"
    if r.is_cover:
        return "high", f"ACRCloud: cover of a known song ({track})"
    if _contains_any(r.label, rules.licensed_sources):
        return "low", f"ACRCloud: source licensed by the studio ({track})"
    if _contains_any(r.label, rules.safe_sources):
        return "low", f"ACRCloud: safe source ({track})"
    if _contains_any(r.label, rules.commercial_labels):
        return "high", f"ACRCloud: commercial label ({track})"
    if _contains_any(r.label, rules.stock_libraries):
        return "medium", f"ACRCloud: stock library without licence on file ({track})"
    return "medium", f"ACRCloud: independent or unknown label ({track})"


def assess(signals: MediaSignals, rules: RiskRules) -> Assessment:
    """Return the risk level and the reasons for one media item."""
    reasons: list[str] = []

    if signals.has_audio_stream is False:
        auto = "no_audio"
        reasons.append("Video has no audio track")
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
            reasons.append("No music detected")
        if not levels:
            # No audio for recognition and no Meta match. Meta "no match" never lowers risk.
            levels.append("unknown")
            reasons.append(
                f"No audio for recognition; Meta status: {signals.meta_status or 'not available'}"
            )
        auto = _worst(levels)

    level = auto
    if signals.manual_verdict == "confirmed_risk":
        level = "high"
        reasons.insert(0, "Manual review: risk confirmed")
    elif signals.manual_verdict == "cleared":
        level = "low"
        reasons.insert(0, "Manual review: checked, cleared")

    return Assessment(level=level, reasons=reasons, rules_version=rules.version, automatic_level=auto)
