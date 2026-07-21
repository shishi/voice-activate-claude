# 注入高速化(ウィンドウ解決1回化)Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Claude Desktop へのテキスト注入を約33秒→約3秒にする(ウィンドウ解決を deliver あたり最大1回にし、初回検索を Win32 列挙にし、hwnd をキャッシュする)。

**Architecture:** spec は `docs/superpowers/specs/2026-07-11-injection-speedup-design.md`(承認済み・Codex adversarial clean)。root cause は「pywinauto の WindowSpecification がメソッド呼び出しごとに約4.3秒のタイトル regex 検索を再実行する」こと。対策は (1) ウィンドウ同一性判定(タイトル+プロセス exe)を WSL でテスト可能な純粋関数モジュールに切り出し、(2) driver の `_find_window` を EnumWindows + UIAWrapper 直結に置き換え、(3) hwnd をインスタンス内キャッシュして3点検証(IsWindow+タイトル+exe)で再利用する。fail-closed(前面検証・完全一致・掴めなければ中止)は一切変えない。

**Tech Stack:** Python 3.12 / pywinauto(UIA backend)/ pywin32(win32gui, win32process, win32api)/ pytest(WSL で実行可能な部分のみ)

**検証の制約:** `claude_driver.py` は pywinauto を import するため WSL では import 不可。driver 変更の検証は `python -c "import ast; ast.parse(...)"` + `uv run pytest -q`(既存68+新規)+ 実機(shishi)で行う。新規モジュール `window_identity.py` は Windows 依存 import を持たないため WSL で普通に TDD する。

---

### Task 1: ウィンドウ同一性判定モジュール(WSL で TDD)

タイトル regex とプロセス exe 名の判定を、Windows 依存 import の無い純粋関数として切り出す。誤マッチ防止(fail-closed)の中核ロジックなのでここだけは実テストを書く。

**Files:**
- Create: `src/vac/adapters/window_identity.py`
- Test: `tests/test_window_identity.py`

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_window_identity.py` を新規作成:

```python
"""tests/test_window_identity.py — ウィンドウ同一性判定(タイトル/exe)のテスト"""
from vac.adapters.window_identity import exe_matches, title_matches


class TestTitleMatches:
    def test_exact_claude(self):
        assert title_matches("Claude")

    def test_claude_with_suffix(self):
        assert title_matches("Claude - 新規チャット")

    def test_rejects_prefix(self):
        # 他アプリが "My Claude" 等を名乗っても掴まない
        assert not title_matches("My Claude")

    def test_rejects_claude_without_separator(self):
        assert not title_matches("ClaudeX")

    def test_rejects_empty_and_none(self):
        assert not title_matches("")
        assert not title_matches(None)


class TestExeMatches:
    def test_standard_install_path(self):
        assert exe_matches(r"C:\Users\you\AppData\Local\AnthropicClaude\claude.exe")

    def test_case_insensitive(self):
        assert exe_matches(r"C:\Program Files\claude-desktop\Claude.EXE")

    def test_rejects_other_exe(self):
        # ブラウザのタブが "Claude - ..." を名乗っていても exe で弾く
        assert not exe_matches(r"C:\Program Files\BraveSoftware\brave.exe")

    def test_rejects_none_and_empty(self):
        # exe が取得できない場合は不一致扱い(fail-closed)
        assert not exe_matches(None)
        assert not exe_matches("")
```

- [ ] **Step 2: テストが失敗することを確認する**

Run: `uv run pytest tests/test_window_identity.py -q`
Expected: FAIL(`ModuleNotFoundError: No module named 'vac.adapters.window_identity'`)

- [ ] **Step 3: 最小実装を書く**

`src/vac/adapters/window_identity.py` を新規作成:

```python
"""src/vac/adapters/window_identity.py — Claude Desktop ウィンドウの同一性判定。

タイトル regex だけでは "Claude - ..." を名乗る他アプリ(ブラウザタブ等)を
誤マッチしうるため、プロセス exe 名も併せて検証する(spec 2026-07-11)。
Windows 依存 import を持たない純粋関数のみ(WSL でテスト可能)。
"""
from __future__ import annotations

import re
from pathlib import PureWindowsPath

WINDOW_TITLE_RE = r"^Claude(\s.*)?$"
# 実機の既知インストール先の実行ファイル名(claude_driver.DEFAULT_EXE_CANDIDATES と対応)
CLAUDE_EXE_NAMES = frozenset({"claude.exe"})


def title_matches(title: str | None) -> bool:
    return bool(title) and re.match(WINDOW_TITLE_RE, title) is not None


def exe_matches(exe_path: str | None) -> bool:
    # exe が取得できない(None/空)は不一致扱い=掴まない(fail-closed)
    if not exe_path:
        return False
    return PureWindowsPath(exe_path).name.lower() in CLAUDE_EXE_NAMES
