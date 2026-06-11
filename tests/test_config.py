"""tests/test_config.py"""
from pathlib import Path

import pytest

from vac.config import Config, ConfigError, load_config


def test_load_config_returns_defaults_when_file_missing(tmp_path: Path):
    config = load_config(tmp_path / "missing.toml")
    assert config == Config()


def test_defaults():
    config = Config()
    assert config.wake_model == "hey_jarvis"
    assert config.wake_threshold == 0.5
    assert config.whisper_model == "small"
    assert config.language == "ja"
    assert config.silence_limit_s == 1.5
    assert config.no_speech_timeout_s == 5.0
    assert config.max_duration_s == 30.0
    assert config.claude_exe_path is None
    assert config.sounds_enabled is True


def test_load_config_overrides_from_toml(tmp_path: Path):
    path = tmp_path / "config.toml"
    path.write_text(
        """
wake_model = "models/hey_claude.onnx"
wake_threshold = 0.7
whisper_model = "medium"
sounds_enabled = false
""",
        encoding="utf-8",
    )
    config = load_config(path)
    assert config.wake_model == "models/hey_claude.onnx"
    assert config.wake_threshold == 0.7
    assert config.whisper_model == "medium"
    assert config.sounds_enabled is False
    # 未指定キーはデフォルトのまま
    assert config.language == "ja"


def test_load_config_rejects_unknown_key(tmp_path: Path):
    path = tmp_path / "config.toml"
    path.write_text('wake_treshold = 0.7\n', encoding="utf-8")  # typo
    with pytest.raises(ConfigError, match="wake_treshold"):
        load_config(path)


def test_load_config_rejects_out_of_range_threshold(tmp_path: Path):
    path = tmp_path / "config.toml"
    path.write_text("wake_threshold = 1.5\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="wake_threshold"):
        load_config(path)
