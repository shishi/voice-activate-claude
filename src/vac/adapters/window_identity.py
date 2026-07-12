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
