"""
Tiered matching of shelf detections to the master catalog. Each tier is cheaper and more certain
than the next; a detection only moves on when the current tier can't resolve it with confidence.

  v2 (current)                                        v1 (first version, kept for comparison)
  0 filter    price tags and shelf signs (regex)      -
  1 upc       valid barcode, exact catalog hit        exact string lookup, no check digit or EAN-13 handling
  2 upc_repair one-digit barcode misread + text agrees -
  2 rules     size + brand blocking, fuzzy text,      fuzzy text over the whole catalog, no margin
              variant guard, margin over runner-up
  3 spatial   (in conflate.py) same-slot consensus    -
  4 llm       (in llm.py) only the ambiguous residue  -
"""
from dataclasses import dataclass, field

from rapidfuzz import fuzz

from . import gtin as G
from .catalog import Product
from .contracts import Detection
from .normalize import clean_text, is_price_tag, parse_sizes, size_matches

VARIANT_WORDS = ["zero sugar", "diet", "cherry", "black cherry", "lime", "grapefruit", "lemon", "unflavored", "sea salt",
                 "salt vinegar", "jalapeno", "bbq", "sour cream onion", "cinnamon", "almond", "low sodium"]
ACCEPT, MARGIN, PLAUSIBLE = 85, 6, 60
CONFIDENT = {"upc", "upc_repair", "rules", "spatial", "llm"}


@dataclass
class Match:
    det_id: str
    tier: str                      # filtered | upc | upc_repair | rules | ambiguous | no_match | spatial | llm | review
    gtin: str | None = None
    score: float = 0.0
    candidates: list = field(default_factory=list)   # [(gtin, score)] for ambiguous detections
    note: str = ""


def _norm_name(p: Product) -> str:
    return clean_text(p.base_name)


def _variants_in(t: str) -> set[str]:
    found = {v for v in VARIANT_WORDS if f" {v} " in f" {t} "}
    if "black cherry" in found:
        found.discard("cherry")
    return found


class Matcher:
    def __init__(self, catalog: list[Product], layout: dict[str, list[str]], version: str = "v2"):
        self.version = version
        self.by_gtin = {p.gtin: p for p in catalog}
        self.catalog = catalog
        self.layout = layout                                          # aisle -> categories (reference data)
        self.names = {p.gtin: _norm_name(p) for p in catalog}
        self.full_names = {p.gtin: clean_text(p.name) + " " + p.size_label.lower() for p in catalog}
        self.brands = sorted({p.brand for p in catalog})

    # ---------- v1: the first, naive version ----------
    def _match_v1(self, d: Detection) -> Match:
        if d.upc and d.upc in self.by_gtin:
            return Match(d.det_id, "upc", d.upc, 100)
        t = d.text.lower()
        best = max(self.catalog, key=lambda p: fuzz.token_set_ratio(t, self.full_names[p.gtin]))
        s = fuzz.token_set_ratio(t, self.full_names[best.gtin])
        return Match(d.det_id, "rules", best.gtin, s) if s >= ACCEPT else Match(d.det_id, "no_match", score=s)

    # ---------- v2 ----------
    def _brands_in(self, t: str) -> list[str]:
        found = []
        for b in self.brands:
            first = b.lower().split()[0]
            if fuzz.partial_ratio(b.lower(), t) >= 90 or any(fuzz.ratio(tok, first) >= 85 for tok in t.split() if len(tok) >= 4):
                found.append(b)
        return found

    def _score(self, t: str, tv: set[str], sizes, p: Product) -> float:
        s = fuzz.token_set_ratio(t, self.names[p.gtin])
        if tv - _variants_in(self.names[p.gtin]):
            s -= 30                                                  # text names a variant this product isn't
        if sizes:
            s += 8 if size_matches(sizes, p.size_dim, p.size_amount) else -25
        return s

    def match(self, d: Detection) -> Match:
        if self.version == "v1":
            return self._match_v1(d)
        if is_price_tag(d.text):
            return Match(d.det_id, "filtered", note="price tag or shelf sign")
        code, status = G.normalize(d.upc)
        if code and code in self.by_gtin:
            return Match(d.det_id, "upc", code, 100)
        if code:   # a barcode that passes its check digit but isn't in the catalog: trust it, don't force a text match
            return Match(d.det_id, "no_match", note=f"valid barcode {code} not in catalog (likely a new item)")

        t, sizes = clean_text(d.text), parse_sizes(d.text)
        tv = _variants_in(t)
        cats = self.layout.get(d.fixture_id.split("-")[0], [])
        pool = [p for p in self.catalog if p.category in cats] or self.catalog
        brands = self._brands_in(t)
        if len(brands) == 1:
            pool = [p for p in pool if p.brand == brands[0]] or pool
        ranked = sorted(((self._score(t, tv, sizes, p), p.gtin) for p in pool), reverse=True)
        best, second = ranked[0], (ranked[1] if len(ranked) > 1 else (0, None))

        if status == "bad_check_digit" and d.upc:                    # barcode misread by one digit?
            read = d.upc[-12:]
            near = [g for s, g in ranked[:8] if s >= PLAUSIBLE and G.hamming(read, g) == 1]
            if len(near) == 1:
                return Match(d.det_id, "upc_repair", near[0], best[0], note=f"barcode {read} failed its check digit; one digit from {near[0]}")
        if best[0] >= ACCEPT and best[0] - second[0] >= MARGIN:
            return Match(d.det_id, "rules", best[1], best[0])
        note = "valid barcode not in catalog" if code else ""
        if best[0] >= PLAUSIBLE:
            # keep every near-tie: cutting at a fixed top 5 silently dropped the right product when scores tied
            cands = [(g, s) for s, g in ranked[:12] if s >= best[0] - 10]
            return Match(d.det_id, "ambiguous", score=best[0], candidates=cands, note=note)
        return Match(d.det_id, "no_match", score=best[0], note=note or "no plausible catalog product")
