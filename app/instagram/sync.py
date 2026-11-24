"""«Актуализировать»: compare the Meta media list with the database.

Pure decision function; see docs/SPEC.md, section 10. Nothing is written when
the guards fail.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SyncDecision:
    outcome: str  # "apply" | "abort" | "needs_confirmation"
    to_missing: list[str] = field(default_factory=list)
    to_in_feed: list[str] = field(default_factory=list)
    reason: str = ""
    missing_ratio: float = 0.0


def decide_sync(
    in_feed_ids: set[str],
    missing_ids: set[str],
    fetched: list[dict],
    complete: bool,
    confirmed: bool = False,
    max_missing_ratio: float = 0.25,
) -> SyncDecision:
    if not complete:
        return SyncDecision("abort", reason="Meta response is incomplete")
    if any(not item.get("permalink") for item in fetched):
        return SyncDecision("abort", reason="A fetched post has no permalink")

    fetched_ids = {str(item["id"]) for item in fetched}
    to_missing = sorted(in_feed_ids - fetched_ids)
    to_in_feed = sorted(missing_ids & fetched_ids)
    ratio = len(to_missing) / len(in_feed_ids) if in_feed_ids else 0.0

    if ratio > max_missing_ratio and not confirmed:
        return SyncDecision(
            "needs_confirmation", to_missing, to_in_feed,
            reason=f"{len(to_missing)} of {len(in_feed_ids)} posts disappeared", missing_ratio=ratio,
        )
    return SyncDecision("apply", to_missing, to_in_feed, missing_ratio=ratio)
