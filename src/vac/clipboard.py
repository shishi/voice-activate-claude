"""src/vac/clipboard.py"""
from __future__ import annotations

from typing import Protocol


class ClipboardPort(Protocol):
    def get_text(self) -> str | None: ...
    def set_text(self, text: str) -> None: ...
    def clear(self) -> None: ...


class ClipboardGuard:
    """クリップボードに一時テキストを置き、抜けるとき必ず元に戻す。"""

    def __init__(self, clipboard: ClipboardPort, text: str) -> None:
        self._clipboard = clipboard
        self._text = text
        self._saved: str | None = None

    def __enter__(self) -> "ClipboardGuard":
        self._saved = self._clipboard.get_text()
        self._clipboard.set_text(self._text)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._saved is None:
            self._clipboard.clear()
        else:
            self._clipboard.set_text(self._saved)
