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


def test_clears_clipboard_when_it_was_empty():
    clipboard = FakeClipboard(initial=None)
    with ClipboardGuard(clipboard, "x"):
        assert clipboard.content == "x"
    assert clipboard.content is None
