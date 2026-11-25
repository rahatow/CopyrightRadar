"""Runtime settings from environment variables."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@dataclass
class Settings:
    data_dir: Path
    rules_path: Path
    demo: bool = False
    meta_pause_s: float = 0.8
    refresh_cooldown_s: int = 60
    max_missing_ratio: float = 0.25

    @classmethod
    def from_env(cls) -> "Settings":
        data_dir = Path(os.environ.get("DATA_DIR", ROOT / "data"))
        custom = ROOT / "config" / "risk_rules.toml"
        rules = Path(os.environ.get("RULES_PATH", custom if custom.exists() else ROOT / "config" / "risk_rules.example.toml"))
        return cls(
            data_dir=data_dir,
            rules_path=rules,
            demo=os.environ.get("DEMO", "") == "1",
            meta_pause_s=float(os.environ.get("META_PAUSE_S", "0.8")),
        )
