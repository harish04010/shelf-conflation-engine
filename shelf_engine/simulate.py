"""
Simulates what wearable cameras send from a store: three walk-throughs ("passes") of the same
aisles, each producing per-facing detections with a noisy position, OCR'd label text and
sometimes a barcode read.

What makes it realistic (and hard):
  * label text is partial and noisy: abbreviations, dropped brand or variant, OCR errors (0/O, 1/l)
  * sizes are written many ways: "12oz", "355 mL", "2L", "67.6 FL OZ", "(227g)"
  * barcodes are often missing, sometimes misread by one digit, sometimes EAN-13 with a leading zero
  * duplicates (the same facing seen twice in a pass) and price tags read as if they were products
  * between passes the shelf changes: gaps appear, slots are reset to other products, new items show up
  * the edge firmware changed between passes 2 and 3, so pass 3 arrives in schema v2
  * about 1% of records are malformed

The ground truth is written to a separate file and is only read by the evaluation step.
"""
import random

from .catalog import Product, new_items

AISLES = {1: ["soda", "sparkling_water"], 2: ["chips"], 3: ["cereal", "pasta_sauce"]}
SIDES, BAYS, SHELVES, SLOTS = ["L", "R"], 3, 4, 6
SLOT_W, SHELF_H = 0.25, 0.35
PASS_DATES = {1: "2026-11-02", 2: "2026-11-04", 3: "2026-11-06"}
SCHEMA_FOR_PASS = {1: "1", 2: "1", 3: "2"}

VARIANT_FORMS = {"Zero Sugar": ["ZERO SUGAR", "Zero Sug", "0 SUGAR", "ZRO", "zero sugar"], "Diet": ["DIET", "Diet", "Dt"],
                 "Salt & Vinegar": ["SALT & VINEGAR", "S&V", "Salt Vinegar"], "Sour Cream & Onion": ["SOUR CREAM & ONION", "Sour Crm Onion"],
                 "Low Sodium": ["LOW SODIUM", "Lo Sodium"], "Jalapeno": ["JALAPENO", "Jalap."], "BBQ": ["BBQ", "Barbecue"]}
PRICE_TAGS = ["SALE 2/$5", "$3.49 UNIT PRICE 29.1c/OZ", "SAVE $1.00", "$4.99", "25% OFF", "$2.29 EA", "UNIT PRICE $0.21 PER OZ"]
OCR_SWAP = {"O": "0", "o": "0", "l": "1", "I": "1", "S": "5", "B": "8", "e": "c"}


def fixture_ids():
    return [f"A{a}-{s}-B{b}" for a in AISLES for s in SIDES for b in range(1, BAYS + 1)]


def slot_xy(shelf: int, slot: int) -> tuple[float, float]:
    return (slot + 0.5) * SLOT_W, shelf * SHELF_H + 0.15


def size_text(p: Product, rng) -> str:
    v = p.size_label.split()[0]
    if p.size_unit == "fl oz":
        return rng.choice([f"{v} FL OZ", f"{v}oz", f"{v} fl. oz", f"{round(p.size_amount)}mL", f"{round(p.size_amount)} ML"])
    if p.size_unit == "L":
        return rng.choice([f"{v}L", f"{v} LITER", f"{p.size_amount / 29.5735:.1f} FL OZ"])
    return rng.choice([f"{v} OZ", f"{v}oz", f"({round(p.size_amount)}g)", f"{round(p.size_amount)} g"])


def label_text(p: Product, rng) -> str:
    parts = []
    if rng.random() < 0.8:
        parts.append(rng.choice([p.brand, p.brand.upper(), p.brand.split()[0]]))
    if rng.random() < 0.95:
        parts.append(rng.choice([p.line, p.line.upper()]))
    if p.variant != "Original" and rng.random() < 0.88:
        parts.append(rng.choice(VARIANT_FORMS.get(p.variant, [p.variant, p.variant.upper()])))
    if rng.random() < 0.7:
        parts.insert(rng.choice([0, len(parts)]), size_text(p, rng))
    text = " ".join(parts) or p.line
    return "".join(OCR_SWAP[c] if c in OCR_SWAP and rng.random() < 0.035 else c for c in text)


def barcode_read(p: Product, rng) -> str | None:
    r = rng.random()
    if r < 0.42:
        return ("0" + p.gtin) if rng.random() < 0.3 else p.gtin       # sometimes reported as EAN-13
    if r < 0.50:
        i = rng.randrange(11)                                          # one misread digit
        return p.gtin[:i] + str((int(p.gtin[i]) + rng.randint(1, 9)) % 10) + p.gtin[i + 1:]
    return None


def planogram(catalog: list[Product], rng) -> dict:
    by_cat: dict[str, list[Product]] = {}
    for p in catalog:
        by_cat.setdefault(p.category, []).append(p)
    pogs = {}
    for fx in fixture_ids():
        cats = AISLES[int(fx[1])]
        for shelf in range(SHELVES):
            pool = by_cat[cats[shelf % len(cats)]]
            slot = 0
            while slot < SLOTS:
                p = rng.choice(pool)
                for _ in range(min(rng.choice([1, 2, 2, 3]), SLOTS - slot)):
                    pogs[(fx, shelf, slot)] = p
                    slot += 1
    return pogs


