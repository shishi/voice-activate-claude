# voice-activate-claude 実装プラン

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 「hey claude」と話しかけると、発話を日本語テキスト化してWindowsのClaude Desktopに注入・自動送信する常駐アプリを作る。

**Architecture:** ヘキサゴナル縮小版。コアの `Orchestrator`(状態機械)と `Endpointer`(終話判定)は純粋ロジックで、`ports.py` のProtocol越しにアダプタを使う。WSL2でユニットTDD、Windows依存アダプタは `python -m vac.check <サブコマンド>` で実機検証する。

**Tech Stack:** Python 3.11+ / uv / pytest / sounddevice / openwakeword / silero-vad / faster-whisper / pywinauto / pystray

**Spec:** `docs/superpowers/specs/2026-06-11-voice-activate-claude-design.md`

**重要な環境前提:**
- 開発・ユニットテストは WSL2 で行う(`uv run pytest`)
- Windows専用ライブラリ(pywinauto等)は `pyproject.toml` で `sys_platform == 'win32'` マーカーを付け、WSL2の `uv sync` を壊さない
- Windows実機での動作確認はタスク内の「Windows検証」ステップに従う(実行できない環境ではスキップし、E2Eチェックリストに委ねる)

---

## Milestone A: コアロジック(WSL2でTDD)

### Task 1: プロジェクト雛形

**Files:**
- Create: `pyproject.toml`
- Create: `src/vac/__init__.py`
- Create: `tests/__init__.py`
- Create: `.gitignore`

- [ ] **Step 1: pyproject.toml を作成**

```toml
[project]
name = "voice-activate-claude"
version = "0.1.0"
description = "Voice-activate Claude Desktop on Windows with a wake word"
requires-python = ">=3.11"
dependencies = [
    "numpy>=1.26",
    "sounddevice>=0.4.6; sys_platform == 'win32'",
    "openwakeword>=0.6.0; sys_platform == 'win32'",
    "faster-whisper>=1.0.0; sys_platform == 'win32'",
    "pywinauto>=0.6.8; sys_platform == 'win32'",
    "pystray>=0.19.5; sys_platform == 'win32'",
    "pillow>=10.0; sys_platform == 'win32'",
]

[dependency-groups]
dev = ["pytest>=8.0"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/vac"]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

注: silero-vad は openwakeword に同梱のVADではなく独立利用するため、実装タスク(Task 10)で `onnxruntime` ベースの `silero-vad` パッケージを追加する。コアのテストには不要なのでここでは入れない。

- [ ] **Step 2: パッケージの空ファイルと .gitignore を作成**

`src/vac/__init__.py` と `tests/__init__.py` は空ファイル。

`.gitignore`:
```
__pycache__/
*.pyc
.venv/
.pytest_cache/
dist/
*.egg-info/
```

- [ ] **Step 3: 依存を解決してpytestが動くことを確認**

Run: `uv sync && uv run pytest`
Expected: `no tests ran` (exit code 5 でOK。テストゼロでも環境が壊れていないことの確認)

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml uv.lock src/ tests/ .gitignore
git commit -m "chore: scaffold Python project with uv (Windows deps behind platform markers)"
```

---

### Task 2: Config(TOML読み込みとバリデーション)

**Files:**
- Create: `src/vac/config.py`
- Test: `tests/test_config.py`

- [ ] **Step 1: 失敗するテストを書く**

```python
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
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `uv run pytest tests/test_config.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'vac.config'`)

- [ ] **Step 3: 最小実装を書く**

```python
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
```

- [ ] **Step 4: テストが通ることを確認**

Run: `uv run pytest tests/test_config.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add src/vac/config.py tests/test_config.py
git commit -m "feat: add TOML config loading with validation and typo detection"
```

---

### Task 3: ポート定義とドメイン型

**Files:**
- Create: `src/vac/ports.py`

純粋な宣言のみでロジックがないため、このタスクはTDD対象外(後続タスクのテストが実質の検証になる)。

- [ ] **Step 1: ports.py を作成**

```python
"""src/vac/ports.py

コア(Orchestrator/Endpointer)が依存するインターフェース定義。
アダプタはこれらのProtocolを満たす。音声は 16kHz / mono / int16、
1フレーム = 1280 サンプル(80ms)を前提とする。
"""
from __future__ import annotations

from enum import Enum, auto
from typing import Protocol

import numpy as np

SAMPLE_RATE = 16_000
FRAME_SAMPLES = 1_280  # 80ms
FRAME_DURATION_S = FRAME_SAMPLES / SAMPLE_RATE


class Feedback(Enum):
    LISTENING = auto()   # 録音開始♪
    DELIVERED = auto()   # 送信完了♪
    ERROR = auto()       # 失敗♪


class DeliveryError(Exception):
    """Claude Desktopへのテキスト注入に失敗したときに送出する。"""


class AudioSource(Protocol):
    def read_frame(self) -> np.ndarray:
        """次の80msフレームを返す(ブロッキング)。shape=(1280,), dtype=int16"""
        ...


class WakeDetector(Protocol):
    def score(self, frame: np.ndarray) -> float:
        """フレームを与えてウェイクワードスコア(0.0-1.0)を返す。"""
        ...

    def reset(self) -> None:
        """内部バッファをクリアする(録音フェーズ後の再武装用)。"""
        ...


class SpeechDetector(Protocol):
    def is_speech(self, frame: np.ndarray) -> bool:
        """フレームに人の発話が含まれるかを返す。"""
        ...


class Transcriber(Protocol):
    def transcribe(self, audio: np.ndarray) -> str:
        """連結した発話音声をテキスト化する。無音なら空文字を返す。"""
        ...


class PromptDeliverer(Protocol):
    def deliver(self, text: str) -> None:
        """Claude Desktopへテキストを注入し送信する。失敗時はDeliveryError。"""
        ...


class FeedbackPlayer(Protocol):
    def play(self, event: Feedback) -> None: ...
```

- [ ] **Step 2: 全テストが通ったまま(構造変更なし)であることを確認**

Run: `uv run pytest`
Expected: PASS (5 passed — Task 2 のテストのみ)

- [ ] **Step 3: Commit**

```bash
git add src/vac/ports.py
git commit -m "feat: define core ports and audio frame contract"
```

### Task 4: Endpointer(終話判定)

**Files:**
- Create: `src/vac/endpointer.py`
- Test: `tests/test_endpointer.py`

仕様(specセクション4): 発話開始後に1.5秒の無音で終了。発話が一度もないまま5秒で「無音終了」。全体で30秒到達なら強制終了。

- [ ] **Step 1: 失敗するテストを書く**

```python
"""tests/test_endpointer.py"""
from vac.endpointer import Endpointer, Verdict

