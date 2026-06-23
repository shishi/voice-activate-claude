"""src/vac/adapters/claude_driver.py — Claude Desktopへのテキスト注入(Windows専用)"""
from __future__ import annotations

import logging
import subprocess
import time
from pathlib import Path

import win32clipboard  # pywinautoが依存するpywin32に同梱
from pywinauto import Desktop
from pywinauto.findwindows import ElementNotFoundError
from pywinauto.keyboard import send_keys

from vac.clipboard import ClipboardGuard
from vac.ports import DeliveryError

logger = logging.getLogger(__name__)

WINDOW_TITLE_RE = r"^Claude(\s.*)?$"
LAUNCH_TIMEOUT_S = 15.0
DEFAULT_EXE_CANDIDATES = [
    # 標準的なインストール先。実機で `where claude` 等で確認して必要なら追加する
    Path.home() / "AppData/Local/AnthropicClaude/claude.exe",
    Path.home() / "AppData/Local/Programs/claude-desktop/Claude.exe",
]


class Win32Clipboard:
    """ClipboardPort のWin32実装(テキストのみ扱う)。"""

    @staticmethod
    def _open_with_retry(attempts: int = 5, delay_s: float = 0.05) -> None:
        # 他プロセスがクリップボードを掴んでいると ACCESS_DENIED になるため少し粘る
        for attempt in range(attempts):
            try:
                win32clipboard.OpenClipboard()
                return
            except Exception:
                if attempt == attempts - 1:
                    raise
                time.sleep(delay_s)

    def get_text(self) -> str | None:
        self._open_with_retry()
        try:
            if not win32clipboard.IsClipboardFormatAvailable(
                win32clipboard.CF_UNICODETEXT
            ):
                return None
            return win32clipboard.GetClipboardData(win32clipboard.CF_UNICODETEXT)
        finally:
            win32clipboard.CloseClipboard()

    def set_text(self, text: str) -> None:
        self._open_with_retry()
        try:
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardData(win32clipboard.CF_UNICODETEXT, text)
        finally:
            win32clipboard.CloseClipboard()

    def clear(self) -> None:
        self._open_with_retry()
        try:
            win32clipboard.EmptyClipboard()
        finally:
            win32clipboard.CloseClipboard()


class ClaudeDesktopDriver:
    def __init__(self, exe_path: str | None = None) -> None:
        self._exe_path = exe_path
        self._clipboard = Win32Clipboard()

    def deliver(self, text: str) -> None:
        try:
            window = self._find_window()
            if window is None:
                self._launch()
                window = self._wait_for_window()
            window.set_focus()
            self._inject(window, text)
            self._assert_foreground(window)
            send_keys("{ENTER}")
        except DeliveryError:
            raise
        except Exception as exc:
            raise DeliveryError(str(exc)) from exc

    def _find_window(self):
        try:
            window = Desktop(backend="uia").window(title_re=WINDOW_TITLE_RE)
            if window.exists():
                return window
            return None
        except ElementNotFoundError:
            return None

    def _launch(self) -> None:
        candidates = (
            [Path(self._exe_path)] if self._exe_path else DEFAULT_EXE_CANDIDATES
        )
        for exe in candidates:
            if exe.exists():
                subprocess.Popen([str(exe)])
                return
        raise DeliveryError(f"claude.exe not found in: {candidates}")

    def _assert_foreground(self, window) -> None:
        # 通知などにフォーカスを奪われたままENTERを打つと他アプリに誤送信されるため、
        # 直前に前面を検証する。奪い返してまで送らない(fail-closed):
        # ユーザーが意図的に他アプリへ移った場合に勝手に送信しないため。
        if not window.is_active():
            raise DeliveryError("Claude window lost focus before ENTER; aborting")

    def _wait_for_window(self):
        deadline = time.monotonic() + LAUNCH_TIMEOUT_S
        while time.monotonic() < deadline:
            window = self._find_window()
            if window is not None:
                return window
            time.sleep(0.5)
        raise DeliveryError(f"window did not appear within {LAUNCH_TIMEOUT_S}s")

    def _inject(self, window, text: str) -> None:
        # 要望: どのタブ(Chat/Cowork/Code)を開いていても、必ず Chat タブの
        # 入力欄に送る。ElectronのcontenteditableはUIA ValuePatternが効かない/
        # 黙って失敗しうるため、入力欄を実クリックでフォーカスしてクリップボード貼り付けする。
        logger.info("switching to Chat tab")
        chat_tab = window.child_window(title="Chat", control_type="Button")
        chat_tab.wait("exists enabled visible ready", timeout=10)
        chat_tab.click_input()  # 既にChatタブでも無害(冪等)
        time.sleep(0.3)  # ビュー切り替えの描画待ち

        logger.info("focusing chat composer (Edit)")
        composer = window.child_window(control_type="Edit")
        composer.wait("exists enabled visible ready", timeout=10)
        composer.click_input()  # 唯一のEdit=Chat入力欄を確実にフォーカス

        logger.info("pasting text via clipboard")
        with ClipboardGuard(self._clipboard, text):
            self._assert_foreground(window)  # 貼り付け直前にも前面を確認(誤爆=漏洩防止)
            send_keys("^v")
            time.sleep(0.3)  # 貼り付け完了を待ってからクリップボードを復元
