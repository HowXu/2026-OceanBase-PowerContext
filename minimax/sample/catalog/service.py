"""Inventory catalog."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Product:
    sku: str
    name: str
    stock: int
    active: bool = True


def _validate_min_stock(min_stock):
    """Validate the optional min_stock threshold.

    min_stock must be a non-negative built-in int. Booleans, integer
    subclasses, floats, strings and containers are rejected.
    """
    if min_stock is None:
        return
    if type(min_stock) is not int:
        raise ValueError("min_stock must be a non-negative built-in int or None")
    if min_stock < 0:
        raise ValueError("min_stock must be a non-negative built-in int or None")


def list_products(products, *, query="", include_inactive=False, min_stock=None):
    """Return matching products in input order; do not mutate caller data."""
    _validate_min_stock(min_stock)
    needle = query.casefold()
    return [
        product
        for product in products
        if (include_inactive or product.active)
        and needle in product.name.casefold()
        and (min_stock is None or product.stock >= min_stock)
    ]
