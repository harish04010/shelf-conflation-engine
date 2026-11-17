"""GTIN / UPC check digits. A barcode read with a wrong digit almost always fails its check digit."""
import re


def check_digit(body: str) -> int:
    """Check digit for a GTIN body (all digits except the last). Weights 3,1,3,1... from the right."""
    total = sum(int(d) * (3 if i % 2 == 0 else 1) for i, d in enumerate(reversed(body)))
    return (10 - total % 10) % 10


def is_valid(code: str) -> bool:
    return code.isdigit() and len(code) in (8, 12, 13, 14) and check_digit(code[:-1]) == int(code[-1])


def normalize(raw: str | None) -> tuple[str | None, str]:
    """Returns (GTIN-12, status). Status is ok, missing, bad_length or bad_check_digit.
    EAN-13 / GTIN-14 codes with leading zeros are reduced to the 12-digit UPC-A form."""
    if not raw:
        return None, "missing"
    digits = re.sub(r"\D", "", raw)
    while len(digits) > 12 and digits.startswith("0"):
        digits = digits[1:]
    if len(digits) != 12:
        return None, "bad_length"
    if not is_valid(digits):
        return None, "bad_check_digit"
    return digits, "ok"


def hamming(a: str, b: str) -> int:
    return sum(x != y for x, y in zip(a, b)) + abs(len(a) - len(b))
