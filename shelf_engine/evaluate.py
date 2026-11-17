"""Scores a pipeline run against the simulator's ground truth. Only this module reads the truth file."""
from collections import Counter

from .match import CONFIDENT


def truth_status(t: dict, in_catalog: set[str]) -> str:
    if t["3"] is None:
        return "gap"
    if t["3"] not in in_catalog:
        return "new_item"
    return "changed" if t["1"] and t["1"] != t["3"] else "in_stock"


def evaluate(result: dict, truth: dict, slot_truth: dict, catalog_gtins: set[str]) -> dict:
    matches = result["matches"]
    rows = Counter()
    per_tier: dict[str, Counter] = {}
    for det_id, m in matches.items():
        t = truth[det_id]
        if t["kind"] == "price_tag":
            kind = "price_tag"
        else:
            kind = "catalog" if t["gtin"] in catalog_gtins else "new_item"
        c = per_tier.setdefault(m.tier, Counter())
        c["n"] += 1
        if m.tier in CONFIDENT:
            ok = m.gtin == t["gtin"]
            c["correct"] += ok
            rows["confident"] += 1
            rows["confident_correct"] += ok
            if kind == "price_tag":
                rows["price_tags_matched_as_products"] += 1
        elif m.tier == "filtered":
            c["correct"] += kind == "price_tag"
        elif m.tier == "no_match":
            c["correct"] += kind in ("new_item", "price_tag")
        rows[f"{kind}_total"] += 1
        if kind == "catalog" and m.tier in CONFIDENT and m.gtin == t["gtin"]:
            rows["catalog_resolved_correctly"] += 1

    # golden records -> true slots (majority of their detections' true slot)
    owner = {}
    for g in result["golden"]:
        votes = Counter("|".join(map(str, truth[d]["slot"])) if truth[d]["slot"] else "price_tag" for d in g["detections"])
        key, n = votes.most_common(1)[0]
        owner.setdefault(key, []).append((n, g))
        g["_mixed"] = len(votes) > 1
    spurious = len(owner.get("price_tag", []))
    status_ok, product_ok, product_n, missed = 0, 0, 0, 0
    confusion: dict[str, Counter] = {}
    examples = []
    for key, t in slot_truth.items():
        ts = truth_status(t, catalog_gtins)
        cands = owner.get(key)
        if not cands:
            missed += 1
            confusion.setdefault(ts, Counter())["(no record)"] += 1
            continue
        g = max(cands, key=lambda x: x[0])[1]
        confusion.setdefault(ts, Counter())[g["status"]] += 1
        s_ok = g["status"] == ts
        status_ok += s_ok
        if ts in ("in_stock", "changed"):
            product_n += 1
            p_ok = g["gtin"] == t["3"]
            product_ok += p_ok
        else:
            p_ok = s_ok
        g["_truth"] = {"status": ts, "gtin": t["3"], "previous_gtin": t["1"], "correct": bool(s_ok and p_ok)}
    n_slots = len(slot_truth)
    return {
        "detections": dict(rows),
        "per_tier": {k: dict(v) for k, v in sorted(per_tier.items())},
        "auto_precision": round(rows["confident_correct"] / max(rows["confident"], 1), 4),
        "catalog_coverage": round(rows["catalog_resolved_correctly"] / max(rows["catalog_total"], 1), 4),
        "wrong_matches": rows["confident"] - rows["confident_correct"],
        "slots": {"true_slots": n_slots, "missed": missed, "spurious_records": spurious,
                  "mixed_clusters": sum(1 for g in result["golden"] if g.get("_mixed")),
                  "split_slots": sum(1 for k, v in owner.items() if k != "price_tag" and len(v) > 1),
                  "status_accuracy": round(status_ok / n_slots, 4),
                  "product_accuracy": round(product_ok / max(product_n, 1), 4),
                  "confusion": {k: dict(v) for k, v in confusion.items()}},
    }
