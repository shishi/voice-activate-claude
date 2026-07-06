"""src/vac/config.py"""
from __future__ import annotations

import json
import re
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
    input_device: str | int | None = None
    sounds_enabled: bool = True


# 各フィールドの期待型("number" は bool を除く int/float)
_FIELD_TYPES: dict[str, object] = {
    "wake_model": str,
    "wake_threshold": "number",
    "whisper_model": str,
    "language": str,
    "silence_limit_s": "number",
    "no_speech_timeout_s": "number",
    "max_duration_s": "number",
    "claude_exe_path": str,
    "input_device": "device",
    "sounds_enabled": bool,
}


def load_config(path: Path) -> Config:
    if not path.exists():
        return Config()
    with path.open("rb") as f:
        data = tomllib.load(f)

    known = {f.name for f in fields(Config)}
    unknown = set(data) - known
    if unknown:
        raise ConfigError(f"unknown config keys: {sorted(unknown)}")

    _validate_types(data)
    config = Config(**data)
    _validate(config)
    return config


def _validate_types(data: dict[str, object]) -> None:
    for key, value in data.items():
        expected = _FIELD_TYPES[key]
        if expected == "number":
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ConfigError(f"{key} must be a number, got {value!r}")
        elif expected == "device":
            if isinstance(value, bool) or not isinstance(value, (str, int)):
                raise ConfigError(f"{key} must be a device name (string) or index (int), got {value!r}")
        elif expected is bool:
            if not isinstance(value, bool):
                raise ConfigError(f"{key} must be a boolean, got {value!r}")
        elif not isinstance(value, str):
            raise ConfigError(f"{key} must be a string, got {value!r}")


def _validate(config: Config) -> None:
    if not 0.0 < config.wake_threshold <= 1.0:
        raise ConfigError(f"wake_threshold must be in (0, 1], got {config.wake_threshold}")
    if config.silence_limit_s <= 0:
        raise ConfigError("silence_limit_s must be positive")
    if config.no_speech_timeout_s <= 0:
        raise ConfigError("no_speech_timeout_s must be positive")
    if config.max_duration_s <= config.silence_limit_s:
        raise ConfigError("max_duration_s must exceed silence_limit_s")
    if isinstance(config.input_device, int) and config.input_device < 0:
        raise ConfigError(f"input_device index must be >= 0, got {config.input_device}")


def save_input_device(path: Path, device: str | int) -> None:
    """config.toml の input_device 行を更新(なければ追記)。他の行・コメントは保持する。"""
    value = json.dumps(device) if isinstance(device, str) else str(device)
    new_line = f"input_device = {value}"
    if path.exists():
        lines = path.read_text(encoding="utf-8").splitlines()
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = []
    pattern = re.compile(r"^\s*#?\s*input_device\s*=")
    for i, line in enumerate(lines):
        if pattern.match(line):
            lines[i] = new_line
            break
    else:
        lines.append(new_line)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
