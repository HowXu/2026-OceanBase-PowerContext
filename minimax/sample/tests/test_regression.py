from catalog.service import list_products


def test_default_behavior(products):
    assert [p.sku for p in list_products(products)] == ["A", "B", "C", "E"]


def test_explicit_none_preserves_default(products):
    assert list_products(products, min_stock=None) == list_products(products)


def test_case_insensitive_search(products):
    assert [p.sku for p in list_products(products, query="oCeAn")] == ["A", "C"]


def test_unicode_casefold_search(products):
    assert [p.sku for p in list_products(products, query="STRASSE")] == ["E"]


def test_include_inactive(products):
    assert [p.sku for p in list_products(products, include_inactive=True)] == ["A", "B", "C", "D", "E"]


def test_empty_input():
    assert list_products([], min_stock=0) == []


def test_input_not_mutated(products):
    original = list(products)
    list_products(products, query="ocean", min_stock=5)
    assert products == original


def test_new_list_returned(products):
    assert list_products(products) is not products

