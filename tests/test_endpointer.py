"""tests/test_endpointer.py"""
from vac.endpointer import Endpointer, Verdict

FRAME_S = 0.08  # 80ms


def make(silence_limit_s=1.5, no_speech_timeout_s=5.0, max_duration_s=30.0):
    return Endpointer(
        silence_limit_s=silence_limit_s,
        no_speech_timeout_s=no_speech_timeout_s,
        max_duration_s=max_duration_s,
        frame_duration_s=FRAME_S,
    )


def feed_n(ep, is_speech: bool, n: int) -> Verdict:
    verdict = Verdict.CONTINUE
    for _ in range(n):
        verdict = ep.feed(is_speech)
        if verdict is not Verdict.CONTINUE:
            return verdict
    return verdict


def test_continues_while_speaking():
    ep = make()
    assert feed_n(ep, True, 50) is Verdict.CONTINUE


def test_completes_after_silence_following_speech():
    ep = make(silence_limit_s=1.5)
    feed_n(ep, True, 10)            # 0.8秒 喋る
    # 1.5秒 = 18.75フレーム → 19フレーム目の無音で完了
    assert feed_n(ep, False, 18) is Verdict.CONTINUE
    assert ep.feed(False) is Verdict.COMPLETE


def test_speech_resets_silence_counter():
    ep = make(silence_limit_s=1.5)
    feed_n(ep, True, 5)
    feed_n(ep, False, 10)           # 0.8秒の沈黙(まだ継続)
    ep.feed(True)                   # 再び話し出す
    # 沈黙カウンタはリセットされているので、また19フレーム必要
    assert feed_n(ep, False, 18) is Verdict.CONTINUE
    assert ep.feed(False) is Verdict.COMPLETE


def test_no_speech_times_out():
    ep = make(no_speech_timeout_s=5.0)
    # 5.0秒 = 62.5フレーム → 63フレーム目で発話なしタイムアウト
    assert feed_n(ep, False, 62) is Verdict.CONTINUE
    assert ep.feed(False) is Verdict.NO_SPEECH


def test_max_duration_forces_completion():
    ep = make(max_duration_s=30.0)
    # 喋り続けても 30秒 = 375フレームで強制完了
    assert feed_n(ep, True, 374) is Verdict.CONTINUE
    assert ep.feed(True) is Verdict.COMPLETE
