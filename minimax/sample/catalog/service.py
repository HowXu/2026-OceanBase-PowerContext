"""Codex reference implementation for local checks; not a MiniMax development record."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Product:
    sku: str
    name: str
    stock: int
    active: bool = True


def list_products(products, *, query="", include_inactive=False, min_stock=None):
    """Return matching products in input order; do not mutate caller data."""
    if min_stock is not None and (type(min_stock) is not int or min_stock < 0):
        raise ValueError("min_stock must be a non-negative integer")
    needle = query.casefold()
    return [
        product
        for product in products
        if (include_inactive or product.active)
        and needle in product.name.casefold()
        and (min_stock is None or product.stock >= min_stock)
    ]

