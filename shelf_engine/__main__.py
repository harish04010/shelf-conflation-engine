"""python -m shelf_engine   -> generate inputs, run pipeline v1 and v2, evaluate, write site/data.json"""
import json
import math
from pathlib import Path

from . import catalog as C
from .evaluate import evaluate
from .pipeline import run
from .simulate import AISLES, simulate

ROOT = Path(__file__).resolve().parent.parent


def main():
    data = ROOT / "data"
    (data / "raw").mkdir(parents=True, exist_ok=True)
    cat = C.build_catalog()
    C.save(cat, data / "catalog.json")
    layout = {f"A{a}": cats for a, cats in AISLES.items()}
    (data / "store_layout.json").write_text(json.dumps(layout, indent=1))
    raw, truth, slot_truth = simulate(cat)
    for pss in (1, 2, 3):
        (data / "raw" / f"pass_{pss}.jsonl").write_text("\n".join(json.dumps(r) for r in raw if r["pass_id"] == pss))
    (data / "ground_truth.json").write_text(json.dumps({"detections": truth, "slots": slot_truth}))

    gtins = {p.gtin for p in cat}
    results, evals = {}, {}
    for v in ("v1", "v2"):
        results[v] = run(raw, cat, layout, v, ROOT / "out")
        evals[v] = evaluate(results[v], truth, slot_truth, gtins)
        e, m = evals[v], results[v]["manifest"]
        print(f"{v}: precision {e['auto_precision']:.3f} coverage {e['catalog_coverage']:.3f} wrong {e['wrong_matches']} "
              f"| slots status {e['slots']['status_accuracy']:.3f} product {e['slots']['product_accuracy']:.3f} "
              f"spurious {e['slots']['spurious_records']} missed {e['slots']['missed']} mixed {e['slots']['mixed_clusters']} | tiers {m['tiers']}")
    (ROOT / "out" / "evaluation.json").write_text(json.dumps(evals, indent=2))
    build_site(cat, raw, results, evals)


def build_site(cat, raw, results, evals):
    names = {p.gtin: p.name for p in cat}
    for p in C.new_items():
        names.setdefault(p.gtin, p.name + " (not in catalog)")
    r2, r1 = results["v2"], results["v1"]
    canon = {d.det_id: d for d in r2["dets"]}
    dets = {}
    for det_id, m in r2["matches"].items():
        d = canon[det_id]
        dets[det_id] = {"pass": d.pass_id, "schema": d.schema_version, "text": d.text, "upc": d.upc, "tier": m.tier,
                        "gtin": m.gtin, "score": round(m.score), "note": m.note,
                        "cands": [names[g] for g, _ in m.candidates]}
    slots = []
    for g in r2["golden"]:
        t = g.get("_truth", {})
        slots.append({"id": g["slot_id"], "fx": g["fixture_id"], "x": g["x"], "z": g["z"], "status": g["status"],
                      "product": g["product"], "gtin": g["gtin"], "prev": names.get(g["previous_gtin"]),
                      "conf": g["confidence"], "dets": g["detections"], "unresolved": g["unresolved_text"],
                      "truth": {"status": t.get("status"), "product": names.get(t.get("gtin")), "correct": t.get("correct")} if t else None})
    # v1 vs v2: the same shelf position in both runs (nearest v1 record within 8 cm)
    diffs = []
    for g in r2["golden"]:
        near = [h for h in r1["golden"] if h["fixture_id"] == g["fixture_id"] and math.dist((h["x"], h["z"]), (g["x"], g["z"])) < 0.08]
        if not near:
            continue
        h = min(near, key=lambda h: math.dist((h["x"], h["z"]), (g["x"], g["z"])))
        if (h["gtin"], h["status"]) != (g["gtin"], g["status"]) and g.get("_truth"):
            diffs.append({"slot": g["slot_id"], "v1": {"status": h["status"], "product": names.get(h["gtin"])},
                          "v2": {"status": g["status"], "product": names.get(g["gtin"])},
                          "truth": {"status": g["_truth"]["status"], "product": names.get(g["_truth"]["gtin"])},
                          "v2_right": g["_truth"]["correct"]})
    site = {"summary": {"catalog_products": len(cat), "raw_records": len(raw), "fixtures": len({s["fx"] for s in slots}),
                        "passes": 3},
            "manifests": {v: results[v]["manifest"] for v in results}, "eval": evals,
            "slots": slots, "dets": dets, "diffs": diffs[:40], "diff_count": len(diffs),
            "quarantine": r2["quarantine"][:12], "names": names}
    (ROOT / "site" / "data.json").write_text(json.dumps(site, separators=(",", ":")))
    print("site data:", round((ROOT / "site" / "data.json").stat().st_size / 1024), "KB,", len(diffs), "v1/v2 diffs")


if __name__ == "__main__":
    main()
