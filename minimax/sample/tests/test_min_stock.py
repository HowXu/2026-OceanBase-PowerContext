"""The exact same target tests must run against baseline and modified code."""

import pytest

from catalog.service import list_products


def test_inclusive_threshold(products):
    assert [p.sku for p in list_products(products, min_stock=5)] == ["B", "C", "E"]


def test_zero_keeps_zero_stock(products):
    assert [p.sku for p in list_products(products, min_stock=0)] == ["A", "B", "C", "E"]


def test_above_all_stock(products):
    assert list_products(products, min_stock=11) == []


def test_filters_compose(products):
    assert [p.sku for p in list_products(products, query="oCeAn", min_stock=5)] == ["C"]


def test_can_include_inactive(products):
    assert [p.sku for p in list_products(products, include_inactive=True, min_stock=8)] == ["C", "D"]


@pytest.mark.parametrize("value", [-1, True, False, 1.5, "5", [], {}])
@pytest.mark.parametrize("empty", [False, True])
def test_invalid_threshold_is_rejected(products, value, empty):
    with pytest.raises(ValueError, match="min_stock"):
        list_products([] if empty else products, min_stock=value)


def test_large_integer_is_valid(products):
    assert list_products(products, min_stock=10**30) == []


def test_integer_subclass_is_not_a_builtin_integer(products):
    class ThresholdInt(int):
        pass

    for items in (products, []):
        with pytest.raises(ValueError, match="min_stock"):
            list_products(items, min_stock=ThresholdInt(5))

