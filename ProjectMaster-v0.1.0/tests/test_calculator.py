import pytest

from project_master.tools.builtin import safe_calculate


def test_basic_arithmetic() -> None:
    assert safe_calculate("2 + 3 * 4") == 14


def test_parentheses_and_power() -> None:
    assert safe_calculate("(2 + 3) ** 2") == 25


def test_rejects_function_calls() -> None:
    with pytest.raises(ValueError):
        safe_calculate("__import__('os').system('echo nope')")


def test_rejects_large_exponent() -> None:
    with pytest.raises(ValueError):
        safe_calculate("2 ** 1000")
