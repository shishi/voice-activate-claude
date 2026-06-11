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


def test_wake_reset_even_when_recording_raises():
    class ExplodingAudio:
        def __init__(self):
            self.calls = 0

        def read_frame(self):
            self.calls += 1
            if self.calls > 3:  # ウェイク検知後の録音フェーズで死ぬ
                raise RuntimeError("mic died")
            return frame(1)

    wake = FakeWake(wake_at=3)
    orchestrator = Orchestrator(
        audio=ExplodingAudio(),
        wake=wake,
        vad=FakeVad(),
        transcriber=FakeTranscriber(),
        deliverer=FakeDeliverer(),
        feedback=FakeFeedback(),
        config=Config(),
    )
    with pytest.raises(RuntimeError):
        orchestrator.run_once()
    assert wake.reset_count == 1


class ExplodingFeedback:
    """play() が常に失敗する(スピーカー死亡など)。"""

    def play(self, event):
        raise OSError("audio device unavailable")


def test_feedback_failure_does_not_break_cycle():
    deliverer = FakeDeliverer()
    wake = FakeWake()
    orchestrator = Orchestrator(
        audio=FakeAudio(),
        wake=wake,
        vad=FakeVad(),
        transcriber=FakeTranscriber(),
        deliverer=deliverer,
        feedback=ExplodingFeedback(),
        config=Config(),
    )
    orchestrator.run_once()  # 例外が漏れないこと
    assert deliverer.delivered == ["こんにちは"]
    assert wake.reset_count == 1


def test_run_forever_survives_error_in_error_handling():
    class DyingAudio:
        """1サイクル目はRuntimeError、2サイクル目はKeyboardInterruptで脱出。"""

        def __init__(self):
            self.calls = 0

        def read_frame(self):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("mic died")
            raise KeyboardInterrupt  # except Exception では捕まらない脱出口

    orchestrator = Orchestrator(
        audio=DyingAudio(),
        wake=FakeWake(),
        vad=FakeVad(),
        transcriber=FakeTranscriber(),
        deliverer=FakeDeliverer(),
        feedback=ExplodingFeedback(),
        config=Config(),
    )
    # 1サイクル目: RuntimeError → except節 → ERROR再生が失敗しても死なずに次のループへ
    # 2サイクル目: KeyboardInterrupt で脱出 = ループが生き延びた証拠
    with pytest.raises(KeyboardInterrupt):
        orchestrator.run_forever()
