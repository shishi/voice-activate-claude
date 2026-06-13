"""src/vac/devices.py — 入力デバイスの選択(純粋ロジック、sounddevice非依存)"""
from __future__ import annotations


def resolve_input_device(query, devices):
    """入力デバイスの指定を sounddevice の device 引数用の値に解決する。

    query:
        None -> None(OS既定デバイスを使う)
        int  -> そのまま(デバイスindex)
        str  -> 名前にその文字列を含む最初の「入力可能な」デバイスのindex
    devices: sounddevice.query_devices() が返す辞書のリスト
             (各要素は "name" と "max_input_channels" を持つ)
    一致する入力デバイスが無ければ ValueError。
    """
    if query is None or isinstance(query, int):
        return query
    needle = query.casefold()
    for index, device in enumerate(devices):
        if device.get("max_input_channels", 0) <= 0:
            continue
        if needle in device["name"].casefold():
            return index
    raise ValueError(
        f"no input device matches {query!r}; "
        f"run `python -m vac.check devices` to list available devices"
    )
