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
from pywinauto.controls.uiawrapper import UIAWrapper
from pywinauto.keyboard import send_keys
from pywinauto.uia_element_info import UIAElementInfo

from vac.adapters.window_identity import WINDOW_TITLE_RE, exe_matches, title_matches
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

HOME_TAB_TITLES = ("Home", "ホーム")            # Home/Code タブ。トグルと入力欄は Home 側にのみ存在
CHAT_MODE_TITLES = ("チャット", "Chat")         # 入力欄のモードトグル(チャット/Cowork)
# 新規チャットのボタン。2026-07 の UI 更新で「新規チャット」→「新規」に改名された。
# Code タブでは同じ位置が「新規セッション」になるが、完全一致なので誤爆しない(fail-closed)。
# 「新しいタスク」は絶対に使わない。
NEW_CHAT_BUTTON_TITLES = ("新規", "新規チャット", "New chat")
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
        self._cached_hwnd: int | None = None  # 常駐時の再注入で列挙を省く

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
            self._cached_hwnd = None
            raise
        except Exception as exc:
            self._cached_hwnd = None
            raise DeliveryError(str(exc)) from exc

    def _find_window(self):
        # WindowSpecification(遅延解決)を返すと、以後のメソッド呼び出しごとに
        # タイトル regex の全ウィンドウ検索(実測約4.3秒)が再実行される。
        # 実体の UIAWrapper を返し、deliver あたりの解決を最大1回にする(spec 2026-07-11)。
        cached = self._validate_cached_hwnd()
        if cached is not None:
            return self._wrap(cached)
        self._cached_hwnd = None
        for hwnd in self._enum_claude_hwnds():
            self._cached_hwnd = hwnd
            return self._wrap(hwnd)
        return None

    def _validate_cached_hwnd(self):
        # hwnd は OS に再利用されるため、liveness(IsWindow)だけでは別ウィンドウを
        # 掴みうる。タイトル+プロセス exe まで再検証して初めて再利用する(fail-closed)。
        hwnd = self._cached_hwnd
        try:
            if (
                hwnd
                and win32gui.IsWindow(hwnd)
                and title_matches(win32gui.GetWindowText(hwnd))
                and exe_matches(self._window_exe(hwnd))
            ):
                return hwnd
        except Exception:
            pass
        return None

    def _enum_claude_hwnds(self):
        # EnumWindows は Z順(手前が先)に列挙するため、複数候補時は先頭=手前を
        # 採用すれば決定的になる。タイトルだけでなくプロセス exe も検証する(誤マッチ防止)。
        # IsWindowVisible は WS_VISIBLE を見るだけなので最小化中でも TRUE(見逃さない)。
        hwnds: list[int] = []

        def _cb(hwnd, _):
            # コールバック内の例外は EnumWindows 全体を中断させる(pywin32 の仕様)ため、
            # 1個の異常ウィンドウで列挙全体が死なないよう握って続行する。
            try:
                if (
                    win32gui.IsWindowVisible(hwnd)
                    and title_matches(win32gui.GetWindowText(hwnd))
                    and exe_matches(self._window_exe(hwnd))
                ):
                    hwnds.append(hwnd)
            except Exception:
                logger.debug("enum callback skipped hwnd=%s", hwnd, exc_info=True)
            return True

        win32gui.EnumWindows(_cb, None)
        return hwnds

    def _window_exe(self, hwnd):
        # hwnd の所有プロセスの実行ファイルパス。取れなければ None(呼び出し側で不一致扱い)。
        # QUERY_INFORMATION|VM_READ だと管理者起動の Claude を開けず(access denied)、
        # 既存ウィンドウを見逃して二重起動しうるため、昇格プロセスでも開ける
        # 最小権限 PROCESS_QUERY_LIMITED_INFORMATION を使う。
        # GetProcessImageFileName はデバイス形式パス(\Device\HarddiskVolumeX\...)を返すが、
        # 判定は exe_matches が basename しか見ないため問題ない。
        import win32api
        import win32process

        try:
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            proc = win32api.OpenProcess(
                win32con.PROCESS_QUERY_LIMITED_INFORMATION, False, pid
            )
            try:
                return win32process.GetProcessImageFileName(proc)
            finally:
                proc.Close()
        except Exception:
            return None

    def _wrap(self, hwnd):
        # UIAElementInfo のシグネチャは (handle_or_elem=None, cache_enable=False)。
        # handle= というキーワードは存在しない(位置引数で渡す)。
        return UIAWrapper(UIAElementInfo(hwnd))

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
                self._log_resolve_failure(elements, specs, missing)
                raise DeliveryError(f"controls not found or not ready: {missing}")
            time.sleep(0.3)

    def _log_resolve_failure(self, elements, specs, missing):
        # 解決失敗の切り分け用ダンプ。最終スナップショットから
        # 「探している型と一致する要素(名前は不問)」と
        # 「探している名前を部分的に含む要素(型は不問)」を全て出す。
        # これで name変更 / control_type変更 / 画面違い(どちらも出ない)を1回で判別できる。
        wanted_types = {ct for _, _, ct in specs}
        wanted_names = tuple(t for _, titles, _ in specs for t in titles)
        logger.info("resolve failed for %s; dumping candidates from %d elems", missing, len(elements))
        raised = 0
        for element in elements:
            try:
                ei = element.element_info
                name = ei.name or ""
                if ei.control_type in wanted_types or any(w in name for w in wanted_names):
                    logger.info("  candidate: %-12s name=%r id=%r",
                                ei.control_type, name, ei.automation_id)
            except Exception:
                raised += 1
        if raised:
            logger.info("  (element_info raised on %d elems)", raised)

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
                # 名前+型は一致した。ready判定で弾く理由を診断ログに出す(なぜ取れないかの切り分け用)。
                vis = ena = None
                try:
                    vis = element.is_visible()
                    ena = element.is_enabled()
                except Exception as exc:
                    logger.info("  ready-check raised for %r/%s: %s", ei.name, control_type, exc)
                if vis and ena:
                    return element
                logger.info(
                    "  matched %r/%s but not ready: visible=%s enabled=%s",
                    ei.name, control_type, vis, ena,
                )
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
        # 要望: 常に「チャット」モードの新規チャットに送る。
        # 実機の UIA ツリー比較(2026-07-11)で確定した現行 UI の前提:
        #   - チャット/Cowork RadioButton と入力欄 Edit は Home タブにのみ存在する。
        #     Code タブでは RadioButton が 0 個になり解決不能 → まず Home をクリックする。
        #     (以前「Home クリックでトグルが消える」問題があったが、その後の UI 更新で
        #      Home は入力欄+トグル常駐のダッシュボードになった。挙動が再変化しても
        #      後続の resolve が失敗して fail-closed 中止になるだけで誤爆はしない)
        #   - サイドバーの新規ボタンは Home タブで「新規」、Code タブで「新規セッション」。
        # 各コントロールはナビゲーションごとに再描画され、事前取得した wrapper は
        # stale になりうる。よって「使う直前に1個ずつ解決」する(descendants は約4.5秒/回)。
        # 順序は Home → 新規 → チャットモード → Edit。モード切替を新規クリックの後に
        # 置くのは、送信直前にチャットモードであることを保証するため。
        logger.info("switching to Home tab")
        with _timed("resolve home-tab"):
            home = self._resolve(window, [("home", HOME_TAB_TITLES, "Button")])["home"]
        self._assert_foreground(window)
        home.click_input()  # 既に Home でも無害(ダッシュボード表示になる)
        time.sleep(self._settle_s)

        logger.info("starting a new chat")
        with _timed("resolve new-chat"):
            new_chat = self._resolve(window, [("new_chat", NEW_CHAT_BUTTON_TITLES, "Button")])["new_chat"]
        self._assert_foreground(window)
        new_chat.click_input()  # 「新しいタスク」「新規セッション」は名前不一致で掴めない=誤爆しない
        time.sleep(self._settle_s)

        logger.info("selecting Chat mode")
        with _timed("resolve chat-mode"):
            chat_mode = self._resolve(window, [("chat_mode", CHAT_MODE_TITLES, "RadioButton")])["chat_mode"]
        self._assert_foreground(window)
        chat_mode.click_input()  # Cowork→チャット。既にチャットでも無害
        time.sleep(self._settle_s)

        logger.info("focusing chat composer (Edit)")
        with _timed("resolve composer"):
            composer = self._resolve(window, [("composer", (), "Edit")])["composer"]
        self._assert_foreground(window)
        composer.click_input()

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
