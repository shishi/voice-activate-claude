"""src/vac/config.py"""
from __future__ import annotations

import tomllib
from dataclasses import dataclass, fields
from pathlib import Path


class ConfigError(Exception):
    """設定ファイルの内容が不正なときに送出する。"""


@dataclass(frozen=True)
class Config:
    wake_model: str = "hey_jarvis"
    wake_threshold: float = 0.5
    whisper_model: str = "small"
    language: str = "ja"
    silence_limit_s: float = 1.5
    no_speech_timeout_s: float = 5.0
    max_duration_s: float = 30.0
    claude_exe_path: str | None = None
    sounds_enabled: bool = True


def load_config(path: Path) -> Config:
    if not path.exists():
        return Config()
    with path.open("rb") as f:
        data = tomllib.load(f)

    known = {f.name for f in fields(Config)}
    unknown = set(data) - known
    if unknown:
        raise ConfigError(f"unknown config keys: {sorted(unknown)}")

    config = Config(**data)
    _validate(config)
    return config


def _validate(config: Config) -> None:
    if not 0.0 < config.wake_threshold <= 1.0:
        raise ConfigError(f"wake_threshold must be in (0, 1], got {config.wake_threshold}")
    if config.silence_limit_s <= 0:
        raise ConfigError("silence_limit_s must be positive")
    if config.max_duration_s <= config.silence_limit_s:
        raise ConfigError("max_duration_s must exceed silence_limit_s")
