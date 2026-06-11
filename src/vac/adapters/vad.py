"""src/vac/adapters/vad.py — Silero VADによる発話検知(Windows専用)"""
from __future__ import annotations

import numpy as np
import torch
from silero_vad import load_silero_vad

from vac.ports import SAMPLE_RATE


class SileroSpeechDetector:
    """80msフレーム(1280サンプル)を512サンプル窓に分割して判定する。

    Silero VAD (v5以降) は 16kHz で512サンプル固定入力。フレーム末尾の半端な
    サンプルは内部バッファに持ち越し、次フレームと連結して全サンプルを漏れなく
    判定する。いずれかの窓が閾値を超えたら「発話あり」とする。
    """

    WINDOW = 512

    def __init__(self, threshold: float = 0.5) -> None:
        self._model = load_silero_vad()
        self._threshold = threshold
        self._buffer = np.empty(0, dtype=np.float32)

    def is_speech(self, frame: np.ndarray) -> bool:
        # int16 → float32 [-1, 1]。フレーム末尾の半端分は持ち越して次フレームと連結する
        audio = frame.astype(np.float32) / 32768.0
        self._buffer = np.concatenate([self._buffer, audio])
        speech = False
        while len(self._buffer) >= self.WINDOW:
            window = torch.from_numpy(self._buffer[: self.WINDOW])
            self._buffer = self._buffer[self.WINDOW :]
            prob = self._model(window, SAMPLE_RATE).item()
            if prob >= self._threshold:
                speech = True
        return speech

    def reset(self) -> None:
        self._model.reset_states()
        self._buffer = np.empty(0, dtype=np.float32)
