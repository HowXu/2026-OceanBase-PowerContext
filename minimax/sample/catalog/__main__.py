import argparse
import json
from dataclasses import asdict
from pathlib import Path

from .service import Product, list_products

parser = argparse.ArgumentParser(description="Inventory catalog demo")
parser.add_argument("--query", default="")
parser.add_argument("--min-stock", type=int)
parser.add_argument("--include-inactive", action="store_true")
args = parser.parse_args()
data = json.loads((Path(__file__).parents[1] / "products.json").read_text())
result = list_products([Product(**item) for item in data], query=args.query,
                       min_stock=args.min_stock, include_inactive=args.include_inactive)
print(json.dumps([asdict(item) for item in result], ensure_ascii=False, indent=2))

