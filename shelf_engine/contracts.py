"""
Data contracts. The edge firmware changed schema between passes (v1 -> v2: renamed fields,
centimeters instead of meters, barcode moved into an object). Every version is upgraded to one
canonical record, so the rest of the pipeline never sees the difference. Records that break the
contract go to a quarantine with the reason, instead of failing the run or silently disappearing.
"""
import math
from dataclasses import dataclass

SUPPORTED = {"1", "2"}


@dataclass(frozen=True)
class Detection:
    det_id: str
    pass_id: int
    fixture_id: str
    x: float          # meters from the left edge of the fixture
    z: float          # meters above the floor
    text: str
    upc: str | None
    conf: float
    schema_version: str


def _num(v) -> bool:
    return isinstance(v, (int, float)) and math.isfinite(v)


def to_canonical(rec: dict) -> tuple[Detection | None, str | None]:
    v = rec.get("schema_version")
    if v not in SUPPORTED:
        return None, f"unsupported schema_version {v!r}"
    try:
        if v == "1":
            x, z, text, upc = rec["x_m"], rec["z_m"], rec["text"], rec.get("upc")
        else:
            x, z = rec["pos_cm"]["x"] / 100, rec["pos_cm"]["z"] / 100
            text, upc = rec["ocr_text"], (rec.get("barcode") or {}).get("value")
        det = Detection(rec["det_id"], int(rec["pass_id"]), rec["fixture_id"], float(x), float(z), str(text or ""), upc,
                        float(rec.get("conf", 0)), v)
    except (KeyError, TypeError, ValueError) as e:
        return None, f"missing or invalid field: {e}"
    if not (_num(det.x) and _num(det.z)):
        return None, "position is not a number"
    if not det.text.strip() and not det.upc:
        return None, "no text and no barcode"
    return det, None
