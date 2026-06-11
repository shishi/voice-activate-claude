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