FRAME_S = 0.08  # 80ms


def make(silence_limit_s=1.5, no_speech_timeout_s=5.0, max_duration_s=30.0):
    return Endpointer(
        silence_limit_s=silence_limit_s,
        no_speech_timeout_s=no_speech_timeout_s,
        max_duration_s=max_duration_s,
        frame_duration_s=FRAME_S,
    )


def feed_n(ep, is_speech: bool, n: int) -> Verdict:
    verdict = Verdict.CONTINUE
    for _ in range(n):
        verdict = ep.feed(is_speech)
        if verdict is not Verdict.CONTINUE:
            return verdict
    return verdict


def test_continues_while_speaking():
    ep = make()
    assert feed_n(ep, True, 50) is Verdict.CONTINUE


def test_completes_after_silence_following_speech():
    ep = make(silence_limit_s=1.5)
    feed_n(ep, True, 10)            # 0.8秒 喋る
    # 1.5秒 = 18.75フレーム → 19フレーム目の無音で完了
    assert feed_n(ep, False, 18) is Verdict.CONTINUE
    assert ep.feed(False) is Verdict.COMPLETE


def test_speech_resets_silence_counter():
    ep = make(silence_limit_s=1.5)
    feed_n(ep, True, 5)
    feed_n(ep, False, 10)           # 0.8秒の沈黙(まだ継続)
    ep.feed(True)                   # 再び話し出す
    # 沈黙カウンタはリセットされているので、また19フレーム必要
    assert feed_n(ep, False, 18) is Verdict.CONTINUE
    assert ep.feed(False) is Verdict.COMPLETE


def test_no_speech_times_out():
    ep = make(no_speech_timeout_s=5.0)
    # 5.0秒 = 62.5フレーム → 63フレーム目で発話なしタイムアウト
    assert feed_n(ep, False, 62) is Verdict.CONTINUE
    assert ep.feed(False) is Verdict.NO_SPEECH


def test_max_duration_forces_completion():
    ep = make(max_duration_s=30.0)
    # 喋り続けても 30秒 = 375フレームで強制完了
    assert feed_n(ep, True, 374) is Verdict.CONTINUE
    assert ep.feed(True) is Verdict.COMPLETE
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `uv run pytest tests/test_endpointer.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'vac.endpointer'`)

- [ ] **Step 3: 最小実装を書く**

```python
"""src/vac/endpointer.py"""
from __future__ import annotations

from enum import Enum, auto


class Verdict(Enum):
    CONTINUE = auto()
    COMPLETE = auto()   # 発話あり→録音をTranscriberへ
    NO_SPEECH = auto()  # 一度も発話なし→何も送らない


class Endpointer:
    """フレーム単位の発話有無から録音の終端を判定する。

    時間はフレーム数で数える(実時間に依存しないためテスト可能)。
    """

    def __init__(
        self,
        silence_limit_s: float,
        no_speech_timeout_s: float,
        max_duration_s: float,
        frame_duration_s: float,
    ) -> None:
        self._silence_limit = silence_limit_s / frame_duration_s
        self._no_speech_timeout = no_speech_timeout_s / frame_duration_s
        self._max_duration = max_duration_s / frame_duration_s
        self._total_frames = 0
        self._silence_frames = 0
        self._heard_speech = False

    def feed(self, is_speech: bool) -> Verdict:
        self._total_frames += 1
        if is_speech:
            self._heard_speech = True
            self._silence_frames = 0
        else:
            self._silence_frames += 1

        if self._total_frames >= self._max_duration:
            return Verdict.COMPLETE if self._heard_speech else Verdict.NO_SPEECH
        if self._heard_speech:
            if self._silence_frames > self._silence_limit:
                return Verdict.COMPLETE
        elif self._total_frames > self._no_speech_timeout:
            return Verdict.NO_SPEECH
        return Verdict.CONTINUE
```

- [ ] **Step 4: テストが通ることを確認**

Run: `uv run pytest tests/test_endpointer.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: 全テストを回して Commit**

Run: `uv run pytest`
Expected: PASS (10 passed)

```bash
git add src/vac/endpointer.py tests/test_endpointer.py
git commit -m "feat: add frame-based endpointer for end-of-utterance detection"
```

---

### Task 5: Orchestrator(状態機械)

**Files:**
- Create: `src/vac/orchestrator.py`
- Test: `tests/test_orchestrator.py`

`run_once()` が「ウェイク待ち→録音→変換→注入」の1サイクルを実行する。テストは全ポートのフェイクで駆動。

- [ ] **Step 1: フェイクと正常系テストを書く**

```python
"""tests/test_orchestrator.py"""
import logging

import numpy as np
import pytest

from vac.config import Config
from vac.orchestrator import Orchestrator
from vac.ports import DeliveryError, Feedback, FRAME_SAMPLES


def frame(value: int = 0) -> np.ndarray:
    return np.full(FRAME_SAMPLES, value, dtype=np.int16)


class FakeAudio:
    """毎回同じフレームを返す。値に意味はなく、フレーム数だけが進む。"""

    def read_frame(self) -> np.ndarray:
        return frame(1)


class FakeWake:
    """score() の呼び出し回数が wake_at に達したら閾値超えを返す。"""

    def __init__(self, wake_at: int = 3):
        self.wake_at = wake_at
        self.calls = 0
        self.reset_count = 0

    def score(self, f) -> float:
        self.calls += 1
        return 0.9 if self.calls >= self.wake_at else 0.0

    def reset(self) -> None:
        self.reset_count += 1


class FakeVad:
    """speech_frames 回だけ True を返し、その後 False(沈黙)を返す。"""

    def __init__(self, speech_frames: int = 10):
        self.remaining = speech_frames

    def is_speech(self, f) -> bool:
        if self.remaining > 0:
            self.remaining -= 1
            return True
        return False


class FakeTranscriber:
    def __init__(self, text: str = "こんにちは"):
        self.text = text
        self.received: np.ndarray | None = None

    def transcribe(self, audio) -> str:
        self.received = audio
        return self.text


class FakeDeliverer:
    def __init__(self, error: Exception | None = None):
        self.error = error
        self.delivered: list[str] = []

    def deliver(self, text: str) -> None:
        if self.error:
            raise self.error
        self.delivered.append(text)


class FakeFeedback:
    def __init__(self):
        self.events: list[Feedback] = []

    def play(self, event: Feedback) -> None:
        self.events.append(event)


def build(wake=None, vad=None, transcriber=None, deliverer=None, feedback=None):
    feedback = feedback or FakeFeedback()
    orchestrator = Orchestrator(
        audio=FakeAudio(),
        wake=wake or FakeWake(),
        vad=vad or FakeVad(),
        transcriber=transcriber or FakeTranscriber(),
        deliverer=deliverer or FakeDeliverer(),
        feedback=feedback,
        config=Config(),
    )
    return orchestrator, feedback


