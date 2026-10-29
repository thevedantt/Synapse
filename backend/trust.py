"""Trust layer: every number in an answer must come from the data the tools returned.

check(reply, sources) pulls the numbers out of the model's reply (₹ crore/lakh, %, commas)
and matches each against numbers in the tool results, directly or as a simple derivation
(ratio, % change, difference, sum of two results). Anything else is flagged as unverified.

Run the self-check: uv run python trust.py
"""

import json
import re
from itertools import permutations

UNITS = {"crore": 1e7, "cr": 1e7, "lakh": 1e5, "lac": 1e5, "k": 1e3}
NUMBER = re.compile(
    r"(?P<rupee>₹|rs\.?\s*|inr\s*)?(?P<num>\d+(?:,\d+)*(?:\.\d+)?)\s*"
    r"(?P<unit>crore|cr\b|lakh|lac\b|k\b|%|x\b|×)?", re.I)
MAX_SOURCES = 400  # cap so the pairwise derivation check stays fast


def reply_numbers(text: str) -> list[dict]:
    """Numbers worth checking in the reply, with value and rounding tolerance."""
    text = re.sub(r"\b\d{4}-\d{2}-\d{2}\b", " ", text)  # ISO dates
    found = []
    for m in NUMBER.finditer(text):
        raw, unit = m["num"], (m["unit"] or "").lower()
        digits = raw.replace(",", "")
        decimals = len(digits.split(".")[1]) if "." in digits else 0
        value = float(digits)
        # Skip things that aren't data: years, list numbers, "top 5", "1 in 3".
        if not m["rupee"] and not unit and "," not in raw:
            if 1900 <= value <= 2100 and decimals == 0 or value <= 12 and decimals == 0:
                continue
        scale = UNITS.get(unit, 1)
        found.append({"text": m.group().strip(), "value": value * scale,
                      "tol": 0.5 * 10 ** -decimals * scale, "pct": unit == "%"})
    return found


def source_numbers(sources: list[str]) -> list[float]:
    """Every number in the tool results (JSON from the MCP servers)."""
    nums: list[float] = []

    def walk(x):
        if isinstance(x, bool):
            return
        if isinstance(x, (int, float)):
            nums.append(float(x))
        elif isinstance(x, str):
            try:
                nums.append(float(x))  # Decimals arrive as strings
            except ValueError:
                # "₹46.66 crore": models do maths on these rounded values, so they count too
                nums.extend(n["value"] for n in reply_numbers(x))
        elif isinstance(x, dict):
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)

    for s in sources:
        try:
            walk(json.loads(s))
        except ValueError:  # plain text, e.g. the user's question ("last 30 days")
            nums += [n["value"] for n in reply_numbers(s)]
    return list(dict.fromkeys(nums))[:MAX_SOURCES]


def close(v: float, s: float, tol: float) -> bool:
    """Direct match: the reply's rounding, or 0.5% (e.g. ₹124.28 crore vs 1,24,28,40,457)."""
    return abs(v - s) <= max(tol, 0.005 * abs(s))


def verified(n: dict, src: list[float]) -> bool:
    v, tol = n["value"], n["tol"]
    # abs(s): "down 19.3%" for a change of -19.3 (the regex never sees a minus sign).
    if any(close(v, abs(s), tol) or n["pct"] and close(v, abs(s) * 100, tol) for s in src):
        return True
    # Simple derivations of two results. Only ~the reply's own rounding is allowed here:
    # with hundreds of pairs, a looser match would "verify" almost anything.
    for a, b in permutations(src, 2):
        derived = [a - b, a + b] + ([a / b, a / b * 100, (a - b) / b * 100] if b else [])
        if any(abs(v - d) <= 2 * tol for d in derived):  # 2*tol: models truncate (19.27 -> 19.2)
            return True
    return False


def check(reply: str, sources: list[str]) -> dict:
    """{"checked": n, "verified": n, "unverified": [text, ...], "truncated": bool}"""
    nums, src = reply_numbers(reply), source_numbers(sources)
    bad = [n["text"] for n in nums if not verified(n, src)]
    truncated = any('"truncated": true' in s for s in sources)
    return {"checked": len(nums), "verified": len(nums) - len(bad), "unverified": bad,
            "truncated": truncated}


if __name__ == "__main__":
    src = [json.dumps({"rows": [
        {"payment_method": "COD", "rto": 6369, "shipped": 23462, "rto_rate_pct": "27.15"},
        {"payment_method": "Prepaid", "rto": 1137, "shipped": 27358, "rto_rate_pct": "4.16"},
        {"revenue": "1242840457.00", "customers": 3838},
    ], "truncated": False})]
    ok = check("COD RTO is 27.15% (6,369 of 23,462) vs 4.16% prepaid, about 6.5x higher. "
               "Revenue ₹124.28 crore from 3,838 customers in September 2026, ₹3.24 lakh each. "
               "Top 5 couriers. 50,820 shipped in total.", src)
    assert ok["unverified"] == [] and ok["checked"] == 9, ok
    bad = check("Revenue was ₹12.43 crore and RTO 31.2%.", src)
    assert bad["unverified"] == ["₹12.43 crore", "31.2%"], bad
    # Maths on rounded display values (46.66 - 37.67 = 8.99) is fine.
    fmt = [json.dumps({"rows": [{"revenue": "466620123.00", "revenue_fmt": "₹46.66 crore"},
                                {"revenue": "376732456.00", "revenue_fmt": "₹37.67 crore"}]})]
    assert check("Down ₹8.99 crore", fmt)["unverified"] == [], check("Down ₹8.99 crore", fmt)
    print("trust self-check ok:", ok, bad)
