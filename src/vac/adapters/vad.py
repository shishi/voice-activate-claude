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