def test_happy_path_delivers_transcript():
    deliverer = FakeDeliverer()
    orchestrator, feedback = build(deliverer=deliverer)
    orchestrator.run_once()
    assert deliverer.delivered == ["こんにちは"]
    assert feedback.events == [Feedback.LISTENING, Feedback.DELIVERED]


def test_recorded_audio_is_passed_to_transcriber():
    transcriber = FakeTranscriber()
    orchestrator, _ = build(transcriber=transcriber, vad=FakeVad(speech_frames=10))
    orchestrator.run_once()
    # 発話10フレーム+終端判定までの沈黙フレームが連結されている
    assert transcriber.received is not None
    assert transcriber.received.ndim == 1
    assert len(transcriber.received) >= 10 * FRAME_SAMPLES


def test_no_speech_sends_nothing_and_plays_error():
    deliverer = FakeDeliverer()
    orchestrator, feedback = build(vad=FakeVad(speech_frames=0), deliverer=deliverer)
    orchestrator.run_once()
    assert deliverer.delivered == []
    assert feedback.events == [Feedback.LISTENING, Feedback.ERROR]


def test_empty_transcript_sends_nothing_and_plays_error():
    deliverer = FakeDeliverer()
    orchestrator, feedback = build(
        transcriber=FakeTranscriber(text="  "), deliverer=deliverer
    )
    orchestrator.run_once()
    assert deliverer.delivered == []
    assert feedback.events == [Feedback.LISTENING, Feedback.ERROR]


def test_delivery_failure_plays_error_and_logs_transcript(caplog):
    orchestrator, feedback = build(
        deliverer=FakeDeliverer(error=DeliveryError("input not found"))
    )
    with caplog.at_level(logging.ERROR):
        orchestrator.run_once()
    assert feedback.events == [Feedback.LISTENING, Feedback.ERROR]
    # 発話内容を消失させない(specセクション5)
    assert "こんにちは" in caplog.text


def test_delivery_failure_does_not_propagate():
    orchestrator, _ = build(deliverer=FakeDeliverer(error=DeliveryError("boom")))
    orchestrator.run_once()  # 例外が漏れなければOK


def test_wake_detector_reset_after_cycle():
    wake = FakeWake()
    orchestrator, _ = build(wake=wake)
    orchestrator.run_once()
    assert wake.reset_count == 1
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `uv run pytest tests/test_orchestrator.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'vac.orchestrator'`)

- [ ] **Step 3: 最小実装を書く**

```python
"""src/vac/orchestrator.py"""
from __future__ import annotations

import logging

import numpy as np

from vac.config import Config
from vac.endpointer import Endpointer, Verdict
from vac.ports import (
    FRAME_DURATION_S,
    AudioSource,
    DeliveryError,
    Feedback,
    FeedbackPlayer,
    PromptDeliverer,
    SpeechDetector,
    Transcriber,
    WakeDetector,
)

logger = logging.getLogger(__name__)


class Orchestrator:
    """IDLE → RECORDING → TRANSCRIBING → DELIVERING → IDLE の1サイクルを駆動する。"""

    def __init__(
        self,
        audio: AudioSource,
        wake: WakeDetector,
        vad: SpeechDetector,
        transcriber: Transcriber,
        deliverer: PromptDeliverer,
        feedback: FeedbackPlayer,
        config: Config,
    ) -> None:
        self._audio = audio
        self._wake = wake
        self._vad = vad
        self._transcriber = transcriber
        self._deliverer = deliverer
        self._feedback = feedback
        self._config = config

    def run_forever(self) -> None:
        while True:
            try:
                self.run_once()
            except Exception:
                # 常駐アプリは死なない(specセクション5)
                logger.exception("unexpected error; returning to idle")
                self._feedback.play(Feedback.ERROR)

    def run_once(self) -> None:
        self._wait_for_wake()
        self._feedback.play(Feedback.LISTENING)
        audio, verdict = self._record_command()
        try:
            if verdict is Verdict.NO_SPEECH:
                logger.info("no speech detected; nothing sent")
                self._feedback.play(Feedback.ERROR)
                return
            text = self._transcriber.transcribe(audio).strip()
            if not text:
                logger.info("empty transcript; nothing sent")
                self._feedback.play(Feedback.ERROR)
                return
            try:
                self._deliverer.deliver(text)
            except DeliveryError:
                logger.exception("delivery failed; transcript was: %s", text)
                self._feedback.play(Feedback.ERROR)
                return
            self._feedback.play(Feedback.DELIVERED)
        finally:
            self._wake.reset()

    def _wait_for_wake(self) -> None:
        while True:
            frame = self._audio.read_frame()
            if self._wake.score(frame) >= self._config.wake_threshold:
                return

    def _record_command(self) -> tuple[np.ndarray, Verdict]:
        endpointer = Endpointer(
            silence_limit_s=self._config.silence_limit_s,
            no_speech_timeout_s=self._config.no_speech_timeout_s,
            max_duration_s=self._config.max_duration_s,
            frame_duration_s=FRAME_DURATION_S,
        )
        frames: list[np.ndarray] = []
        while True:
            frame = self._audio.read_frame()
            frames.append(frame)
            verdict = endpointer.feed(self._vad.is_speech(frame))
            if verdict is not Verdict.CONTINUE:
                return np.concatenate(frames), verdict
```

- [ ] **Step 4: テストが通ることを確認**

Run: `uv run pytest tests/test_orchestrator.py -v`
Expected: PASS (7 passed)

- [ ] **Step 5: 全テストを回して Commit**

Run: `uv run pytest`
Expected: PASS (17 passed)

```bash
git add src/vac/orchestrator.py tests/test_orchestrator.py
git commit -m "feat: add orchestrator state machine with crash-proof error paths"
```

### Task 6: ClipboardGuard(退避→復元)

**Files:**
- Create: `src/vac/clipboard.py`
- Test: `tests/test_clipboard.py`

クリップボード操作自体はポート(`ClipboardPort`)に切り出し、「退避して書き換え、必ず復元する」というロジックだけをテストする。

- [ ] **Step 1: 失敗するテストを書く**

