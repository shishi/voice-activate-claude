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
