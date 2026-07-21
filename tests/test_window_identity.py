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

    def test_device_form_path(self):
        # GetProcessImageFileName はデバイス形式パスを返す。basename 判定で通ること
        assert exe_matches(r"\Device\HarddiskVolume4\Users\you\AppData\Local\AnthropicClaude\claude.exe")

    def test_allowed_names_override_accepts_custom_exe(self):
        # --exe で別名バイナリを指定した場合は許可集合に追加されて通る
        allowed = frozenset({"claude.exe", "claude-portable.exe"})
        assert exe_matches(r"D:\apps\claude-portable.exe", allowed)

    def test_default_rejects_custom_exe(self):
        assert not exe_matches(r"D:\apps\claude-portable.exe")
