"""src/vac/check.py — アダプタ単体の実機診断CLI

usage: python -m vac.check <subcommand>
subcommands は実装が進むたびに増える。
"""
from __future__ import annotations

import argparse
import sys


def _parse_device(value):
    # 数字(符号付き含む)ならindex、それ以外は名前(部分一致)として扱う
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return value


def check_sound(args: argparse.Namespace) -> int:
    from vac.adapters.sound import WinSoundPlayer
    from vac.ports import Feedback

    player = WinSoundPlayer()
    for event in (Feedback.LISTENING, Feedback.DELIVERED, Feedback.ERROR):
        print(f"playing {event.name} ...")
        player.play(event)
    print("OK: 3種類のビープが聞こえたら成功")
    return 0


def check_mic(args: argparse.Namespace) -> int:
    import numpy as np

    from vac.adapters.mic import SoundDeviceAudioSource
    from vac.ports import FRAME_DURATION_S

    seconds = 3
    print(f"{seconds}秒間レベルメーターを表示します。話しかけてください...")
    with SoundDeviceAudioSource(device=_parse_device(args.device)) as source:
        frames = int(seconds / FRAME_DURATION_S)
        for _ in range(frames):
            frame = source.read_frame()
            # 平均振幅をそのまま数値で出し、棒も鳴らす。speechの平均は数十〜数百なので
            # /10 でちょうど見やすい本数になる(無音は数値もほぼ0で一目で分かる)。
            mean = int(np.abs(frame).mean())
            print(f"{mean:5d} " + "#" * min(mean // 10, 60))
    print("OK: 声に反応して数値と棒が伸びていれば成功")
    return 0


def check_wake(args: argparse.Namespace) -> int:
    import openwakeword.utils

    from vac.adapters.mic import SoundDeviceAudioSource
    from vac.adapters.wakeword import OpenWakeWordDetector

    openwakeword.utils.download_models()
    detector = OpenWakeWordDetector(model=args.model)
    print(f"モデル {args.model} で待機中。ウェイクワードを話してください (Ctrl+Cで終了)")
    try:
        with SoundDeviceAudioSource(device=_parse_device(args.device)) as source:
            while True:
                score = detector.score(source.read_frame())
                if score > 0.2:
                    print(f"score={score:.2f}" + ("  <<< WAKE!" if score >= 0.5 else ""))
    except KeyboardInterrupt:
        pass
    return 0


def check_vad(args: argparse.Namespace) -> int:
    from vac.adapters.mic import SoundDeviceAudioSource
    from vac.adapters.vad import SileroSpeechDetector

    detector = SileroSpeechDetector()
    print("VAD監視中。話すと SPEECH、黙ると silence (Ctrl+Cで終了)")
    try:
        with SoundDeviceAudioSource(device=_parse_device(args.device)) as source:
            while True:
                label = "SPEECH" if detector.is_speech(source.read_frame()) else "silence"
                print(label)
    except KeyboardInterrupt:
        pass
    return 0


def check_whisper(args: argparse.Namespace) -> int:
    import numpy as np

    from vac.adapters.mic import SoundDeviceAudioSource
    from vac.adapters.whisper import FasterWhisperTranscriber
    from vac.ports import FRAME_DURATION_S

    seconds = 5
    print("モデルをロード中(初回はダウンロードあり)...")
    transcriber = FasterWhisperTranscriber()
    print(f"{seconds}秒間録音します。日本語で話してください...")
    frames = []
    with SoundDeviceAudioSource(device=_parse_device(args.device)) as source:
        for _ in range(int(seconds / FRAME_DURATION_S)):
            frames.append(source.read_frame())
    text = transcriber.transcribe(np.concatenate(frames))
    print(f"認識結果: {text}")
    print("OK: 話した内容と概ね一致していれば成功")
    return 0


def check_devices(args: argparse.Namespace) -> int:
    import sounddevice as sd

    print(sd.query_devices())
    print("\n上の一覧で使いたいマイクの index か名前を config の input_device に設定する")
    return 0


def check_inject(args: argparse.Namespace) -> int:
    import logging

    from vac.adapters.claude_driver import ClaudeDesktopDriver

    # どの注入経路(UIA ValuePattern / クリップボード貼り付け)を使ったかを
    # 画面で確認できるよう、診断時だけドライバのINFOログを出す。
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    driver = ClaudeDesktopDriver(exe_path=args.exe)
    print(f"Claude Desktopに注入します: {args.text!r}")
    driver.deliver(args.text)
    print("OK: Claude Desktopにテキストが送信されていれば成功")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m vac.check")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("sound", help="効果音を順に再生する").set_defaults(func=check_sound)

    mic_parser = sub.add_parser("mic", help="マイク入力レベルを表示する")
    mic_parser.add_argument("--device", default=None, help="入力デバイスの名前(部分一致)かindex")
    mic_parser.set_defaults(func=check_mic)

    wake_parser = sub.add_parser("wake", help="ウェイクワード検知を試す")
    wake_parser.add_argument("--model", default="hey_jarvis")
    wake_parser.add_argument("--device", default=None, help="入力デバイスの名前(部分一致)かindex")
    wake_parser.set_defaults(func=check_wake)

    vad_parser = sub.add_parser("vad", help="発話検知を試す")
    vad_parser.add_argument("--device", default=None, help="入力デバイスの名前(部分一致)かindex")
    vad_parser.set_defaults(func=check_vad)

    whisper_parser = sub.add_parser("whisper", help="5秒録音して文字起こしする")
    whisper_parser.add_argument("--device", default=None, help="入力デバイスの名前(部分一致)かindex")
    whisper_parser.set_defaults(func=check_whisper)

    inject_parser = sub.add_parser("inject", help="Claude Desktopにテキストを送る")
    inject_parser.add_argument("text")
    inject_parser.add_argument("--exe", default=None, help="claude.exe のパス")
    inject_parser.set_defaults(func=check_inject)

    sub.add_parser("devices", help="入力/出力デバイス一覧を表示する").set_defaults(func=check_devices)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
