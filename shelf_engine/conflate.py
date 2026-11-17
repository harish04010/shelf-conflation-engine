"""
Conflation: turning many sightings into one golden record per shelf slot.

1. Cluster every detection on a fixture, across all passes, by position (DBSCAN). Each cluster is a slot;
   duplicates within a pass and repeat sightings across passes fall into the same cluster.
2. Spatial consensus (v2): an ambiguous detection takes the product that other detections in the same slot
   confidently resolved to, if that product is one of its own candidates. Same pass first, then other passes.
   This is free, and it settles most of what would otherwise go to the LLM.
3. Golden record: the product and status of each slot as of the latest pass, with full lineage.
"""
from collections import Counter

import numpy as np
from sklearn.cluster import DBSCAN

from .contracts import Detection
from .match import CONFIDENT, Match

EPS_M = 0.10    # 8 cm split 8 slots in two (one sighting drifted); 10 cm splits none and merges none


def cluster(dets: list[Detection]) -> dict[str, str]:
    """det_id -> slot_id"""
    out, by_fx = {}, {}
    for d in dets:
        by_fx.setdefault(d.fixture_id, []).append(d)
    for fx, group in sorted(by_fx.items()):
        pts = np.array([[d.x, d.z] for d in group])
        labels = DBSCAN(eps=EPS_M, min_samples=1).fit(pts).labels_
        # name slots by position so ids are stable and readable: fixture / shelf row / position
        cents = {lab: pts[labels == lab].mean(axis=0) for lab in set(labels)}
        order = sorted(cents, key=lambda lab: (round(cents[lab][1] / 0.35), cents[lab][0]))
        names = {lab: f"{fx}/S{int(round((cents[lab][1] - 0.15) / 0.35)) + 1}-{i:02d}" for i, lab in enumerate(order)}
        for d, lab in zip(group, labels):
            out[d.det_id] = names[lab]
    return out


def spatial_consensus(dets: list[Detection], matches: dict[str, Match], slots: dict[str, str]) -> int:
    by_slot: dict[str, list[Detection]] = {}
    for d in dets:
        by_slot.setdefault(slots[d.det_id], []).append(d)
    resolved = 0
    for group in by_slot.values():
        for d in group:
            m = matches[d.det_id]
            if m.tier != "ambiguous":
                continue
            cand = {g for g, _ in m.candidates}
            for same_pass in (True, False):
                votes = Counter(matches[o.det_id].gtin for o in group
                                if o.det_id != d.det_id and matches[o.det_id].tier in CONFIDENT - {"spatial"}
                                and (o.pass_id == d.pass_id) == same_pass and matches[o.det_id].gtin in cand)
                if votes:
                    g, _ = votes.most_common(1)[0]
                    m.tier, m.gtin = "spatial", g
                    m.note = f"agrees with {'the same pass' if same_pass else 'other passes'} in slot {slots[d.det_id]}"
                    resolved += 1
                    break
    return resolved


def golden_records(dets: list[Detection], matches: dict[str, Match], slots: dict[str, str], names: dict[str, str]) -> list[dict]:
    latest = max(d.pass_id for d in dets)
    by_slot: dict[str, list[Detection]] = {}
    for d in dets:
        by_slot.setdefault(slots[d.det_id], []).append(d)
    out = []
    for slot_id, group in sorted(by_slot.items()):
        def majority(ds):
            c = Counter(matches[d.det_id].gtin for d in ds if matches[d.det_id].tier in CONFIDENT)
            return c.most_common(1)[0][0] if c else None
        now = [d for d in group if d.pass_id == latest]
        earlier = [d for d in group if d.pass_id < latest]
        before = majority(earlier)
        if not now:
            status, gtin = "gap", before
        else:
            gtin = majority(now)
            if gtin:
                status = "changed" if before and before != gtin else "in_stock"
            elif any(matches[d.det_id].tier == "no_match" for d in now):
                status = "new_item"
            else:
                status = "needs_review"
        tiers = Counter(matches[d.det_id].tier for d in group)
        conf_share = sum(v for k, v in tiers.items() if k in CONFIDENT) / len(group)
        xs, zs = [d.x for d in group], [d.z for d in group]
        out.append({"slot_id": slot_id, "fixture_id": group[0].fixture_id, "x": round(sum(xs) / len(xs), 3),
                    "z": round(sum(zs) / len(zs), 3), "status": status, "gtin": gtin, "product": names.get(gtin),
                    "previous_gtin": before if status == "changed" else None, "passes_seen": sorted({d.pass_id for d in group}),
                    "confidence": round(conf_share, 2), "detections": sorted(d.det_id for d in group),
                    "unresolved_text": [d.text for d in now if matches[d.det_id].tier in ("no_match", "review", "ambiguous")][:3]})
    return out
