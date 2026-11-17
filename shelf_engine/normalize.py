"""
Rules-based normalization: the cheap, deterministic layer. Regex is the right tool here because
the patterns are bounded: there are only so many ways to write a pack size or abbreviate "Zero Sugar".
"""
import re

FL_OZ_ML, OZ_G = 29.5735, 28.3495

PRICE_TAG = re.compile(r"\$\s?\d|\bsale\b|\d+\s?/\s?\$|\bunit price\b|\bper\s?(oz|lb|ct)\b|\bsave\b|%\s?off|\bea\b", re.I)
SIZE = re.compile(r"(\d+(?:\.\d+)?)\s*(fl\.?\s*oz|oz|ml|liters?|litres?|l|g)\b", re.I)
ALIASES = [
    (r"\bzro\b|\bzero sug\b|\b0 sugar\b", "zero sugar"),
    (r"\bdt\b", "diet"),
    (r"\bs\s?&\s?v\b", "salt vinegar"),
    (r"\bbarbecue\b", "bbq"),
    (r"\bjalap\.?(?=\s|$)", "jalapeno"),
    (r"\blo sodium\b", "low sodium"),
    (r"\bcrm\b", "cream"),
]
OCR_FIX = str.maketrans({"0": "o", "1": "l", "5": "s", "8": "b"})


def is_price_tag(text: str) -> bool:
    return bool(PRICE_TAG.search(text))


def parse_sizes(text: str) -> list[tuple[str, float]]:
    """All plausible (dimension, amount) readings. Bare "oz" is ambiguous (fluid or weight), so both are kept
    and the candidate product decides which one applies."""
    text = re.sub(r"(?<=\d)\s*[0O]Z\b", "oz", text)                       # OCR: "12 0Z" -> "12oz"
    out = []
    for num, unit in SIZE.findall(text):
        v, u = float(num), re.sub(r"[\s.]", "", unit.lower())
        if u == "floz":
            out.append(("ml", v * FL_OZ_ML))
        elif u == "oz":
            out += [("ml", v * FL_OZ_ML), ("g", v * OZ_G)]
        elif u == "ml":
            out.append(("ml", v))
        elif u.startswith("l"):
            out.append(("ml", v * 1000))
        elif u == "g":
            out.append(("g", v))
    return out


def size_matches(sizes: list[tuple[str, float]], dim: str, amount: float, tol: float = 0.03) -> bool:
    return any(d == dim and abs(a - amount) / amount <= tol for d, a in sizes)


def _fix_token(tok: str) -> str:
    letters = sum(c.isalpha() for c in tok)
    digits = sum(c.isdigit() for c in tok)
    return tok.translate(OCR_FIX) if letters and digits and letters >= digits else tok


def clean_text(text: str) -> str:
    """Lowercase, undo OCR digit/letter swaps inside words, expand abbreviations, drop sizes and punctuation."""
    t = SIZE.sub(" ", re.sub(r"(?<=\d)\s*[0O]Z\b", "oz", text))
    t = " ".join(_fix_token(tok) for tok in t.lower().split())
    for pat, rep in ALIASES:
        t = re.sub(pat, rep, t)
    t = re.sub(r"[^a-z0-9 ]+", " ", t)
    t = re.sub(r"\b\d+(\.\d+)?\b", " ", t)                                 # stray numbers left after size removal
    return re.sub(r"\s+", " ", t).strip()