```

- [ ] **Step 4: テストが通ることを確認する**

Run: `uv run pytest -q`
Expected: 77 passed(既存68 + 新規9)

- [ ] **Step 5: コミット**

```bash
git add src/vac/adapters/window_identity.py tests/test_window_identity.py
git commit -m "feat: add window identity checks (title + process exe)

注入先ウィンドウをタイトル regex だけで選ぶと 'Claude - ...' を名乗る
他アプリを誤マッチしうる(Codex adversarial review 指摘)。プロセス exe 名
の検証を加えて fail-closed 性を強める。判定は Windows 依存 import の無い
純粋関数に切り出し、WSL でもテストできるようにする。"
```

---

### Task 2: driver のウィンドウ解決を EnumWindows + UIAWrapper 直結にする

`_find_window` が WindowSpecification(遅延解決=毎回4.3秒検索)を返すのをやめ、Win32 列挙で hwnd を見つけて実体の UIAWrapper を返す。呼び出し側(`deliver`/`_raise_foreground`/`_resolve` 等)は同じメソッド名を wrapper に対して呼ぶだけなので変更不要(probe で `set_focus`/`is_active`/`descendants` が wrapper 上で動くことは実測済み)。

**Files:**
- Modify: `src/vac/adapters/claude_driver.py`(import 部、定数部、`_find_window`)

- [ ] **Step 1: import と定数を差し替える**

`src/vac/adapters/claude_driver.py` の import 部(現13〜14行)を変更。

変更前:

```python
from pywinauto import Desktop
from pywinauto.findwindows import ElementNotFoundError
from pywinauto.keyboard import send_keys
```

変更後(`Desktop`/`ElementNotFoundError` は `_find_window` でしか使っていないため削除。`WINDOW_TITLE_RE` は window_identity へ移す):

```python
from pywinauto.controls.uiawrapper import UIAWrapper
from pywinauto.keyboard import send_keys
from pywinauto.uia_element_info import UIAElementInfo

from vac.adapters.window_identity import WINDOW_TITLE_RE, exe_matches, title_matches
```

(注: `WINDOW_TITLE_RE` は `check.py` の tree/probe が `from vac.adapters.claude_driver import WINDOW_TITLE_RE` で参照しているため、re-export として import を残す。)

定数部(現32行)から `WINDOW_TITLE_RE = r"^Claude(\s.*)?$"` の行を削除する(window_identity 側に移動済み)。

- [ ] **Step 2: `_find_window` を置き換え、ヘルパーを追加する**

現在の `_find_window`(112〜119行):

```python
    def _find_window(self):
        try:
            window = Desktop(backend="uia").window(title_re=WINDOW_TITLE_RE)
            if window.exists():
                return window
            return None
        except ElementNotFoundError:
            return None
```

を以下に置き換える:

```python
    def _find_window(self):
        # WindowSpecification(遅延解決)を返すと、以後のメソッド呼び出しごとに
        # タイトル regex の全ウィンドウ検索(実測約4.3秒)が再実行される。
        # 実体の UIAWrapper を返し、deliver あたりの解決を最大1回にする(spec 2026-07-11)。
        for hwnd in self._enum_claude_hwnds():
            return self._wrap(hwnd)
        return None

    def _enum_claude_hwnds(self):
        # EnumWindows は Z順(手前が先)に列挙するため、複数候補時は先頭=手前を
        # 採用すれば決定的になる。タイトルだけでなくプロセス exe も検証する(誤マッチ防止)。
        # IsWindowVisible は WS_VISIBLE を見るだけなので最小化中でも TRUE(見逃さない)。
        hwnds: list[int] = []

        def _cb(hwnd, _):
            if (
                win32gui.IsWindowVisible(hwnd)
                and title_matches(win32gui.GetWindowText(hwnd))
                and exe_matches(self._window_exe(hwnd))
            ):
                hwnds.append(hwnd)
            return True

        win32gui.EnumWindows(_cb, None)
        return hwnds

    def _window_exe(self, hwnd):
        # hwnd の所有プロセスの実行ファイルパス。取れなければ None(呼び出し側で不一致扱い)。
        import win32api
        import win32process

        try:
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            proc = win32api.OpenProcess(
                win32con.PROCESS_QUERY_INFORMATION | win32con.PROCESS_VM_READ, False, pid
            )
            try:
                return win32process.GetModuleFileNameEx(proc, 0)
            finally:
                proc.Close()
        except Exception:
            return None

    def _wrap(self, hwnd):
        # UIAElementInfo のシグネチャは (handle_or_elem=None, cache_enable=False)。
        # handle= というキーワードは存在しない(位置引数で渡す)。
        return UIAWrapper(UIAElementInfo(hwnd))
