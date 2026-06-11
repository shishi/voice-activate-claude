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
