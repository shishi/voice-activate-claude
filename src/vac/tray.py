"""src/vac/tray.py — 常駐エントリポイント(Windows専用)

構成: メインスレッドで pystray のトレイアイコン、ワーカースレッドで
Orchestrator.run_once() のループを回す。トレイの「マイク」から入力デバイスを
選ぶと、重いモデルはキャッシュしたままマイクだけ作り直して即切替し、選択を
config.toml に保存する(次回起動でも記憶)。マイク消失時は5秒間隔で復帰する。
"""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

import pystray
from PIL import Image, ImageDraw

from vac.config import Config, load_config, save_input_device
from vac.orchestrator import Orchestrator
from vac.ports import Feedback

logger = logging.getLogger(__name__)

CONFIG_PATH = Path.home() / ".config" / "voice-activate-claude" / "config.toml"
LOG_PATH = Path.home() / ".config" / "voice-activate-claude" / "vac.log"
MIC_RETRY_INTERVAL_S = 5.0


class MicControl:
    """マイク選択と再起動要求をワーカースレッドと共有する。"""

    def __init__(self, device: str | int | None) -> None:
        self.device = device
        self.selected_name = device if isinstance(device, str) else None
        self.stop = threading.Event()
        self.restart = threading.Event()
        self._audio = None
        self._lock = threading.Lock()

    def bind(self, audio) -> None:
        with self._lock:
            self._audio = audio

    def switch(self, name: str) -> None:
        # トレイから呼ばれる。選択を保存し、稼働中のストリームを止めて作り直させる。
        self.device = name
        self.selected_name = name
        try:
            save_input_device(CONFIG_PATH, name)
        except Exception:
            logger.exception("failed to save input_device to config")
        self.restart.set()
        with self._lock:
            if self._audio is not None:
                try:
                    self._audio.abort()
                except Exception:
                    logger.exception("failed to abort audio for switch")


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


def worker(config: Config, control: MicControl) -> None:
    import pythoncom  # pywinauto(UIA/COM)をワーカースレッドで使うための初期化

    from vac.adapters.mic import SoundDeviceAudioSource
    from vac.adapters.sound import WinSoundPlayer

    pythoncom.CoInitialize()
    feedback = WinSoundPlayer(enabled=config.sounds_enabled)
    components: dict | None = None
    while not control.stop.is_set():
        try:
            if components is None:
                components = build_components(config)
            control.restart.clear()
            with SoundDeviceAudioSource(device=control.device) as audio:
                control.bind(audio)
                orchestrator = Orchestrator(
                    audio=audio, feedback=feedback, config=config, **components
                )
                while not control.stop.is_set() and not control.restart.is_set():
                    orchestrator.run_once()
        except Exception:
            if control.restart.is_set():
                # トレイからの意図的なマイク切替。エラー音も待機もなしで作り直す。
                logger.info("restarting audio with device: %r", control.device)
                continue
            # マイク消失・モデルロード失敗等。マイクだけ作り直して復帰を試みる。
            # 既知の制限: マイク以外(Whisper等)の持続的故障は components を
            # 作り直さないため、5秒毎のエラー音ループになる(ログで原因確認)。v1では許容。
            logger.exception(
                "audio pipeline crashed; retrying in %ss", MIC_RETRY_INTERVAL_S
            )
            try:
                feedback.play(Feedback.ERROR)
            except Exception:
                logger.exception("feedback playback failed")
            time.sleep(MIC_RETRY_INTERVAL_S)
        finally:
            control.bind(None)


def make_icon_image() -> Image.Image:
    image = Image.new("RGB", (64, 64), "#222222")
    draw = ImageDraw.Draw(image)
    draw.ellipse((20, 12, 44, 40), fill="#44ccff")   # マイクのヘッド
    draw.rectangle((29, 40, 35, 52), fill="#44ccff")  # マイクの柄
    return image


def _make_select(control: MicControl, name: str):
    def select(icon, item) -> None:
        control.switch(name)
    return select


def _make_checked(control: MicControl, name: str):
    def checked(item) -> bool:
        return control.selected_name == name
    return checked


def _build_mic_menu(control: MicControl):
    import sounddevice as sd

    from vac.devices import list_input_devices

    try:
        devices = list_input_devices(sd.query_devices())
    except Exception:
        logger.exception("failed to list input devices")
        devices = []
    items = [
        pystray.MenuItem(
            name,
            _make_select(control, name),
            checked=_make_checked(control, name),
            radio=True,
        )
        for _index, name in devices
    ]
    return pystray.Menu(*items)


def main() -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=LOG_PATH,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    config = load_config(CONFIG_PATH)
    control = MicControl(config.input_device)
    thread = threading.Thread(target=worker, args=(config, control), daemon=True)
    thread.start()

    def on_quit(icon, item) -> None:
        control.stop.set()
        with control._lock:
            if control._audio is not None:
                try:
                    control._audio.abort()
                except Exception:
                    logger.exception("failed to abort audio on quit")
        icon.stop()

    icon = pystray.Icon(
        "voice-activate-claude",
        make_icon_image(),
        "voice-activate-claude",
        menu=pystray.Menu(
            pystray.MenuItem("マイク", _build_mic_menu(control)),
            pystray.MenuItem("終了", on_quit),
        ),
    )
    icon.run()


if __name__ == "__main__":
    main()