```

- [ ] **Step 3: WSL で回帰確認する**

Run: `python -c "import ast; ast.parse(open('src/vac/adapters/claude_driver.py').read())" && uv run pytest -q && uv run python -m vac.check --help > /dev/null && echo OK`
Expected: 77 passed / OK(import エラー無し)

- [ ] **Step 4: コミット**

```bash
git add src/vac/adapters/claude_driver.py
git commit -m "feat: resolve Claude window once via EnumWindows + UIAWrapper

vac.check probe の実測で、注入の遅さ(約33秒)の真因は
WindowSpecification がメソッド呼び出しごとにタイトル regex の
全ウィンドウ検索(約4.3秒)を再実行することだった(descendants 自体は
0.2秒、set_focus は0秒)。実体の UIAWrapper を返して deliver あたりの
解決を1回にし、初回検索も UIA 照合ではなく Win32 EnumWindows にする。
候補は Z順先頭で決定的に選び、タイトルに加えプロセス exe も検証する。"
```

---

### Task 3: hwnd キャッシュと3点検証・失敗時破棄

デーモン(常駐 orchestrator)は同じ driver インスタンスで繰り返し注入するため、hwnd を保持して2回目以降の列挙も省く。hwnd は OS に再利用されるため、liveness(IsWindow)だけでなくタイトル+exe も再検証する(spec の3点検証)。

**Files:**
- Modify: `src/vac/adapters/claude_driver.py`(`__init__`、`deliver`、`_find_window`)

- [ ] **Step 1: `__init__` にキャッシュ状態を足す**

変更前(90〜93行):

```python
    def __init__(self, exe_path: str | None = None, settle_s: float = 0.3) -> None:
        self._exe_path = exe_path
        self._settle_s = settle_s
        self._clipboard = Win32Clipboard()
```

変更後:

```python
    def __init__(self, exe_path: str | None = None, settle_s: float = 0.3) -> None:
        self._exe_path = exe_path
        self._settle_s = settle_s
        self._clipboard = Win32Clipboard()
        self._cached_hwnd: int | None = None  # 常駐時の再注入で列挙を省く
```

- [ ] **Step 2: `_find_window` にキャッシュ利用を足し、`deliver` の失敗経路で破棄する**

Task 2 で置き換えた `_find_window` を以下に更新:

```python
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
```

`deliver` の except 節(107〜110行)を変更。

変更前:

```python
        except DeliveryError:
            raise
        except Exception as exc:
            raise DeliveryError(str(exc)) from exc
```

変更後(失敗したら次回はフレッシュに列挙し直す):

```python
        except DeliveryError:
            self._cached_hwnd = None
            raise
        except Exception as exc:
            self._cached_hwnd = None
            raise DeliveryError(str(exc)) from exc
```

- [ ] **Step 3: WSL で回帰確認する**

Run: `python -c "import ast; ast.parse(open('src/vac/adapters/claude_driver.py').read())" && uv run pytest -q && echo OK`
Expected: 77 passed / OK

- [ ] **Step 4: コミット**

```bash
git add src/vac/adapters/claude_driver.py
git commit -m "feat: cache Claude hwnd across deliveries with 3-point revalidation

常駐デーモンは同じ driver インスタンスで繰り返し注入するため、
hwnd を保持して2回目以降の列挙を省く。hwnd は OS に再利用されるため
IsWindow だけでなくタイトル+プロセス exe の3点で再検証し、
どれか欠けるか注入が失敗したら破棄して次回列挙し直す
(Codex adversarial review 指摘の採用)。"
```

---

### Task 4: レビューゲートと実機検証

**Files:**
- Modify: `docs/progress.md`(結果に応じて更新)

- [ ] **Step 1: Codex native レビュー(コミット済み実装全体)**

```bash
codex exec review --dangerously-bypass-approvals-and-sandbox --base df2db04 --title "feat: injection speedup - resolve window once, EnumWindows + hwnd cache"
```

Expected: clean("I did not find a discrete defect" 相当)。指摘があれば修正して再実行(clean まで反復)。
注意: `codex exec`(素のプロンプト)がハングした前例あり。5分無出力なら kill して1回だけリトライ、再発なら膠着として報告。

- [ ] **Step 2: 実機検証(shishi に依頼)**

```
git pull ; uv run python -m vac.check inject "診断テスト"
```

確認事項(spec の受け入れ条件):
- (a) チャットモードの新規チャットに「診断テスト」が送信される(Code タブ開始でも)
- (b) ログの合計所要が5秒以下(期待値約3秒: find ~0.1s / raise ~0.5s / resolve 4回×0.2s / settle 1.2s)
- 2回連続で実行し、2回目(デーモン相当のキャッシュ経路は vac.check では効かないが、単発でも5秒以下であること)を確認

- [ ] **Step 3: progress.md を実測結果で更新してコミット**

「速度について」セクションの数値を実測で置き換え、未完タスクから速度を消す(または残課題を明記)。

```bash
git add docs/progress.md
git commit -m "docs: record injection speedup results"
```