```python
"""tests/test_clipboard.py"""
import pytest

from vac.clipboard import ClipboardGuard


class FakeClipboard:
    def __init__(self, initial: str | None = "before"):
        self.content = initial

    def get_text(self) -> str | None:
        return self.content

    def set_text(self, text: str) -> None:
        self.content = text

    def clear(self) -> None:
        self.content = None


def test_sets_text_inside_context():
    clipboard = FakeClipboard()
    with ClipboardGuard(clipboard, "新しいテキスト"):
        assert clipboard.content == "新しいテキスト"


def test_restores_previous_text_on_exit():
    clipboard = FakeClipboard(initial="before")
    with ClipboardGuard(clipboard, "x"):
        pass
    assert clipboard.content == "before"


def test_restores_even_when_body_raises():
    clipboard = FakeClipboard(initial="before")
    with pytest.raises(RuntimeError):
        with ClipboardGuard(clipboard, "x"):
            raise RuntimeError("paste failed")
    assert clipboard.content == "before"


def test_clears_clipboard_when_it_was_empty():
    clipboard = FakeClipboard(initial=None)
    with ClipboardGuard(clipboard, "x"):
        assert clipboard.content == "x"
    assert clipboard.content is None
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `uv run pytest tests/test_clipboard.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'vac.clipboard'`)

- [ ] **Step 3: 最小実装を書く**

```python
"""src/vac/clipboard.py"""
from __future__ import annotations

from typing import Protocol


class ClipboardPort(Protocol):
    def get_text(self) -> str | None: ...
    def set_text(self, text: str) -> None: ...
    def clear(self) -> None: ...


class ClipboardGuard:
    """クリップボードに一時テキストを置き、抜けるとき必ず元に戻す。"""

    def __init__(self, clipboard: ClipboardPort, text: str) -> None:
        self._clipboard = clipboard
        self._text = text
        self._saved: str | None = None

    def __enter__(self) -> "ClipboardGuard":
        self._saved = self._clipboard.get_text()
        self._clipboard.set_text(self._text)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._saved is None:
            self._clipboard.clear()
        else:
            self._clipboard.set_text(self._saved)
```

- [ ] **Step 4: テストが通ることを確認**

Run: `uv run pytest tests/test_clipboard.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: 全テストを回して Commit**

Run: `uv run pytest`
Expected: PASS (21 passed)

```bash
git add src/vac/clipboard.py tests/test_clipboard.py
git commit -m "feat: add clipboard guard that always restores prior contents"
```

---

## Milestone B: Windowsアダプタと配線

以降のタスクはWindows専用コード。WSL2ではユニットテスト対象外(import すらしない)。
各タスクの「Windows検証」ステップは、Windows側に `uv` と本リポジトリのクローンがあり
`uv sync` 済みであることが前提。Windowsで実行できない場合はスキップ可とし、
Task 14 のE2Eチェックリストで最終確認する。

**アダプタ共通の構造規約:** Windows依存モジュールは `src/vac/adapters/` 配下に置き、
モジュールのトップレベルでWindows専用パッケージをimportしてよい(コア側からは
`tray.py` / `check.py` のみがimportする)。

### Task 7: 効果音アダプタと診断CLIの骨格

**Files:**
- Create: `src/vac/adapters/__init__.py`(空)
- Create: `src/vac/adapters/sound.py`
- Create: `src/vac/check.py`
- Create: `src/vac/__main__.py`

- [ ] **Step 1: sound.py を作成**

```python
"""src/vac/adapters/sound.py — winsoundによる効果音(Windows専用)"""
from __future__ import annotations

import winsound

from vac.ports import Feedback

_TONES: dict[Feedback, tuple[int, int]] = {
    # (周波数Hz, 長さms)
    Feedback.LISTENING: (880, 150),
    Feedback.DELIVERED: (1320, 150),
    Feedback.ERROR: (330, 400),
}


class WinSoundPlayer:
    def __init__(self, enabled: bool = True) -> None:
        self._enabled = enabled

    def play(self, event: Feedback) -> None:
        if not self._enabled:
            return
        freq, duration = _TONES[event]
        winsound.Beep(freq, duration)
```

- [ ] **Step 2: check.py(診断CLIの骨格+soundサブコマンド)を作成**

```python
"""src/vac/check.py — アダプタ単体の実機診断CLI

usage: python -m vac.check <subcommand>
subcommands は実装が進むたびに増える。
"""
from __future__ import annotations

import argparse
import sys


def check_sound(args: argparse.Namespace) -> int:
    from vac.adapters.sound import WinSoundPlayer
    from vac.ports import Feedback

    player = WinSoundPlayer()
    for event in (Feedback.LISTENING, Feedback.DELIVERED, Feedback.ERROR):
        print(f"playing {event.name} ...")
        player.play(event)
    print("OK: 3種類のビープが聞こえたら成功")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m vac.check")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("sound", help="効果音を順に再生する").set_defaults(func=check_sound)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
```

`src/vac/__main__.py`:

```python
"""`python -m vac` はトレイ常駐起動(Task 13で実装)。それまではガイドのみ。"""
print("entry point not implemented yet; use `python -m vac.check <subcommand>`")
```

- [ ] **Step 3: WSL2で全テストが壊れていないことを確認**

Run: `uv run pytest`
Expected: PASS (21 passed — Windows専用importはテストから参照されないため影響なし)

- [ ] **Step 4: Windows検証**

Windows側で: `uv run python -m vac.check sound`
Expected: 高→より高→低 の3音が鳴り、`OK: ...` が表示される

- [ ] **Step 5: Commit**

```bash
git add src/vac/adapters/ src/vac/check.py src/vac/__main__.py
git commit -m "feat: add winsound feedback adapter and diagnostic CLI skeleton"
```

---

### Task 8: マイクアダプタ

**Files:**
- Create: `src/vac/adapters/mic.py`
- Modify: `src/vac/check.py`(micサブコマンド追加)

- [ ] **Step 1: mic.py を作成**

```python
"""src/vac/adapters/mic.py — sounddeviceによるマイク入力(Windows専用)"""
from __future__ import annotations

import queue

import numpy as np
import sounddevice as sd

from vac.ports import FRAME_SAMPLES, SAMPLE_RATE


class SoundDeviceAudioSource:
    """既定の入力デバイスから80msフレームを供給する。

    コールバックスレッドからキュー経由で受け渡す(read_frameはブロッキング)。
    デバイス消失時はsounddeviceが例外を投げるため、上位(tray.py)が
    再接続リトライを行う(specセクション5)。
    """

    def __init__(self) -> None:
        self._queue: queue.Queue[np.ndarray] = queue.Queue(maxsize=100)
        self._stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype="int16",
            blocksize=FRAME_SAMPLES,
            callback=self._on_audio,
        )

    def _on_audio(self, indata, frames, time, status) -> None:
        if status:
            # オーバーフロー等はログより先に取りこぼし防止を優先し黙って続行
            pass
        try:
            self._queue.put_nowait(indata[:, 0].copy())
        except queue.Full:
            pass  # 下流が詰まっているときは古い音声を諦める

    def __enter__(self) -> "SoundDeviceAudioSource":
        self._stream.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self._stream.stop()
        self._stream.close()

    def read_frame(self) -> np.ndarray:
        return self._queue.get()
```

