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
