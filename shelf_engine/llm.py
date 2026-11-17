"""
The last tier: an LLM picks between the few remaining catalog candidates for a detection that rules and
spatial consensus couldn't settle. It is only ever shown the shortlist, and its answer is accepted only
if it is one of those candidates (or "none").

With ANTHROPIC_API_KEY set, it calls the Messages API. Without a key, these detections go to a human
review queue instead. Either way the cost is estimated so the tiered design can be compared with
sending every detection to an LLM.
"""
import json
import os
import urllib.request

from .match import Match

PRICE_IN, PRICE_OUT = 3.00, 15.00          # assumed USD per million tokens; set to your model's rates
INSTRUCTIONS = ("You match a retail shelf label read by a camera to a product catalog. Reply with JSON only: "
                '{"gtin": "<one of the candidate GTINs>"} or {"gtin": "none"} if none fit. Pay attention to variant '
                "(e.g. Zero Sugar vs Original) and pack size.")


def tokens(text: str) -> int:
    return max(1, len(text) // 4)


def prompt_for(text: str, cands: list[tuple[str, str]]) -> str:
    lines = "\n".join(f"- {g}: {n}" for g, n in cands)
    return f"{INSTRUCTIONS}\n\nLabel text: {text!r}\nCandidates:\n{lines}"


def cost(prompt_tokens: int, output_tokens: int = 15) -> float:
    return prompt_tokens * PRICE_IN / 1e6 + output_tokens * PRICE_OUT / 1e6


def ask(prompt: str) -> str | None:
    key = os.getenv("ANTHROPIC_API_KEY")
    if not key:
        return None
    req = urllib.request.Request("https://api.anthropic.com/v1/messages", method="POST",
                                 headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                                 data=json.dumps({"model": os.getenv("SHELF_MODEL", "claude-haiku-5-5"), "max_tokens": 50,
                                                  "messages": [{"role": "user", "content": prompt}]}).encode())
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())["content"][0]["text"]


def resolve(m: Match, text: str, names: dict[str, str]) -> tuple[Match, int]:
    cands = [(g, names[g]) for g, _ in m.candidates]
    p = prompt_for(text, cands)
    reply = ask(p)
    if reply is None:
        m.tier, m.note = "review", "ambiguous after rules and spatial consensus; queued for review (no LLM key set)"
        return m, tokens(p)
    try:
        g = json.loads(reply[reply.index("{"): reply.rindex("}") + 1])["gtin"]
    except (ValueError, KeyError):
        g = None
    if g in {c for c, _ in cands}:
        m.tier, m.gtin, m.note = "llm", g, "chosen by LLM from the shortlist"
    else:
        m.tier, m.note = "review", f"LLM answer {g!r} not in the shortlist; queued for review"
    return m, tokens(p)
