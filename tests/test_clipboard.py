"""tests/test_clipboard.py"""
import pytest

from vac.clipboard import ClipboardGuard


class FakeClipboard:
    def __init__(self, initial: str | None = "before"):
        self.content = initial

    def get_text(self) -> str | None:
        return self.content

    def set_text(self, text: str) -> None:
        self.content = text

    def clear(self) -> None:
        self.content = None


def test_sets_text_inside_context():
    clipboard = FakeClipboard()
    with ClipboardGuard(clipboard, "新しいテキスト"):
        assert clipboard.content == "新しいテキスト"


def test_restores_previous_text_on_exit():
    clipboard = FakeClipboard(initial="before")
    with ClipboardGuard(clipboard, "x"):
        pass
    assert clipboard.content == "before"


def test_restores_even_when_body_raises():
    clipboard = FakeClipboard(initial="before")
    with pytest.raises(RuntimeError):
        with ClipboardGuard(clipboard, "x"):
            raise RuntimeError("paste failed")
    assert clipboard.content == "before"


def test_restores_empty_string_clipboard():
    clipboard = FakeClipboard(initial="")
    with ClipboardGuard(clipboard, "x"):
        pass
    assert clipboard.content == ""  # "" != None なので clear() してはいけない


def test_clears_clipboard_when_it_was_empty():
    clipboard = FakeClipboard(initial=None)
    with ClipboardGuard(clipboard, "x"):
        assert clipboard.content == "x"
    assert clipboard.content is None


def test_restore_failure_does_not_mask_body_exception():
    class FailingRestoreClipboard(FakeClipboard):
        def __init__(self) -> None:
            super().__init__(initial="before")
            self._set_calls = 0

        def set_text(self, text: str) -> None:
            self._set_calls += 1
            if self._set_calls > 1:  # 2回目 = 復元時に失敗
                raise OSError("clipboard service died")
            super().set_text(text)

    clipboard = FailingRestoreClipboard()
    with pytest.raises(RuntimeError, match="paste failed"):
        with ClipboardGuard(clipboard, "x"):
            raise RuntimeError("paste failed")