- [ ] **Step 2: check.py に mic サブコマンドを追加**

`check.py` の `check_sound` の後に追加:

```python
def check_mic(args: argparse.Namespace) -> int:
    import numpy as np

    from vac.adapters.mic import SoundDeviceAudioSource
    from vac.ports import SAMPLE_RATE

    seconds = 3
    print(f"{seconds}秒間レベルメーターを表示します。話しかけてください...")
    with SoundDeviceAudioSource() as source:
        frames = int(seconds / 0.08)
        for _ in range(frames):
            frame = source.read_frame()
            level = int(np.abs(frame).mean() / 300)
            print("#" * min(level, 60))
    print("OK: 声に反応して棒が伸びていれば成功")
    return 0
```

`main()` のサブコマンド登録に追加:

```python
    sub.add_parser("mic", help="マイク入力レベルを表示する").set_defaults(func=check_mic)
```

- [ ] **Step 3: WSL2で全テスト確認**

Run: `uv run pytest`
Expected: PASS (21 passed)

- [ ] **Step 4: Windows検証**

Windows側で: `uv run python -m vac.check mic`
Expected: 無音時はほぼ空行、話すと `#` の棒が伸びる

- [ ] **Step 5: Commit**

```bash
git add src/vac/adapters/mic.py src/vac/check.py
git commit -m "feat: add sounddevice mic adapter with 80ms frame queue"
```

### Task 9: ウェイクワードアダプタ

**Files:**
- Create: `src/vac/adapters/wakeword.py`
- Modify: `src/vac/check.py`(wakeサブコマンド追加)

- [ ] **Step 1: wakeword.py を作成**

```python
"""src/vac/adapters/wakeword.py — openwakewordによる検知(Windows専用)"""
from __future__ import annotations

import numpy as np
from openwakeword.model import Model


class OpenWakeWordDetector:
    """openwakewordのラッパー。

    model: 同梱モデル名("hey_jarvis"等)または .onnx/.tflite のパス。
    初回はモデルダウンロードが必要: `python -m vac.check wake` 実行時に
    openwakeword.utils.download_models() を呼ぶ。
    """

    def __init__(self, model: str) -> None:
        self._model_key = model
        self._model = Model(wakeword_models=[model])

    def score(self, frame: np.ndarray) -> float:
        predictions = self._model.predict(frame)
        # predict はモデル名→スコアの辞書を返す。単一モデルなので最大値でよい
        return max(predictions.values())

    def reset(self) -> None:
        self._model.reset()
```

- [ ] **Step 2: check.py に wake サブコマンドを追加**

```python
def check_wake(args: argparse.Namespace) -> int:
    import openwakeword.utils

    from vac.adapters.mic import SoundDeviceAudioSource
    from vac.adapters.wakeword import OpenWakeWordDetector

    openwakeword.utils.download_models()
    detector = OpenWakeWordDetector(model=args.model)
    print(f"モデル {args.model} で待機中。ウェイクワードを話してください (Ctrl+Cで終了)")
    with SoundDeviceAudioSource() as source:
        while True:
            score = detector.score(source.read_frame())
            if score > 0.2:
                print(f"score={score:.2f}" + ("  <<< WAKE!" if score >= 0.5 else ""))
    return 0
```

`main()` のサブコマンド登録に追加:

```python
    wake_parser = sub.add_parser("wake", help="ウェイクワード検知を試す")
    wake_parser.add_argument("--model", default="hey_jarvis")
    wake_parser.set_defaults(func=check_wake)
```

- [ ] **Step 3: WSL2で全テスト確認**

Run: `uv run pytest`
Expected: PASS (21 passed)

- [ ] **Step 4: Windows検証**

Windows側で: `uv run python -m vac.check wake`
Expected: 「hey jarvis」と話すと `score=0.xx <<< WAKE!` が出る。雑談では出ない

- [ ] **Step 5: Commit**

```bash
git add src/vac/adapters/wakeword.py src/vac/check.py
git commit -m "feat: add openwakeword detector adapter"
```

---

### Task 10: VADアダプタ

**Files:**
- Modify: `pyproject.toml`(silero-vad依存を追加)
- Create: `src/vac/adapters/vad.py`
- Modify: `src/vac/check.py`(vadサブコマンド追加)

- [ ] **Step 1: 依存を追加**

`pyproject.toml` の `dependencies` に追加(openwakewordがonnxruntimeを既に引き込むため追加コスト小):

```toml
    "silero-vad>=5.1; sys_platform == 'win32'",
    "torch>=2.0; sys_platform == 'win32'",
```

注: `silero-vad` パッケージはPyTorch版が必要。`uv sync` がWindows側で重い場合は
代替として `onnxruntime` 直接利用に切り替えてよい(その判断は実装時にWindowsで
`uv sync` の所要時間を見て行い、切り替えたらこのプランに追記する)。

Run(Windows側): `uv sync`
Expected: 成功

- [ ] **Step 2: vad.py を作成**

```python
"""src/vac/adapters/vad.py — Silero VADによる発話検知(Windows専用)"""
from __future__ import annotations

import numpy as np
import torch
from silero_vad import load_silero_vad

from vac.ports import SAMPLE_RATE


class SileroSpeechDetector:
    """80msフレーム(1280サンプル)を512サンプル窓に分割して判定する。

    Silero VAD v5 は 16kHz で512サンプル固定入力。フレーム内のどこかの窓が
    閾値を超えたら「発話あり」とする。
    """

    WINDOW = 512

    def __init__(self, threshold: float = 0.5) -> None:
        self._model = load_silero_vad()
        self._threshold = threshold

    def is_speech(self, frame: np.ndarray) -> bool:
        # int16 → float32 [-1, 1]
        audio = frame.astype(np.float32) / 32768.0
        for start in range(0, len(audio) - self.WINDOW + 1, self.WINDOW):
            window = torch.from_numpy(audio[start : start + self.WINDOW])
            prob = self._model(window, SAMPLE_RATE).item()
            if prob >= self._threshold:
                return True
        return False
```

- [ ] **Step 3: check.py に vad サブコマンドを追加**

```python
def check_vad(args: argparse.Namespace) -> int:
    from vac.adapters.mic import SoundDeviceAudioSource
    from vac.adapters.vad import SileroSpeechDetector

    detector = SileroSpeechDetector()
    print("VAD監視中。話すと SPEECH、黙ると silence (Ctrl+Cで終了)")
    with SoundDeviceAudioSource() as source:
        while True:
            label = "SPEECH" if detector.is_speech(source.read_frame()) else "silence"
            print(label)
    return 0
```

