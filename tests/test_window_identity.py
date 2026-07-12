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
        assert exe_matches(r"C:\Users\shishi\AppData\Local\AnthropicClaude\claude.exe")

    def test_case_insensitive(self):
        assert exe_matches(r"C:\Program Files\claude-desktop\Claude.EXE")

    def test_rejects_other_exe(self):
        # ブラウザのタブが "Claude - ..." を名乗っていても exe で弾く
        assert not exe_matches(r"C:\Program Files\BraveSoftware\brave.exe")

    def test_rejects_none_and_empty(self):
        # exe が取得できない場合は不一致扱い(fail-closed)
        assert not exe_matches(None)
        assert not exe_matches("")
