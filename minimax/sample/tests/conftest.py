import json
from pathlib import Path

import pytest

from catalog.service import Product


@pytest.fixture
def products():
    data = json.loads((Path(__file__).parents[1] / "products.json").read_text())
    return [Product(**item) for item in data]

