"""src/vac/endpointer.py"""
from __future__ import annotations

import math
from enum import Enum, auto


class Verdict(Enum):
    CONTINUE = auto()
    COMPLETE = auto()   # 発話あり→録音をTranscriberへ
    NO_SPEECH = auto()  # 一度も発話なし→何も送らない


def _frames_exceeding(seconds: float, frame_duration_s: float) -> int:
    """「seconds を超えた」と見なす最小フレーム数(浮動小数の誤差を吸収)。"""
    return math.floor(round(seconds / frame_duration_s, 6)) + 1


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
        self._silence_frames_needed = _frames_exceeding(silence_limit_s, frame_duration_s)
        self._no_speech_frames_needed = _frames_exceeding(no_speech_timeout_s, frame_duration_s)
        self._max_frames = math.ceil(round(max_duration_s / frame_duration_s, 6))
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

        if self._total_frames >= self._max_frames:
            return Verdict.COMPLETE if self._heard_speech else Verdict.NO_SPEECH
        if self._heard_speech:
            if self._silence_frames >= self._silence_frames_needed:
                return Verdict.COMPLETE
        elif self._total_frames >= self._no_speech_frames_needed:
            return Verdict.NO_SPEECH
        return Verdict.CONTINUE
