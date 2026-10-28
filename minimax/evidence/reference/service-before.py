"""Inventory catalog. The baseline deliberately lacks the requested min_stock behavior."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Product:
    sku: str
    name: str
    stock: int
    active: bool = True


def list_products(products, *, query="", include_inactive=False, min_stock=None):
    """Return matching products in input order; do not mutate caller data."""
    needle = query.casefold()
    return [
        product
        for product in products
        if (include_inactive or product.active)
        and needle in product.name.casefold()
    ]

