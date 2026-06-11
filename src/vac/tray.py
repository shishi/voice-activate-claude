"""src/vac/tray.py — 常駐エントリポイント(Windows専用)

構成: メインスレッドでpystrayのトレイアイコン、ワーカースレッドで
Orchestrator.run_once() のループを回す。パイプラインが死んだら(マイク消失等)
エラー音を鳴らし、5秒間隔でマイクだけ作り直して復帰する(specセクション5)。
重いモデル群(Whisper等)は初回構築後キャッシュして再利用する。
"""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

import pystray
from PIL import Image, ImageDraw

from vac.config import Config, load_config
from vac.orchestrator import Orchestrator
from vac.ports import Feedback

logger = logging.getLogger(__name__)

CONFIG_PATH = Path.home() / ".config" / "voice-activate-claude" / "config.toml"
LOG_PATH = Path.home() / ".config" / "voice-activate-claude" / "vac.log"
MIC_RETRY_INTERVAL_S = 5.0


def build_components(config: Config) -> dict:
    """重いアダプタ群を構築する(マイク以外。失敗時は呼び出し側でリトライ)。"""
    from vac.adapters.claude_driver import ClaudeDesktopDriver
    from vac.adapters.vad import SileroSpeechDetector
    from vac.adapters.wakeword import OpenWakeWordDetector
    from vac.adapters.whisper import FasterWhisperTranscriber

    return {
        "wake": OpenWakeWordDetector(model=config.wake_model),
        "vad": SileroSpeechDetector(),
        "transcriber": FasterWhisperTranscriber(
            model_size=config.whisper_model, language=config.language
        ),
        "deliverer": ClaudeDesktopDriver(exe_path=config.claude_exe_path),
    }


def worker(config: Config, stop: threading.Event) -> None:
    import pythoncom  # pywinauto(UIA/COM)をワーカースレッドで使うための初期化

    from vac.adapters.mic import SoundDeviceAudioSource
    from vac.adapters.sound import WinSoundPlayer

    pythoncom.CoInitialize()
    feedback = WinSoundPlayer(enabled=config.sounds_enabled)
    components: dict | None = None
    while not stop.is_set():
        try:
            if components is None:
                components = build_components(config)
            with SoundDeviceAudioSource() as audio:
                orchestrator = Orchestrator(
                    audio=audio, feedback=feedback, config=config, **components
                )
                while not stop.is_set():
                    orchestrator.run_once()
        except Exception:
            # マイク消失・モデルロード失敗等。マイクだけ作り直して復帰を試みる。
            # 既知の制限: マイク以外(Whisper等)の持続的故障は components を
            # 作り直さないため、5秒毎のエラー音ループになる(ログで原因確認)。
            # v1では許容。
            logger.exception(
                "audio pipeline crashed; retrying in %ss", MIC_RETRY_INTERVAL_S
            )
            try:
                feedback.play(Feedback.ERROR)
            except Exception:
                logger.exception("feedback playback failed")
            time.sleep(MIC_RETRY_INTERVAL_S)


def make_icon_image() -> Image.Image:
    image = Image.new("RGB", (64, 64), "#222222")
    draw = ImageDraw.Draw(image)
    draw.ellipse((20, 12, 44, 40), fill="#44ccff")   # マイクのヘッド
    draw.rectangle((29, 40, 35, 52), fill="#44ccff")  # マイクの柄
    return image


def main() -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=LOG_PATH,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    config = load_config(CONFIG_PATH)
    stop = threading.Event()
    thread = threading.Thread(target=worker, args=(config, stop), daemon=True)
    thread.start()

    def on_quit(icon, item) -> None:
        stop.set()
        icon.stop()

    icon = pystray.Icon(
        "voice-activate-claude",
        make_icon_image(),
        "voice-activate-claude",
        menu=pystray.Menu(pystray.MenuItem("終了", on_quit)),
    )
    icon.run()


if __name__ == "__main__":
    main()
