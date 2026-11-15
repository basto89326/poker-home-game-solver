"""App settings from ``config.yaml`` (optional; defaults below)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]


@dataclass
class Config:
    data_root: Path = PROJECT_ROOT
    host: str = "127.0.0.1"
    port: int = 5000
    claude_executable: str = "claude"
    claude_model: str | None = None
    claude_timeout: int = 180


def load_config(path: Path = PROJECT_ROOT / "config.yaml") -> Config:
    """Read settings, falling back to defaults for anything not given."""
    if not path.exists():
        return Config()
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    server = raw.get("server") or {}
    claude = raw.get("claude") or {}
    defaults = Config()
    return Config(
        data_root=PROJECT_ROOT / raw["data_root"] if raw.get("data_root") else defaults.data_root,
        host=server.get("host", defaults.host),
        port=int(server.get("port", defaults.port)),
        claude_executable=claude.get("executable", defaults.claude_executable),
        claude_model=claude.get("model") or defaults.claude_model,
        claude_timeout=int(claude.get("timeout", defaults.claude_timeout)),
    )
