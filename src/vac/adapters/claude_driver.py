"""src/vac/adapters/claude_driver.py — Claude Desktopへのテキスト注入(Windows専用)"""
from __future__ import annotations

import contextlib
import logging
import subprocess
import time
from pathlib import Path

import win32clipboard  # pywinautoが依存するpywin32に同梱
import win32con
import win32gui
from pywinauto import Desktop
from pywinauto.findwindows import ElementNotFoundError
from pywinauto.keyboard import send_keys

from vac.clipboard import ClipboardGuard
from vac.ports import DeliveryError

logger = logging.getLogger(__name__)


@contextlib.contextmanager
def _timed(label: str):
    # 各ステップの所要時間をINFOログに出す(遅さの原因切り分け用)。
    start = time.monotonic()
    try:
        yield
    finally:
        logger.info("%s: %.2fs", label, time.monotonic() - start)

WINDOW_TITLE_RE = r"^Claude(\s.*)?$"
HOME_TAB_TITLES = ("Home", "ホーム")            # ホーム(チャット一覧)へ戻るタブ。ロケール差を吸収
CHAT_MODE_TITLES = ("チャット", "Chat")         # 入力欄のモードトグル(チャット/Cowork)
NEW_CHAT_BUTTON_TITLES = ("新規チャット", "New chat")  # 新規チャットのみ。「新しいタスク」は使わない
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
    def __init__(self, exe_path: str | None = None, settle_s: float = 0.3) -> None:
        self._exe_path = exe_path
        self._settle_s = settle_s
        self._clipboard = Win32Clipboard()

    def deliver(self, text: str) -> None:
        try:
            with _timed("find_window"):
                window = self._find_window()
            if window is None:
                self._launch()
                window = self._wait_for_window()
            with _timed("raise_foreground"):
                self._raise_foreground(window)
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

    def _raise_foreground(self, window) -> None:
        # バックグラウンドから set_focus だけでは Windows の SetForegroundWindow 制約で
        # 前面化できないことがある。既知の回避策を順に試し、最後に成否を確認する。
        # (実機検証: Claude が前面でないと注入が fail-closed で中止されるため必須)
        try:
            window.set_focus()  # pywinauto の通常経路(最小化トグル等を内部で試す)
        except Exception:
            logger.info("set_focus failed; trying Win32 fallbacks", exc_info=True)

        if self._is_foreground(window):
            return

        try:
            hwnd = window.handle
            # 回避策1: 最小化→復元。OSがユーザー操作扱いして前面化を許すことが多い。
            win32gui.ShowWindow(hwnd, win32con.SW_MINIMIZE)
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            time.sleep(self._settle_s)
            if self._is_foreground(window):
                return
            # 回避策2: 前面スレッドに一時アタッチして SetForegroundWindow 制約を外す。
            fg = win32gui.GetForegroundWindow()
            import win32process

            target_thread, _ = win32process.GetWindowThreadProcessId(hwnd)
            fg_thread, _ = win32process.GetWindowThreadProcessId(fg) if fg else (0, 0)
            import win32api

            current_thread = win32api.GetCurrentThreadId()
            for other in {fg_thread, current_thread}:
                if other and other != target_thread:
                    try:
                        win32process.AttachThreadInput(other, target_thread, True)
                    except Exception:
                        logger.info("AttachThreadInput attach failed", exc_info=True)
            try:
                win32gui.SetForegroundWindow(hwnd)
            finally:
                for other in {fg_thread, current_thread}:
                    if other and other != target_thread:
                        try:
                            win32process.AttachThreadInput(other, target_thread, False)
                        except Exception:
                            logger.info("AttachThreadInput detach failed", exc_info=True)
            time.sleep(self._settle_s)
        except Exception:
            logger.info("Win32 foreground fallback failed", exc_info=True)
        # ここで前面化できていなくても、後続の _assert_foreground が fail-closed で守る。

    def _is_foreground(self, window) -> bool:
        try:
            return bool(window.is_active())
        except Exception:
            return False

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

    def _resolve(self, window, specs, timeout=10):
        # descendants() は型に関係なく約4.5秒(ツリー全走査)なので、1スキャンで
        # 複数コントロールをまとめて解決する。specs は (label, titles, control_type) の
        # リスト。control_type が "Edit" のときは name 不問で唯一のEditを拾う。
        # visible+enabled になるまで全体 timeout 秒まで再スキャン(fail-closed)。
        deadline = time.monotonic() + timeout
        first_scan = True
        while True:
            scan_start = time.monotonic()
            try:
                elements = window.descendants()
            except Exception:
                elements = []
            if first_scan:
                logger.info("  descendants(all): %.2fs, %d elems",
                            time.monotonic() - scan_start, len(elements))
                first_scan = False
            resolved = {}
            for label, titles, control_type in specs:
                if control_type == "Edit":
                    resolved[label] = self._pick_edit(elements)
                else:
                    resolved[label] = self._pick(elements, titles, control_type)
            if all(resolved.values()):
                return resolved
            if time.monotonic() >= deadline:
                missing = [label for label, val in resolved.items() if not val]
                raise DeliveryError(f"controls not found or not ready: {missing}")
            time.sleep(0.3)

    def _pick(self, elements, titles, control_type):
        # スナップショットから name+control_type 一致で visible+enabled な要素を返す(無ければ None)。
        wanted = tuple(titles)
        for element in elements:
            try:
                ei = element.element_info
                if ei.control_type != control_type:
                    continue
                if ei.name not in wanted:
                    continue
                if element.is_visible() and element.is_enabled():
                    return element
            except Exception:
                continue
        return None

    def _pick_edit(self, elements):
        # スナップショットから唯一の Edit を返す(visible+enabled)。無ければ None。
        for element in elements:
            try:
                if element.element_info.control_type != "Edit":
                    continue
                if element.is_visible() and element.is_enabled():
                    return element
            except Exception:
                continue
        return None

    def _inject(self, window, text: str) -> None:
        # 要望: 常に「チャット」モードの「新規チャット」に送る。Coworkのままだと
        # 新規ボタンが「新しいタスク」に変わるため、先にチャットモードへ切り替える。
        # 「新規チャット」はチャットモードにして初めて出現するので、スナップショットは
        # 2回に分ける: ①Home+チャットトグル(最初から在る) ②切替後に新規チャット+入力欄。
        # descendants は型に関係なく約4.5秒なので、まとめ取りで呼び出し回数を抑える。
        with _timed("snapshot home+chat-mode"):
            first = self._resolve(window, [
                ("home", HOME_TAB_TITLES, "Button"),
                ("chat_mode", CHAT_MODE_TITLES, "RadioButton"),
            ])

        logger.info("going to Home")
        self._assert_foreground(window)
        first["home"].click_input()
        time.sleep(self._settle_s)

        logger.info("selecting Chat mode")
        self._assert_foreground(window)
        first["chat_mode"].click_input()  # Cowork→チャット。既にチャットでも無害
        time.sleep(self._settle_s)

        with _timed("snapshot new-chat+composer"):
            second = self._resolve(window, [
                ("new_chat", NEW_CHAT_BUTTON_TITLES, "Button"),
                ("composer", (), "Edit"),
            ])

        logger.info("starting a new chat")
        self._assert_foreground(window)
        second["new_chat"].click_input()  # 「新しいタスク」は使わない
        time.sleep(self._settle_s)

        logger.info("focusing chat composer (Edit)")
        self._assert_foreground(window)
        second["composer"].click_input()

        logger.info("clearing composer")
        self._assert_foreground(window)
        send_keys("^a")
        self._assert_foreground(window)  # 破壊的なDelete直前に再確認
        send_keys("{DELETE}")
        time.sleep(self._settle_s)

        logger.info("pasting text via clipboard")
        with ClipboardGuard(self._clipboard, text):
            self._assert_foreground(window)
            send_keys("^v")
            time.sleep(self._settle_s)
