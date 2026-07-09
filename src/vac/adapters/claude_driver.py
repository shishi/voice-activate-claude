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

    def _first_existing(self, window, titles, control_type, timeout=10):
        # child_window の遅延評価は呼ぶ度にツリー全体を再走査して激遅(Electronの巨大UIA)。
        # descendants(control_type=...) を1回だけ回して name で絞り、visible/enabled になった
        # 解決済み wrapper を返す。起動直後・遷移直後は未描画のことがあるので全体 timeout 秒
        # まで再走査する。id は毎回変わる(base-ui-_r_...)ので name+control_type で掴む。
        wanted = tuple(titles)
        deadline = time.monotonic() + timeout
        first_scan = True
        while True:
            scan_start = time.monotonic()
            try:
                candidates = window.descendants(control_type=control_type)
            except Exception:
                candidates = []
            if first_scan:
                logger.info(
                    "  descendants(%s): %.2fs, %d elems",
                    control_type, time.monotonic() - scan_start, len(candidates),
                )
                first_scan = False
            for element in candidates:
                try:
                    if element.window_text() in wanted and element.is_visible() and element.is_enabled():
                        return element
                except Exception:
                    continue
            if time.monotonic() >= deadline:
                raise DeliveryError(f"{control_type} not found or not ready (tried {titles})")
            time.sleep(0.3)

    def _first_edit(self, window, timeout=10):
        # 入力欄は唯一の Edit。descendants を1回で拾い、visible/enabled な解決済み wrapper を返す。
        deadline = time.monotonic() + timeout
        first_scan = True
        while True:
            scan_start = time.monotonic()
            try:
                edits = window.descendants(control_type="Edit")
            except Exception:
                edits = []
            if first_scan:
                logger.info(
                    "  descendants(Edit): %.2fs, %d elems",
                    time.monotonic() - scan_start, len(edits),
                )
                first_scan = False
            for edit in edits:
                try:
                    if edit.is_visible() and edit.is_enabled():
                        return edit
                except Exception:
                    continue
            if time.monotonic() >= deadline:
                raise DeliveryError("chat composer (Edit) not found or not ready")
            time.sleep(0.3)

    def _inject(self, window, text: str) -> None:
        # 要望: 常に「チャット」モードの「新規チャット」に送る。Coworkのままだと
        # 新規ボタンが「新しいタスク」に変わるため、先にチャットモードへ切り替える。
        # 「新しいタスク」は絶対に押さない(見つからなければ fail-closed 中止)。
        # ElectronのcontenteditableはUIA ValuePatternが効かない/黙って失敗しうるため、
        # 入力欄を実クリックでフォーカスしてクリップボード貼り付けする。物理クリック/
        # 貼り付けの各直前で前面を検証し、前面化できないなら一切操作しない(誤爆防止)。
        logger.info("going to Home")
        with _timed("find Home tab"):
            home = self._first_existing(window, HOME_TAB_TITLES, "Button")
        self._assert_foreground(window)
        home.click_input()  # 既にHomeでも無害
        time.sleep(self._settle_s)

        logger.info("selecting Chat mode")
        with _timed("find Chat mode toggle"):
            chat_mode = self._first_existing(window, CHAT_MODE_TITLES, "RadioButton")
        self._assert_foreground(window)
        chat_mode.click_input()  # Cowork→チャット。既にチャットでも無害
        time.sleep(self._settle_s)

        logger.info("starting a new chat")
        with _timed("find new-chat button"):
            new_chat = self._first_existing(window, NEW_CHAT_BUTTON_TITLES, "Button")
        self._assert_foreground(window)
        new_chat.click_input()  # 毎回まっさらなチャットに送る(「新しいタスク」は使わない)
        time.sleep(self._settle_s)

        logger.info("focusing chat composer (Edit)")
        with _timed("find composer"):
            composer = self._first_edit(window)
        self._assert_foreground(window)
        composer.click_input()  # 唯一のEdit=入力欄を確実にフォーカス

        logger.info("clearing composer")
        self._assert_foreground(window)
        send_keys("^a")           # 全選択
        send_keys("{DELETE}")     # 前回の未送信テキストを消す(残り混入を防ぐ)
        time.sleep(self._settle_s)

        logger.info("pasting text via clipboard")
        with ClipboardGuard(self._clipboard, text):
            self._assert_foreground(window)
            send_keys("^v")
            time.sleep(self._settle_s)  # 貼り付け完了を待ってからクリップボードを復元