`main()` のサブコマンド登録に追加:

```python
    sub.add_parser("vad", help="発話検知を試す").set_defaults(func=check_vad)
```

- [ ] **Step 4: WSL2で全テスト確認**

Run: `uv run pytest`
Expected: PASS (21 passed)

- [ ] **Step 5: Windows検証**

Windows側で: `uv run python -m vac.check vad`
Expected: 話している間だけ `SPEECH` が連続表示される

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock src/vac/adapters/vad.py src/vac/check.py
git commit -m "feat: add Silero VAD speech detector adapter"
```

---

### Task 11: Whisperアダプタ

**Files:**
- Create: `src/vac/adapters/whisper.py`
- Modify: `src/vac/check.py`(whisperサブコマンド追加)

- [ ] **Step 1: whisper.py を作成**

```python
"""src/vac/adapters/whisper.py — faster-whisperによる文字起こし(Windows専用)"""
from __future__ import annotations

import numpy as np
from faster_whisper import WhisperModel


class FasterWhisperTranscriber:
    def __init__(self, model_size: str = "small", language: str = "ja") -> None:
        # CPU前提。GPUがあれば device="cuda" を設定で切り替えられるよう拡張余地あり
        self._model = WhisperModel(model_size, device="cpu", compute_type="int8")
        self._language = language

    def transcribe(self, audio: np.ndarray) -> str:
        # int16 → float32 [-1, 1](faster-whisperの想定形式)
        samples = audio.astype(np.float32) / 32768.0
        segments, _info = self._model.transcribe(samples, language=self._language)
        return "".join(segment.text for segment in segments).strip()
```

- [ ] **Step 2: check.py に whisper サブコマンドを追加**

```python
def check_whisper(args: argparse.Namespace) -> int:
    import numpy as np

    from vac.adapters.mic import SoundDeviceAudioSource
    from vac.adapters.whisper import FasterWhisperTranscriber

    seconds = 5
    print("モデルをロード中(初回はダウンロードあり)...")
    transcriber = FasterWhisperTranscriber()
    print(f"{seconds}秒間録音します。日本語で話してください...")
    frames = []
    with SoundDeviceAudioSource() as source:
        for _ in range(int(seconds / 0.08)):
            frames.append(source.read_frame())
    text = transcriber.transcribe(np.concatenate(frames))
    print(f"認識結果: {text}")
    print("OK: 話した内容と概ね一致していれば成功")
    return 0
```

`main()` のサブコマンド登録に追加:

```python
    sub.add_parser("whisper", help="5秒録音して文字起こしする").set_defaults(func=check_whisper)
```

- [ ] **Step 3: WSL2で全テスト確認**

Run: `uv run pytest`
Expected: PASS (21 passed)

- [ ] **Step 4: Windows検証**

Windows側で: `uv run python -m vac.check whisper`
Expected: 話した日本語がテキストで表示される(多少の誤字は許容)

- [ ] **Step 5: Commit**

```bash
git add src/vac/adapters/whisper.py src/vac/check.py
git commit -m "feat: add faster-whisper transcriber adapter"
```

### Task 12: Claude Desktopドライバ(最重要・最大リスク)

**Files:**
- Create: `src/vac/adapters/claude_driver.py`
- Modify: `src/vac/check.py`(injectサブコマンド追加)

specセクション4の注入手順そのもの。UIA ValuePatternがElectronで効かない可能性が高いため、クリップボード+Ctrl+Vを対等なフォールバックとして実装する。**ここが実機検証の最優先項目**(specセクション10)。

- [ ] **Step 1: claude_driver.py を作成**

```python
"""src/vac/adapters/claude_driver.py — Claude Desktopへのテキスト注入(Windows専用)"""
from __future__ import annotations

import logging
import subprocess
import time
from pathlib import Path

import win32clipboard  # pywinautoが依存するpywin32に同梱
from pywinauto import Application, Desktop
from pywinauto.findwindows import ElementNotFoundError
from pywinauto.keyboard import send_keys

from vac.clipboard import ClipboardGuard
from vac.ports import DeliveryError

logger = logging.getLogger(__name__)

WINDOW_TITLE_RE = r"^Claude(\s.*)?$"
LAUNCH_TIMEOUT_S = 15.0
DEFAULT_EXE_CANDIDATES = [
    # 標準的なインストール先。実機で `where claude` 等で確認して必要なら追加する
    Path.home() / "AppData/Local/AnthropicClaude/claude.exe",
    Path.home() / "AppData/Local/Programs/claude-desktop/Claude.exe",
]


class Win32Clipboard:
    """ClipboardPort のWin32実装(テキストのみ扱う)。"""

    def get_text(self) -> str | None:
        win32clipboard.OpenClipboard()
        try:
            if not win32clipboard.IsClipboardFormatAvailable(
                win32clipboard.CF_UNICODETEXT
            ):
                return None
            return win32clipboard.GetClipboardData(win32clipboard.CF_UNICODETEXT)
        finally:
            win32clipboard.CloseClipboard()

    def set_text(self, text: str) -> None:
        win32clipboard.OpenClipboard()
        try:
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardData(win32clipboard.CF_UNICODETEXT, text)
        finally:
            win32clipboard.CloseClipboard()

    def clear(self) -> None:
        win32clipboard.OpenClipboard()
        try:
            win32clipboard.EmptyClipboard()
        finally:
            win32clipboard.CloseClipboard()


class ClaudeDesktopDriver:
    def __init__(self, exe_path: str | None = None) -> None:
        self._exe_path = exe_path
        self._clipboard = Win32Clipboard()

    def deliver(self, text: str) -> None:
        try:
            window = self._find_window()
            if window is None:
                self._launch()
                window = self._wait_for_window()
            window.set_focus()
            self._inject(window, text)
            send_keys("{ENTER}")
        except DeliveryError:
            raise
        except Exception as exc:
            raise DeliveryError(str(exc)) from exc

    def _find_window(self):
        try:
            return Desktop(backend="uia").window(title_re=WINDOW_TITLE_RE)
        except ElementNotFoundError:
            return None

    def _launch(self) -> None:
        candidates = (
            [Path(self._exe_path)] if self._exe_path else DEFAULT_EXE_CANDIDATES
        )
        for exe in candidates:
            if exe.exists():
                subprocess.Popen([str(exe)])
                return
        raise DeliveryError(f"claude.exe not found in: {candidates}")

    def _wait_for_window(self):
        deadline = time.monotonic() + LAUNCH_TIMEOUT_S
        while time.monotonic() < deadline:
            window = self._find_window()
            if window is not None and window.exists():
                return window
            time.sleep(0.5)
        raise DeliveryError(f"window did not appear within {LAUNCH_TIMEOUT_S}s")

    def _inject(self, window, text: str) -> None:
        # 経路1: UIAで入力欄(Edit/Documentコントロール)を探しValuePatternで設定
        try:
            edit = window.child_window(control_type="Edit", found_index=0)
            edit.set_focus()
            edit.set_edit_text(text)  # ValuePattern相当
            return
        except Exception:
            logger.info("UIA ValuePattern injection failed; falling back to clipboard")
        # 経路2: クリップボード+Ctrl+V(contenteditable対策の本命フォールバック)
        with ClipboardGuard(self._clipboard, text):
            send_keys("^v")
            time.sleep(0.3)  # 貼り付け完了を待ってからクリップボードを復元
