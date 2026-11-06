import sys
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import calc


def test_basic():
    assert calc.evaluate("1+2*3") == Decimal("7")


def test_parentheses_and_neg():
    assert calc.evaluate("-(1.5-2.5)*100") == Decimal("100.0")


def test_thousands_and_fullwidth_comma():
    assert calc.evaluate("1,234.5 + 1，000") == Decimal("2234.5")


def test_division():
    assert calc.evaluate("(174144069958.25-147692500000)/147692500000*100") == \
        Decimal("17.90989383905750122721194373")


def test_reject_names():
    with pytest.raises(calc.CalcError):
        calc.evaluate("__import__('os').system('dir')")


def test_reject_div_zero():
    with pytest.raises(calc.CalcError):
        calc.evaluate("1/0")


def test_fmt():
    assert calc.calculate("1/3")[0] == "0.333333"
