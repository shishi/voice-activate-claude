"""tests/test_check.py"""
from vac.check import _parse_device


def test_parse_device_none():
    assert _parse_device(None) is None


def test_parse_device_digit_string_becomes_int():
    assert _parse_device("4") == 4


def test_parse_device_signed_becomes_int():
    assert _parse_device("-1") == -1


def test_parse_device_name_passes_through():
    assert _parse_device("BRIO") == "BRIO"