```

- [ ] **Step 2: check.py に inject サブコマンドを追加**

```python
def check_inject(args: argparse.Namespace) -> int:
    from vac.adapters.claude_driver import ClaudeDesktopDriver

    driver = ClaudeDesktopDriver(exe_path=args.exe)
    print(f"Claude Desktopに注入します: {args.text!r}")
    driver.deliver(args.text)
    print("OK: Claude Desktopにテキストが送信されていれば成功")
    return 0
```

`main()` のサブコマンド登録に追加:

```python
    inject_parser = sub.add_parser("inject", help="Claude Desktopにテキストを送る")
    inject_parser.add_argument("text")
    inject_parser.add_argument("--exe", default=None, help="claude.exe のパス")
    inject_parser.set_defaults(func=check_inject)
```

- [ ] **Step 3: WSL2で全テスト確認**

Run: `uv run pytest`
Expected: PASS (21 passed)

- [ ] **Step 4: Windows検証(最優先の不確実性をここで潰す)**

Windows側で順に:
1. Claude Desktop起動済みの状態で `uv run python -m vac.check inject "テスト送信です"`
   Expected: 入力欄にテキストが入りEnterで送信される
2. Claude Desktopを終了した状態で同コマンド
   Expected: アプリが起動し、ウィンドウ出現後に送信される
3. 送信後にメモ帳等で Ctrl+V
   Expected: 注入前のクリップボード内容が貼り付く(復元の確認)

**検証で判明した実機の差異(ウィンドウタイトル、exeパス、Editコントロールの有無、ValuePattern成否)は、このファイルの定数とspecセクション10に必ず追記すること。**

- [ ] **Step 5: Commit**

```bash
git add src/vac/adapters/claude_driver.py src/vac/check.py
git commit -m "feat: add Claude Desktop driver with UIA injection and clipboard fallback"
```

---

### Task 13: トレイ常駐エントリポイント

**Files:**
- Create: `src/vac/tray.py`
- Modify: `src/vac/__main__.py`
- Create: `config.example.toml`

- [ ] **Step 1: tray.py を作成**

```python
"""src/vac/tray.py — 常駐エントリポイント(Windows専用)

構成: メインスレッドでpystrayのトレイアイコン、ワーカースレッドで
Orchestrator.run_forever() を回す。マイク消失時は5秒間隔で再接続する
(specセクション5)。
"""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

import pystray
from PIL import Image, ImageDraw

from vac.config import Config, load_config
from vac.orchestrator import Orchestrator

logger = logging.getLogger(__name__)

CONFIG_PATH = Path.home() / ".config" / "voice-activate-claude" / "config.toml"
LOG_PATH = Path.home() / ".config" / "voice-activate-claude" / "vac.log"
MIC_RETRY_INTERVAL_S = 5.0


def build_orchestrator(config: Config, audio) -> Orchestrator:
    from vac.adapters.claude_driver import ClaudeDesktopDriver
    from vac.adapters.sound import WinSoundPlayer
    from vac.adapters.vad import SileroSpeechDetector
    from vac.adapters.wakeword import OpenWakeWordDetector
    from vac.adapters.whisper import FasterWhisperTranscriber

    return Orchestrator(
        audio=audio,
        wake=OpenWakeWordDetector(model=config.wake_model),
        vad=SileroSpeechDetector(),
        transcriber=FasterWhisperTranscriber(
            model_size=config.whisper_model, language=config.language
        ),
        deliverer=ClaudeDesktopDriver(exe_path=config.claude_exe_path),
        feedback=WinSoundPlayer(enabled=config.sounds_enabled),
        config=config,
    )


def worker(config: Config, stop: threading.Event) -> None:
    from vac.adapters.mic import SoundDeviceAudioSource

    while not stop.is_set():
        try:
            with SoundDeviceAudioSource() as audio:
                orchestrator = build_orchestrator(config, audio)
                orchestrator.run_forever()
        except Exception:
            logger.exception(
                "audio pipeline crashed; retrying in %ss", MIC_RETRY_INTERVAL_S
            )
            time.sleep(MIC_RETRY_INTERVAL_S)


def make_icon_image() -> Image.Image:
    image = Image.new("RGB", (64, 64), "#222222")
    draw = ImageDraw.Draw(image)
    draw.ellipse((20, 12, 44, 40), fill="#44ccff")   # マイクのヘッド
    draw.rectangle((29, 40, 35, 52), fill="#44ccff")  # マイクの柄
    return image


def main() -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=LOG_PATH,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    config = load_config(CONFIG_PATH)
    stop = threading.Event()
    thread = threading.Thread(target=worker, args=(config, stop), daemon=True)
    thread.start()

    def on_quit(icon, item) -> None:
        stop.set()
        icon.stop()

    icon = pystray.Icon(
        "voice-activate-claude",
        make_icon_image(),
        "voice-activate-claude",
        menu=pystray.Menu(pystray.MenuItem("終了", on_quit)),
    )
    icon.run()


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: __main__.py を差し替え**

```python
"""src/vac/__main__.py"""
from vac.tray import main

main()
```

- [ ] **Step 3: config.example.toml を作成**

```toml
# ~/.config/voice-activate-claude/config.toml にコピーして使う。
# 全キー省略可。以下はデフォルト値。

wake_model = "hey_jarvis"        # openwakeword同梱名 または .onnxパス
wake_threshold = 0.5             # 誤検知が多ければ上げる(0〜1)
whisper_model = "small"          # tiny/base/small/medium/large-v3
language = "ja"
silence_limit_s = 1.5            # この秒数黙ると喋り終わり判定
no_speech_timeout_s = 5.0        # ウェイク後この秒数無言ならキャンセル
max_duration_s = 30.0            # 1回の発話の最大録音時間
# claude_exe_path = 'C:\Users\you\AppData\Local\AnthropicClaude\claude.exe'
sounds_enabled = true
```

- [ ] **Step 4: WSL2で全テスト確認**

Run: `uv run pytest`
Expected: PASS (21 passed)

- [ ] **Step 5: Windows検証**