def evolve(state: dict, orig: dict, catalog, rng, gap_rate: float, restock: float, reset_rate: float, new_rate: float):
    by_cat: dict[str, list[Product]] = {}
    for p in catalog:
        by_cat.setdefault(p.category, []).append(p)
    news = new_items()
    nxt = {}
    for key, p in sorted(state.items(), key=lambda kv: kv[0]):
        if p is None:
            nxt[key] = orig[key] if rng.random() < restock else None
            continue
        r = rng.random()
        if r < gap_rate:
            nxt[key] = None
        elif r < gap_rate + reset_rate:
            nxt[key] = rng.choice([q for q in by_cat[p.category] if q.gtin != p.gtin])
        elif r < gap_rate + reset_rate + new_rate:
            cands = [q for q in news if q.category == p.category] or news
            nxt[key] = rng.choice(cands)
        else:
            nxt[key] = p
    return nxt


def simulate(catalog: list[Product], seed: int = 11):
    rng = random.Random(seed)
    p1 = planogram(catalog, rng)
    states = {1: dict(p1)}
    states[2] = evolve(p1, p1, catalog, rng, 0.04, 0.0, 0.0, 0.0)
    states[3] = evolve(states[2], p1, catalog, rng, 0.04, 0.5, 0.03, 0.02)

    records, truth = [], {}
    for pss, state in states.items():
        n = 0
        for (fx, shelf, slot), p in sorted(state.items(), key=lambda kv: kv[0]):
            if p is None:
                continue
            copies = (1 if rng.random() < 0.96 else 0) + (1 if rng.random() < 0.08 else 0)
            for _ in range(copies):
                n += 1
                x, z = slot_xy(shelf, slot)
                det = {"det_id": f"p{pss}-{n:05d}", "pass_id": pss, "fixture_id": fx,
                       "x": x + rng.gauss(0, 0.025), "z": z + rng.gauss(0, 0.02),
                       "text": label_text(p, rng), "upc": barcode_read(p, rng), "conf": round(rng.uniform(0.55, 0.99), 2)}
                records.append(det)
                truth[det["det_id"]] = {"slot": [fx, shelf, slot], "gtin": p.gtin, "kind": "product"}
        for fx in fixture_ids():                                          # price tags read as products
            for _ in range(rng.choice([0, 0, 1, 1, 2])):
                n += 1
                shelf = rng.randrange(SHELVES)
                det = {"det_id": f"p{pss}-{n:05d}", "pass_id": pss, "fixture_id": fx,
                       "x": rng.uniform(0.05, SLOTS * SLOT_W - 0.05), "z": shelf * SHELF_H + 0.01 + rng.gauss(0, 0.01),
                       "text": rng.choice(PRICE_TAGS), "upc": None, "conf": round(rng.uniform(0.4, 0.9), 2)}
                records.append(det)
                truth[det["det_id"]] = {"slot": None, "gtin": None, "kind": "price_tag"}

    raw = []
    for d in records:
        pss = d["pass_id"]
        ts = f"{PASS_DATES[pss]}T{9 + int(d['x'] * 2) % 3:02d}:{rng.randrange(60):02d}:00Z"
        if SCHEMA_FOR_PASS[pss] == "1":
            rec = {"schema_version": "1", "det_id": d["det_id"], "pass_id": pss, "captured_at": ts, "fixture_id": d["fixture_id"],
                   "x_m": round(d["x"], 4), "z_m": round(d["z"], 4), "text": d["text"], "upc": d["upc"], "conf": d["conf"]}
        else:   # firmware 2: renamed fields, centimeters, barcode object, bounding box
            rec = {"schema_version": "2", "det_id": d["det_id"], "pass_id": pss, "captured_at": ts, "fixture_id": d["fixture_id"],
                   "pos_cm": {"x": round(d["x"] * 100, 2), "z": round(d["z"] * 100, 2)}, "ocr_text": d["text"],
                   "barcode": {"value": d["upc"], "symbology": ("EAN13" if d["upc"] and len(d["upc"]) == 13 else "UPCA") if d["upc"] else None},
                   "conf": d["conf"], "bbox_px": [rng.randint(60, 140), rng.randint(120, 260)]}
        if rng.random() < 0.01:                                            # malformed records
            truth[d["det_id"]]["malformed"] = True
            if rec["schema_version"] == "1":
                rec.pop(rng.choice(["fixture_id", "x_m"]))
            else:
                rec.pop("pos_cm")
        raw.append(rec)
    slot_truth = {f"{fx}|{sh}|{sl}": {str(pss): (states[pss].get((fx, sh, sl)).gtin if states[pss].get((fx, sh, sl)) else None) for pss in states}
                  for (fx, sh, sl) in p1}
    return raw, truth, slot_truth
