"""src/vac/adapters/claude_driver.py — Claude Desktopへのテキスト注入(Windows専用)"""
from __future__ import annotations

import contextlib
import ctypes
import logging
import subprocess
import time
from ctypes import wintypes
from pathlib import Path, PureWindowsPath

import win32clipboard  # pywinautoが依存するpywin32に同梱
import win32con
import win32gui
from pywinauto.controls.uiawrapper import UIAWrapper
from pywinauto.keyboard import send_keys
from pywinauto.uia_element_info import UIAElementInfo

from vac.adapters.window_identity import CLAUDE_EXE_NAMES, WINDOW_TITLE_RE, exe_matches, title_matches
from vac.clipboard import ClipboardGuard
from vac.ports import DeliveryError

logger = logging.getLogger(__name__)

# QueryFullProcessImageNameW は実機の pywin32 が wrap していない(win32process.pyd の
# export は GetModuleFileNameEx のみ)ため ctypes で直接呼ぶ。64bit で HANDLE が
# 既定の int(32bit)に切り詰められないよう prototype を明示する。
_kernel32 = ctypes.windll.kernel32
_kernel32.OpenProcess.restype = wintypes.HANDLE
_kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
_kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
_kernel32.QueryFullProcessImageNameW.argtypes = [
    wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD),
]
_kernel32.CloseHandle.restype = wintypes.BOOL
_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]


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
# Microsoft Store (MSIX) 版の起動用 AUMID。WindowsApps 配下の exe は直接実行できない
# (ACL 制限)ため、shell:AppsFolder 経由で起動する。パッケージファミリー名は
# 発行元証明書由来で版が変わっても不変。
CLAUDE_MSIX_AUMID = r"shell:AppsFolder\Claude_pzs8sxrjxfjjc!Claude"
# MSIX パッケージの導入判定に使うユーザー領域のフォルダ。WindowsApps 直下は
# 通常権限で列挙できないため、ユーザーが読めるこちらで存在確認する。
CLAUDE_MSIX_PACKAGE_DIR = Path.home() / "AppData/Local/Packages/Claude_pzs8sxrjxfjjc"


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
        # 設定(--exe / config)で別名バイナリを指した場合、その basename も
        # ウィンドウ同一性判定に加える。既定名しか認めないと _launch はできるのに
        # _find_window が永遠に認識できず再起動ループになる(Codex review P2)。
        names = set(CLAUDE_EXE_NAMES)
        if exe_path:
            names.add(PureWindowsPath(exe_path).name.lower())
        self._allowed_exe_names = frozenset(names)
        self._settle_s = settle_s
        self._clipboard = Win32Clipboard()
        self._cached_hwnd: int | None = None  # 常駐時の再注入で列挙を省く
        self._scan_saw_candidate = False  # 直近スキャンで候補窓を見たか(未準備と不在の区別)

    def deliver(self, text: str) -> None:
        try:
            with _timed("find_window"):
                window = self._find_window()
            if window is None:
                # 候補窓は見えたが UIA 未準備で掴めなかっただけなら、二重起動を避けて
                # 待機だけする(_wait_for_window が _find_window を再ポーリングする)
                if not self._scan_saw_candidate:
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
        # 注: キャッシュが有効な間は最初に掴んだウィンドウに固定される(複数の
        # Claude ウィンドウがある場合、後から手前に来た別ウィンドウには切り替わらない)。
        # どのウィンドウでも「チャットモードの新規チャットに送る」要件は満たすため、
        # 速度優先の意図的なトレードオフ(失敗時はキャッシュ破棄で再列挙される)。
        # _wrap の一時失敗(起動直後で UIA プロバイダ未準備等)は「まだ見つからない」
        # 扱いで None に落とし、_wait_for_window のポーリング継続を殺さない。
        self._scan_saw_candidate = False
        cached = self._validate_cached_hwnd()
        if cached is not None:
            self._scan_saw_candidate = True
            try:
                return self._wrap(cached)
            except Exception:
                logger.debug("wrap failed for cached hwnd=%s", cached, exc_info=True)
        self._cached_hwnd = None
        for hwnd in self._enum_claude_hwnds():
            self._scan_saw_candidate = True
            try:
                wrapper = self._wrap(hwnd)
            except Exception:
                logger.debug("wrap failed for hwnd=%s (UIA not ready?)", hwnd, exc_info=True)
                continue
            self._cached_hwnd = hwnd
            return wrapper
        return None

    def _validate_cached_hwnd(self):
        # hwnd は OS に再利用されるため、liveness(IsWindow)だけでは別ウィンドウを
        # 掴みうる。タイトル+プロセス exe まで再検証して初めて再利用する(fail-closed)。
        hwnd = self._cached_hwnd
        try:
            if (
                hwnd
                and win32gui.IsWindow(hwnd)
                and win32gui.IsWindowVisible(hwnd)  # トレイ格納で非表示になった窓は新規探索(_launch経由の復元)に回す
                and title_matches(win32gui.GetWindowText(hwnd))
                and exe_matches(self._window_exe(hwnd), self._allowed_exe_names)
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
                if not win32gui.IsWindowVisible(hwnd):
                    return True
                if not title_matches(win32gui.GetWindowText(hwnd)):
                    return True
                exe = self._window_exe(hwnd)
                if exe_matches(exe, self._allowed_exe_names):
                    hwnds.append(hwnd)
                else:
                    # タイトルは Claude 一致なのに exe で弾いた事実は誤検知切り分けの要。
                    # (exe=None なら OpenProcess/GetProcessImageFileName 失敗)
                    logger.info("title matched but exe rejected: hwnd=%s exe=%r", hwnd, exe)
            except Exception:
                logger.debug("enum callback skipped hwnd=%s", hwnd, exc_info=True)
            return True

        win32gui.EnumWindows(_cb, None)
        return hwnds

    def _window_exe(self, hwnd):
        # hwnd の所有プロセスの実行ファイルパス。取れなければ None(呼び出し側で不一致扱い)。
        # 実機の pywin32 は GetProcessImageFileName / QueryFullProcessImageName を
        # export しておらず(AttributeError で全ウィンドウを棄却する実障害になった)、
        # ctypes の QueryFullProcessImageNameW(最小権限 LIMITED、昇格プロセスも可)を
        # 第一候補に、pywin32 の GetModuleFileNameEx(要 QUERY_INFORMATION|VM_READ、
        # export 実在は .pyd で確認済み)を予備にする。
        import win32process

        try:
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
        except Exception as exc:
            logger.info("window pid lookup failed for hwnd=%s: %r", hwnd, exc)
            return None
        return self._process_exe_limited(pid) or self._process_exe_pywin32(pid, hwnd)

    def _process_exe_limited(self, pid):
        try:
            proc = _kernel32.OpenProcess(
                win32con.PROCESS_QUERY_LIMITED_INFORMATION, False, pid
            )
            if not proc:
                return None
            try:
                buf = ctypes.create_unicode_buffer(32768)
                size = wintypes.DWORD(len(buf))
                if _kernel32.QueryFullProcessImageNameW(proc, 0, buf, ctypes.byref(size)):
                    return buf.value
                return None
            finally:
                _kernel32.CloseHandle(proc)
        except Exception:
            return None

    def _process_exe_pywin32(self, pid, hwnd):
        import win32api
        import win32process

        try:
            proc = win32api.OpenProcess(
                win32con.PROCESS_QUERY_INFORMATION | win32con.PROCESS_VM_READ, False, pid
            )
            try:
                return win32process.GetModuleFileNameEx(proc, 0)
            finally:
                proc.Close()
        except Exception as exc:
            # 両経路とも失敗=認識不能。原因切り分けのため例外の実体を INFO で残す。
            logger.info("window exe lookup failed for hwnd=%s: %r", hwnd, exc)
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
        if not self._exe_path and CLAUDE_MSIX_PACKAGE_DIR.exists():
            # Squirrel 版のパスが無ければ Microsoft Store (MSIX) 版とみなして
            # AUMID 起動を試す(実機は Store 版だった)。パッケージ導入確認をせず
            # 無条件に explorer を起動すると、未インストール機で即エラーの代わりに
            # 15秒の曖昧な timeout になるため、先に存在確認する。
            logger.info("launching via MSIX AUMID: %s", CLAUDE_MSIX_AUMID)
            subprocess.Popen(["explorer.exe", CLAUDE_MSIX_AUMID])
            return
        raise DeliveryError(
            f"Claude Desktop not found (exe candidates: {candidates}, "
            f"msix package dir: {CLAUDE_MSIX_PACKAGE_DIR})"
        )

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
