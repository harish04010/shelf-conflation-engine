"""
The master product catalog: the "source of truth" every shelf detection must resolve to.

Products are fictional (public product databases aren't reachable from the build environment),
but structured like a real retail catalog: brand, product line, variant, pack size, and a valid
GTIN-12 with a correct check digit. Several products differ only by variant or size, which is
exactly what makes shelf matching hard (Cola vs Cola Zero Sugar, 12 fl oz vs 20 fl oz).
"""
import json
import random
from dataclasses import asdict, dataclass

from .gtin import check_digit

FL_OZ_ML = 29.5735
OZ_G = 28.3495

CATEGORIES = {
    "soda": {"lines": ["Cola", "Lemon Lime", "Root Beer", "Orange", "Ginger Ale"],
             "variants": ["Original", "Zero Sugar", "Diet", "Cherry"], "sizes": [(12, "fl oz"), (20, "fl oz"), (2, "L")]},
    "sparkling_water": {"lines": ["Sparkling Water"], "variants": ["Lime", "Grapefruit", "Black Cherry", "Unflavored", "Lemon"],
                        "sizes": [(12, "fl oz"), (1, "L")]},
    "chips": {"lines": ["Kettle Chips", "Tortilla Chips", "Potato Chips"],
              "variants": ["Sea Salt", "Salt & Vinegar", "Jalapeno", "BBQ", "Sour Cream & Onion"], "sizes": [(8, "oz"), (13, "oz")]},
    "cereal": {"lines": ["Oat Clusters", "Corn Flakes", "Honey Crunch", "Granola"], "variants": ["Original", "Cinnamon", "Almond"],
               "sizes": [(12, "oz"), (18, "oz")]},
    "pasta_sauce": {"lines": ["Marinara", "Tomato Basil", "Vodka Sauce", "Arrabbiata"], "variants": ["Original", "Low Sodium"],
                    "sizes": [(24, "oz")]},
}
BRANDS = [("Summit Fizz", "soda", "081234"), ("Polar Peak", "soda", "081277"),
          ("Brightwater", "sparkling_water", "082210"), ("Clearbrook", "sparkling_water", "082245"),
          ("Kettle Ridge", "chips", "083301"), ("Crisp Valley", "chips", "083388"),
          ("Harvest Lane", "cereal", "084412"), ("Morning Field", "cereal", "084490"),
          ("Golden Acre", "pasta_sauce", "085517"), ("Nonna Rosa", "pasta_sauce", "085560")]
# Products that appear on shelves later but were never added to the catalog (new items).
NEW_ITEMS = [("Lumen Soda", "soda", "089901", "Yuzu Spritz", "Original", (12, "fl oz")),
             ("Lumen Soda", "soda", "089901", "Cola", "Vanilla", (12, "fl oz")),
             ("Fieldstone", "chips", "089902", "Kettle Chips", "Hot Honey", (8, "oz")),
             ("Brightwater", "sparkling_water", "082210", "Sparkling Water", "Mango", (12, "fl oz")),
             ("Harvest Lane", "cereal", "084412", "Protein Clusters", "Original", (16, "oz")),
             ("Nonna Rosa", "pasta_sauce", "085560", "Puttanesca", "Original", (24, "oz"))]


@dataclass(frozen=True)
class Product:
    gtin: str
    brand: str
    category: str
    line: str
    variant: str
    size_value: float
    size_unit: str
    size_dim: str      # "ml" (volume) or "g" (mass)
    size_amount: float  # size in ml or g

    @property
    def size_label(self) -> str:
        v = int(self.size_value) if float(self.size_value).is_integer() else self.size_value
        return f"{v} {self.size_unit}"

    @property
    def base_name(self) -> str:
        return " ".join([self.brand, self.line] + ([] if self.variant == "Original" else [self.variant]))

    @property
    def name(self) -> str:
        return f"{self.base_name} {self.size_label}"


def size_canon(value: float, unit: str) -> tuple[str, float]:
    return {"fl oz": ("ml", value * FL_OZ_ML), "L": ("ml", value * 1000), "oz": ("g", value * OZ_G)}[unit]


def make_gtin(prefix: str, item: int) -> str:
    body = f"{prefix}{item:05d}"
    return body + str(check_digit(body))


def build_catalog(seed: int = 7) -> list[Product]:
    rng = random.Random(seed)
    out = []
    for brand, cat, prefix in BRANDS:
        spec, item = CATEGORIES[cat], 0
        for line in spec["lines"]:
            for variant in spec["variants"]:
                if variant != "Original" and rng.random() > 0.75:
                    continue
                sizes = [s for s in spec["sizes"] if rng.random() < 0.8] or spec["sizes"][:1]
                for v, u in sizes:
                    item += 1
                    dim, amt = size_canon(v, u)
                    out.append(Product(make_gtin(prefix, item), brand, cat, line, variant, v, u, dim, round(amt, 1)))
    return out


def new_items() -> list[Product]:
    out = []
    for i, (brand, cat, prefix, line, variant, (v, u)) in enumerate(NEW_ITEMS):
        dim, amt = size_canon(v, u)
        out.append(Product(make_gtin(prefix, 900 + i), brand, cat, line, variant, v, u, dim, round(amt, 1)))
    return out


def save(products: list[Product], path) -> None:
    path.write_text(json.dumps([asdict(p) for p in products], indent=1))


def load(path) -> list[Product]:
    return [Product(**r) for r in json.loads(path.read_text())]
