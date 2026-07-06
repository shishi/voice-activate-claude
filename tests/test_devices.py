"""tests/test_devices.py"""
import pytest

from vac.devices import resolve_input_device

# sd.query_devices() が返す形を模した最小のデバイス一覧
DEVICES = [
    {"name": "Microsoft Sound Mapper - Input", "max_input_channels": 2},
    {"name": "LARK M2 (Wireless microphone)", "max_input_channels": 2},
    {"name": "マイク (Logicool BRIO)", "max_input_channels": 2},
    {"name": "スピーカー (EDIFIER M60)", "max_input_channels": 0},
]


def test_none_returns_none():
    assert resolve_input_device(None, DEVICES) is None


def test_int_passes_through():
    assert resolve_input_device(2, DEVICES) == 2


def test_int_index_must_be_input_capable():
    # index 3 = EDIFIER スピーカー (max_input_channels=0)
    with pytest.raises(ValueError, match="not an input device"):
        resolve_input_device(3, DEVICES)


def test_int_index_out_of_range_raises():
    with pytest.raises(ValueError, match="out of range"):
        resolve_input_device(99, DEVICES)


def test_string_matches_substring():
    assert resolve_input_device("BRIO", DEVICES) == 2


def test_string_match_is_case_insensitive():
    assert resolve_input_device("brio", DEVICES) == 2


def test_returns_first_matching_input_device():
    # "microphone" は LARK(index 1)にマッチ。最初の入力デバイスを返す
    assert resolve_input_device("microphone", DEVICES) == 1


def test_skips_output_only_devices():
    # スピーカーは max_input_channels=0 なので入力候補から除外。マッチなしで例外
    with pytest.raises(ValueError, match="EDIFIER"):
        resolve_input_device("EDIFIER", DEVICES)


def test_no_match_raises_with_query_in_message():
    with pytest.raises(ValueError, match="nonexistent"):
        resolve_input_device("nonexistent", DEVICES)


def test_list_input_devices_filters_output_only_and_pairs_index_name():
    from vac.devices import list_input_devices
    devices = [
        {"name": "Mapper", "max_input_channels": 2},
        {"name": "BRIO", "max_input_channels": 2},
        {"name": "Speaker", "max_input_channels": 0},
    ]
    assert list_input_devices(devices) == [(0, "Mapper"), (1, "BRIO")]
