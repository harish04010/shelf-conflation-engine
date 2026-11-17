"""
Runs one pipeline version end to end and writes a versioned, reproducible output:

  out/<pipeline version>/run-<input hash>/
      manifest.json        pipeline + schema versions, input hash, counts, cost
      matches.jsonl        one row per detection: tier, product, score, note
      golden_records.json  one row per shelf slot, with lineage back to detections
      quarantine.jsonl     records that broke the data contract, with the reason

The same input and version always produce the same run folder, so outputs can be diffed across versions.
"""
import hashlib
import json
from collections import Counter
from dataclasses import asdict
from pathlib import Path

from . import llm
from .catalog import Product
from .conflate import cluster, golden_records, spatial_consensus
from .contracts import to_canonical
from .match import Matcher

PIPELINE_CHANGES = {
    "v1": ["Exact barcode lookup", "Fuzzy text match over the whole catalog, accepted at score 85"],
    "v2": ["Price tags and shelf signs filtered by regex before matching",
           "Barcodes validated by check digit; EAN-13 leading zeros normalized",
           "One-digit barcode misreads repaired when the text agrees",
           "Pack sizes parsed (12oz, 355 mL, 2L, 227g) and used to rule candidates in or out",
           "Brand and aisle used to narrow candidates; variant guard (Zero Sugar is not Original)",
           "A match needs a 6-point lead over the runner-up, otherwise it is ambiguous",
           "Ambiguous detections resolved by agreement within the same shelf slot",
           "Only the remaining residue goes to an LLM (or a review queue)"],
}


def input_hash(raw: list[dict]) -> str:
    return hashlib.sha256(json.dumps(raw, sort_keys=True).encode()).hexdigest()[:12]


def run(raw: list[dict], catalog: list[Product], layout: dict, version: str, out_root: Path) -> dict:
    names = {p.gtin: p.name for p in catalog}
    dets, quarantine = [], []
    for rec in raw:
        d, err = to_canonical(rec)
        if d:
            dets.append(d)
        else:
            quarantine.append({"det_id": rec.get("det_id"), "schema_version": rec.get("schema_version"), "reason": err})

    m = Matcher(catalog, layout, version)
    matches = {d.det_id: m.match(d) for d in dets}
    placed = [d for d in dets if matches[d.det_id].tier != "filtered"]
    slots = cluster(placed)
    spatial = spatial_consensus(placed, matches, slots) if version != "v1" else 0

    text = {d.det_id: d.text for d in dets}
    llm_calls, llm_tokens = 0, 0
    for mt in matches.values():
        if mt.tier == "ambiguous":
            _, tk = llm.resolve(mt, text[mt.det_id], names)
            llm_calls, llm_tokens = llm_calls + 1, llm_tokens + tk

    # Baseline for comparison: send every detection to an LLM with a 20-product shortlist from its aisle.
    by_aisle = {}
    for p in catalog:
        by_aisle.setdefault(p.category, []).append(p)
    all_llm_tokens = 0
    for d in dets:
        pool = [p for c in layout.get(d.fixture_id.split("-")[0], []) for p in by_aisle.get(c, [])][:20]
        all_llm_tokens += llm.tokens(llm.prompt_for(d.text, [(p.gtin, p.name) for p in pool]))

    golden = golden_records(placed, matches, slots, names)
    tiers = Counter(mt.tier for mt in matches.values())
    h = input_hash(raw)
    manifest = {
        "pipeline_version": version, "changes": PIPELINE_CHANGES[version], "input_hash": h,
        "schema_versions_seen": dict(Counter(r.get("schema_version") for r in raw)),
        "records_in": len(raw), "quarantined": len(quarantine), "detections": len(dets),
        "tiers": dict(sorted(tiers.items())), "spatial_resolved": spatial, "golden_records": len(golden),
        "status_counts": dict(Counter(g["status"] for g in golden)),
        "llm": {"calls": llm_calls, "est_tokens": llm_tokens, "est_cost_usd": round(llm.cost(llm_tokens, 15 * llm_calls), 4),
                "all_detections_baseline_calls": len(dets),
                "all_detections_baseline_cost_usd": round(llm.cost(all_llm_tokens, 15 * len(dets)), 4),
                "assumed_usd_per_million_tokens": {"input": llm.PRICE_IN, "output": llm.PRICE_OUT}},
    }
    out = out_root / version / f"run-{h}"
    out.mkdir(parents=True, exist_ok=True)
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2))
    (out / "golden_records.json").write_text(json.dumps(golden, indent=1))
    (out / "matches.jsonl").write_text("\n".join(json.dumps(asdict(mt)) for mt in matches.values()))
    (out / "quarantine.jsonl").write_text("\n".join(json.dumps(q) for q in quarantine))
    return {"manifest": manifest, "dets": dets, "matches": matches, "slots": slots, "golden": golden,
            "quarantine": quarantine, "out_dir": out}
