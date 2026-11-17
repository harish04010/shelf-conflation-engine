import json
from pathlib import Path

import pytest

from shelf_engine import gtin as G
from shelf_engine.catalog import build_catalog
from shelf_engine.contracts import to_canonical
from shelf_engine.evaluate import evaluate
from shelf_engine.match import Matcher
from shelf_engine.normalize import clean_text, is_price_tag, parse_sizes, size_matches
from shelf_engine.pipeline import run
from shelf_engine.simulate import AISLES, simulate

LAYOUT = {f"A{a}": c for a, c in AISLES.items()}


@pytest.fixture(scope="module")
def world():
    cat = build_catalog()
    raw, truth, slot_truth = simulate(cat)
    return cat, raw, truth, slot_truth


def test_check_digit_and_normalization():
    assert G.is_valid("036000291452")                        # a well-known valid UPC-A
    assert G.normalize("0036000291452") == ("036000291452", "ok")   # EAN-13 with a leading zero
    assert G.normalize("036000291453")[1] == "bad_check_digit"
    assert G.normalize(None)[1] == "missing"


def test_every_catalog_gtin_is_valid(world):
    cat = world[0]
    assert all(G.is_valid(p.gtin) for p in cat) and len({p.gtin for p in cat}) == len(cat)


def test_size_parsing_handles_the_many_ways_sizes_are_written():
    assert size_matches(parse_sizes("Cola 355 mL"), "ml", 354.9)
    assert size_matches(parse_sizes("COLA 12oz"), "ml", 354.9)          # bare oz may be fluid ounces...
    assert size_matches(parse_sizes("Chips (227g)"), "g", 226.8)
    assert size_matches(parse_sizes("Kettle 8 0Z"), "g", 226.8)          # ...or weight, and OCR wrote 0Z
    assert size_matches(parse_sizes("Cola 2L"), "ml", 2000)
    assert not size_matches(parse_sizes("Cola 20 FL OZ"), "ml", 354.9)


def test_cleaning_undoes_ocr_and_abbreviations():
    assert clean_text("5UMMIT Fizz Cola ZRO 12oz") == "summit fizz cola zero sugar"
    assert clean_text("Kettle Ridge Kettle Chips S&V") == "kettle ridge kettle chips salt vinegar"


def test_price_tags_are_filtered():
    for t in ["SALE 2/$5", "$4.99", "UNIT PRICE $0.21 PER OZ", "25% OFF"]:
        assert is_price_tag(t)
    assert not is_price_tag("Summit Fizz Cola 12 FL OZ")


def test_both_schema_versions_reach_the_same_record_and_bad_ones_are_quarantined():
    v1 = {"schema_version": "1", "det_id": "a", "pass_id": 1, "fixture_id": "A1-L-B1", "x_m": 0.5, "z_m": 0.15, "text": "Cola", "upc": None, "conf": 0.9}
    v2 = {"schema_version": "2", "det_id": "a", "pass_id": 1, "fixture_id": "A1-L-B1", "pos_cm": {"x": 50, "z": 15},
          "ocr_text": "Cola", "barcode": {"value": None}, "conf": 0.9}
    a, _ = to_canonical(v1)
    b, _ = to_canonical(v2)
    assert (a.x, a.z, a.text) == (b.x, b.z, b.text)
    bad = dict(v1)
    bad.pop("x_m")
    det, err = to_canonical(bad)
    assert det is None and "x_m" in err
    assert to_canonical({**v1, "schema_version": "9"})[1].startswith("unsupported")


def test_variant_guard_keeps_zero_sugar_off_the_original(world):
    from shelf_engine.contracts import Detection
    cat = world[0]
    m = Matcher(cat, LAYOUT)
    d = Detection("t", 1, "A1-L-B1", 0.5, 0.15, "Summit Fizz Cola ZERO SUGAR 12 FL OZ", None, 0.9, "1")
    got = m.match(d)
    p = next(p for p in cat if p.gtin == got.gtin)
    assert got.tier == "rules" and p.variant == "Zero Sugar" and p.size_label == "12 fl oz"


def test_end_to_end_v2_is_precise_and_beats_v1(world, tmp_path):
    cat, raw, truth, slot_truth = world
    gtins = {p.gtin for p in cat}
    e1 = evaluate(run(raw, cat, LAYOUT, "v1", tmp_path), truth, slot_truth, gtins)
    r2 = run(raw, cat, LAYOUT, "v2", tmp_path)
    e2 = evaluate(r2, truth, slot_truth, gtins)
    assert e2["auto_precision"] >= 0.99
    assert e2["catalog_coverage"] > e1["catalog_coverage"] + 0.2
    assert e2["slots"]["spurious_records"] == 0 and e2["slots"]["split_slots"] == 0 and e2["slots"]["mixed_clusters"] == 0
    assert r2["manifest"]["llm"]["calls"] < 0.1 * r2["manifest"]["detections"]


def test_runs_are_reproducible_and_versioned(world, tmp_path):
    cat, raw, _, _ = world
    a = run(raw, cat, LAYOUT, "v2", tmp_path / "a")
    b = run(raw, cat, LAYOUT, "v2", tmp_path / "b")
    assert a["out_dir"].name == b["out_dir"].name
    for f in ("manifest.json", "golden_records.json", "matches.jsonl"):
        assert (a["out_dir"] / f).read_text() == (b["out_dir"] / f).read_text()
    assert json.loads((a["out_dir"] / "manifest.json").read_text())["schema_versions_seen"].keys() == {"1", "2"}
