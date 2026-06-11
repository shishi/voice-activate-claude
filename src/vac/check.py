"""src/vac/check.py — アダプタ単体の実機診断CLI

usage: python -m vac.check <subcommand>
subcommands は実装が進むたびに増える。
"""
from __future__ import annotations

import argparse
import sys


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
    from vac.ports import SAMPLE_RATE

    seconds = 3
    print(f"{seconds}秒間レベルメーターを表示します。話しかけてください...")
    with SoundDeviceAudioSource() as source:
        frames = int(seconds / 0.08)
        for _ in range(frames):
            frame = source.read_frame()
            level = int(np.abs(frame).mean() / 300)
            print("#" * min(level, 60))
    print("OK: 声に反応して棒が伸びていれば成功")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m vac.check")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("sound", help="効果音を順に再生する").set_defaults(func=check_sound)
    sub.add_parser("mic", help="マイク入力レベルを表示する").set_defaults(func=check_mic)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