Windows側で: `uv run python -m vac`
Expected: トレイにアイコンが出る。「hey jarvis」→開始音→話す→Claude Desktopに送信→完了音。トレイメニューの「終了」で停止

- [ ] **Step 6: Commit**

```bash
git add src/vac/tray.py src/vac/__main__.py config.example.toml
git commit -m "feat: add tray entry point wiring all adapters with mic retry loop"
```

### Task 14: E2E手動チェックリスト

**Files:**
- Create: `docs/e2e-checklist.md`
- Create: `README.md`

- [ ] **Step 1: docs/e2e-checklist.md を作成**

```markdown
# E2E 手動チェックリスト(Windows実機)

前提: `uv sync` 済み、`uv run python -m vac` で常駐中。

## 正常系
- [ ] 「hey jarvis」(またはカスタムワード)で開始音が鳴る
- [ ] 日本語で話し、黙って約1.5秒で録音が終わる
- [ ] Claude Desktop(起動済み)に認識テキストが入力され自動送信される
- [ ] 完了音が鳴り、続けてもう一度ウェイクワードに反応する
- [ ] Claude Desktopを終了した状態でも、ウェイク→発話でアプリが起動し送信される

## 異常系
- [ ] ウェイク後に何も話さない → 約5秒でエラー音、何も送信されない
- [ ] ウェイク後に雑音のみ → エラー音、何も送信されない(送信されたら閾値を調整)
- [ ] 送信前にクリップボードへコピーした内容が、送信後も Ctrl+V で貼り付けられる
- [ ] USBマイクを抜く → vac.log にエラーが記録され、挿し直すと自動復帰する
- [ ] テレビ・音楽を流した状態で30分放置 → 誤送信が起きない(起きたら wake_threshold を上げる)

## ログ確認
- [ ] `~/.config/voice-activate-claude/vac.log` に各サイクルのINFOログが残っている
- [ ] 注入失敗時、認識テキストがERRORログに残っている
```

- [ ] **Step 2: README.md を作成**

```markdown
# voice-activate-claude

「hey claude」と話しかけるだけで、WindowsのClaude Desktopに音声で指示を送る常駐アプリ。

## 仕組み

ウェイクワード検知(openWakeWord)→ 発話録音(Silero VADで終話判定)→
文字起こし(faster-whisper, ローカル)→ Claude Desktopへ注入・自動送信(UI Automation)。
すべてローカル処理で、音声が外部に送られることはない。

## セットアップ(Windows)

1. [uv](https://docs.astral.sh/uv/) をインストール
2. `git clone` してリポジトリ直下で `uv sync`
3. (任意)`config.example.toml` を `~/.config/voice-activate-claude/config.toml` にコピーして調整
4. `uv run python -m vac` で常駐開始(タスクトレイにアイコンが出る)

## 動作確認

各コンポーネントを単体で診断できる:

    uv run python -m vac.check sound    # 効果音
    uv run python -m vac.check mic      # マイク入力
    uv run python -m vac.check wake     # ウェイクワード検知
    uv run python -m vac.check vad      # 発話検知
    uv run python -m vac.check whisper  # 文字起こし
    uv run python -m vac.check inject "テスト"  # Claude Desktopへの注入

## 開発

コアロジックはWSL2/Linuxでもテストできる: `uv run pytest`

設計: `docs/superpowers/specs/2026-06-11-voice-activate-claude-design.md`
```

- [ ] **Step 3: 全テスト確認と Commit**

Run: `uv run pytest`
Expected: PASS (21 passed)

```bash
git add docs/e2e-checklist.md README.md
git commit -m "docs: add E2E manual checklist and README"
```

---

## Milestone C: カスタムウェイクワード「hey claude」

### Task 15: カスタムモデルの学習と切り替え

**Files:**
- Create: `docs/wake-model-training.md`
- Create: `models/hey_claude.onnx`(学習成果物)

openWakeWord公式の学習パイプライン(合成音声で訓練データを自動生成)を使う、一回きりの手作業。コード変更は不要 — Task 2 の設定機構で `wake_model` をパスに変えるだけで切り替わる。

- [ ] **Step 1: 学習手順を docs/wake-model-training.md に記録しながら実行**

手順(実行時に公式READMEの最新手順を必ず確認すること):

1. openWakeWord公式リポジトリの「Training New Models」ノートブック
   (https://github.com/dscripka/openWakeWord 参照)をGoogle Colabで開く
2. ターゲットフレーズに `hey claude` を指定して学習を実行
   (合成音声生成→特徴抽出→分類器学習まで自動)
3. 出力された `hey_claude.onnx` をダウンロードし `models/` に配置
4. 実際に試した手順・所要時間・ハマりどころを `docs/wake-model-training.md` に記録

- [ ] **Step 2: Windows実機で精度を検証**

Windows側で: `uv run python -m vac.check wake --model models/hey_claude.onnx`
Expected: 「hey claude」で `<<< WAKE!`、雑談・テレビ音声では反応しない

検知が弱い/誤検知が多い場合: configの `wake_threshold` を調整して再検証。
それでも実用に耐えない場合は学習パラメータ(データ量・エポック)を変えて再学習。
2回の再学習でもダメなら「hey jarvis」運用に戻し、specセクション10に記録して停止。

- [ ] **Step 3: デフォルト設定を切り替え**

`config.example.toml` の該当行を更新:

```toml
wake_model = "models/hey_claude.onnx"
```

`src/vac/config.py` のデフォルトも更新:

```python
    wake_model: str = "models/hey_claude.onnx"
```

対応するテスト `tests/test_config.py::test_defaults` の期待値も更新:

```python
    assert config.wake_model == "models/hey_claude.onnx"
```

- [ ] **Step 4: 全テスト確認と Commit**

Run: `uv run pytest`
Expected: PASS (21 passed)

```bash
git add models/hey_claude.onnx docs/wake-model-training.md config.example.toml src/vac/config.py tests/test_config.py
git commit -m "feat: switch default wake word to custom hey-claude model"
```

- [ ] **Step 5: E2Eチェックリストを「hey claude」で全項目再実行**

`docs/e2e-checklist.md` の全項目をカスタムモデルで確認。全部通ったら完成🎉

---

## 進め方メモ

- タスクは番号順に実行する(Task 7以降は前のタスクの`check.py`に追記していくため順序依存)
- 各タスク完了時にCodexレビューゲート(CLAUDE.md)の条件に該当すればレビューを挟む
- Windows検証ステップが実行できない環境では「未検証」とコミットメッセージに残し、Task 14 のE2Eでまとめて検証する
- Task 12 の実機検証で設計の前提(UIAツリー構造)が崩れた場合は、specセクション10を更新してから実装を直す





