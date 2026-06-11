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
