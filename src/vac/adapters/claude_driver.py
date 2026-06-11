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

    def get_text(self) -> str | None:
        win32clipboard.OpenClipboard()
        try:
            if not win32clipboard.IsClipboardFormatAvailable(
                win32clipboard.CF_UNICODETEXT
            ):
                return None
            return win32clipboard.GetClipboardData(win32clipboard.CF_UNICODETEXT)
        finally:
            win32clipboard.CloseClipboard()

    def set_text(self, text: str) -> None:
        win32clipboard.OpenClipboard()
        try:
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardData(win32clipboard.CF_UNICODETEXT, text)
        finally:
            win32clipboard.CloseClipboard()

    def clear(self) -> None:
        win32clipboard.OpenClipboard()
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

    def _wait_for_window(self):
        deadline = time.monotonic() + LAUNCH_TIMEOUT_S
        while time.monotonic() < deadline:
            window = self._find_window()
            if window is not None:
                return window
            time.sleep(0.5)
        raise DeliveryError(f"window did not appear within {LAUNCH_TIMEOUT_S}s")

    def _inject(self, window, text: str) -> None:
        # 経路1: UIAで入力欄(Editコントロール)を探しValuePatternで設定
        try:
            edit = window.child_window(control_type="Edit", found_index=0)
            edit.set_focus()
            edit.set_edit_text(text)  # ValuePattern相当
            return
        except Exception:
            logger.info("UIA ValuePattern injection failed; falling back to clipboard")
        # 経路2: クリップボード+Ctrl+V(contenteditable対策の本命フォールバック)
        with ClipboardGuard(self._clipboard, text):
            send_keys("^v")
            time.sleep(0.3)  # 貼り付け完了を待ってからクリップボードを復元
