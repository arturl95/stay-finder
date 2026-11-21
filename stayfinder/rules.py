"""Keyword rules over a listing's own text (title, description, house rules, in English and the
host's original language).

Each rule: {"label": str, "pattern": regex, "unless": optional regex, "action": "exclude"|"flag"|"highlight"}
- exclude:   the listing is hidden (it can still be shown with the "excluded" view, with the quote)
- flag:      the listing stays, with an amber note quoting the host
- highlight: the listing gets a green chip and a "must have" filter
"unless" is checked against the same sentence: a match there cancels the hit.
Matching is case-insensitive. This is a keyword search, so it can miss rules or misread them.
"""
import re

ACTIONS = ("exclude", "flag", "highlight")


KEYS = {"label", "pattern", "unless", "action"}


def _regex(rule, key):
    try:
        return re.compile(rule[key], re.I)
    except re.error as e:
        raise ValueError(f"rule {rule.get('label')!r}: {key} is not a valid regular expression: {e}") from e


def compile_rules(rules):
    if not isinstance(rules, list):
        raise ValueError("rules must be a list")
    out, labels = [], set()
    for r in rules:
        if not isinstance(r, dict):
            raise ValueError(f"rule {r!r}: must be an object")
        unknown = set(r) - KEYS
        if unknown:
            raise ValueError(f"rule {r.get('label')!r}: unknown keys {sorted(unknown)}; allowed: {sorted(KEYS)}")
        if r.get("action") not in ACTIONS:
            raise ValueError(f"rule {r.get('label')!r}: action must be one of {ACTIONS}")
        if not isinstance(r.get("label"), str) or not r["label"].strip() \
                or not isinstance(r.get("pattern"), str) or not r["pattern"]:
            raise ValueError(f"rule {r!r}: needs a non-empty label and pattern")
        if r["label"] in labels:
            raise ValueError(f"rule label {r['label']!r} is used twice; labels must be unique")
        labels.add(r["label"])
        if "unless" in r and (not isinstance(r["unless"], str) or not r["unless"]):
            raise ValueError(f"rule {r['label']!r}: unless must be a non-empty string when given")
        out.append({**r, "_p": _regex(r, "pattern"), "_u": _regex(r, "unless") if r.get("unless") else None})
    return out


def sentences(texts):
    for t in texts:
        for s in re.split(r"(?<=[.!?])\s+|\n+", t or ""):
            s = s.strip()
            if s:
                yield s


def apply_rules(rules, texts):
    """Return {"exclude": [...], "flag": [...], "highlight": [...]}, each a list of {label, quote}."""
    hits = {a: [] for a in ACTIONS}
    sents = list(dict.fromkeys(sentences(texts)))
    for r in rules:
        quotes = [s for s in sents if r["_p"].search(s) and not (r["_u"] and r["_u"].search(s))]
        if quotes:
            hits[r["action"]].append({"label": r["label"], "quote": " … ".join(q[:220] for q in quotes[:2])})
    return hits
