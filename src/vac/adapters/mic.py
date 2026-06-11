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
        # 正常なら80msごとにフレームが届くため、2秒無音はストリーム死と判断。
        # RuntimeErrorは上位の包括catchに届き、ERROR音→ソース再生成につながる。
        try:
            return self._queue.get(timeout=2.0)
        except queue.Empty:
            raise RuntimeError("audio stream stalled; no frames for 2s") from None
