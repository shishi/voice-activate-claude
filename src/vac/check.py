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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m vac.check")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("sound", help="効果音を順に再生する").set_defaults(func=check_sound)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
