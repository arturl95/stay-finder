"""Load and check a search config. Every problem is reported at once, with the key it concerns."""
import datetime as dt
import json
import re
from pathlib import Path

from .rules import compile_rules

REQUIRED = ("title", "checkin", "checkout", "adults", "currency", "max_total", "output")
OPTIONAL = {  # key: default when the key is left out
    "entire_place": False,
    "amenity_ids": [],
    "min_rating": 0,
    "border_tolerance_km": 0.2,
    "rules": [],
    "studio_pattern": None,
    "amenity_filters": [],
    "max_listings": 300,
    "judgements": None,
}
WHERE = ("areas", "bbox")  # exactly one of these
SEARCH_KEYS = ("areas", "bbox", "checkin", "checkout", "adults", "currency", "max_total",
               "entire_place", "amenity_ids", "border_tolerance_km")


class ConfigError(ValueError):
    pass


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _date(v, key, errs):
    if not isinstance(v, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
        errs.append(f"{key}: must be a date written YYYY-MM-DD, got {v!r}")
        return None
    try:
        return dt.date.fromisoformat(v)
    except ValueError as e:
        errs.append(f"{key}: {e}")
        return None


def validate(raw, today=None, allow_past=False):
    """Return a checked config dict (with defaults for optional keys). Raise ConfigError listing
    every problem found."""
    today = today or dt.date.today()
    errs = []
    if not isinstance(raw, dict):
        raise ConfigError("config must be a JSON object")
    known = set(REQUIRED) | set(OPTIONAL) | set(WHERE)
    for k in raw:
        if k not in known and not k.startswith("_"):
            errs.append(f"{k}: unknown key (keys starting with _ are ignored, use them for comments)")
    for k in REQUIRED:
        if k not in raw:
            errs.append(f"{k}: missing")
    cfg = {**OPTIONAL, **{k: v for k, v in raw.items() if not k.startswith("_")}}

    has = [k for k in WHERE if k in raw]
    if len(has) != 1:
        errs.append("give exactly one of 'areas' (place names) or 'bbox' ([south, west, north, east])")
    if "areas" in raw:
        a = raw["areas"]
        if not isinstance(a, list) or not a or not all(isinstance(x, str) and x.strip() for x in a):
            errs.append("areas: must be a non-empty list of place names, e.g. \"Alfama, Lisbon, Portugal\"")
        else:
            labels = [area_label(x) for x in a]
            dup = {l for l in labels if labels.count(l) > 1}
            if dup:
                errs.append(f"areas: two areas would get the same label {sorted(dup)}; the label is the text before the first comma")
    if "bbox" in raw:
        b = raw["bbox"]
        if not (isinstance(b, list) and len(b) == 4 and all(_num(x) for x in b)):
            errs.append("bbox: must be [south, west, north, east] in degrees")
        elif not (-90 <= b[0] < b[2] <= 90 and -180 <= b[1] < b[3] <= 180):
            errs.append("bbox: need -90 <= south < north <= 90 and -180 <= west < east <= 180")

    if "title" in raw and (not isinstance(raw["title"], str) or not raw["title"].strip()):
        errs.append("title: must be a non-empty string")
    ci = _date(raw.get("checkin"), "checkin", errs) if "checkin" in raw else None
    co = _date(raw.get("checkout"), "checkout", errs) if "checkout" in raw else None
    if ci and co:
        if co <= ci:
            errs.append("checkout: must be after checkin")
        if ci < today and not allow_past:
            errs.append(f"checkin: {ci} is in the past")
    if "adults" in raw and not (isinstance(raw["adults"], int) and not isinstance(raw["adults"], bool)
                                and 1 <= raw["adults"] <= 16):
        errs.append("adults: must be a whole number from 1 to 16")
    if "currency" in raw and not (isinstance(raw["currency"], str) and re.fullmatch(r"[A-Z]{3}", raw["currency"])):
        errs.append("currency: must be a 3-letter code such as EUR, USD, GBP")
    if "max_total" in raw and not (_num(raw["max_total"]) and raw["max_total"] > 0):
        errs.append("max_total: must be a positive number (the most you would pay for the whole stay)")
    if "output" in raw and not (isinstance(raw["output"], str) and raw["output"].endswith(".html")):
        errs.append("output: must be a file path ending in .html")

    if not isinstance(cfg["entire_place"], bool):
        errs.append("entire_place: must be true or false")
    ai = cfg["amenity_ids"]
    if not (isinstance(ai, list) and all(isinstance(x, int) and not isinstance(x, bool) and x > 0 for x in ai)):
        errs.append("amenity_ids: must be a list of Airbnb amenity numbers, e.g. [4, 8] for wifi and kitchen")
    if not (_num(cfg["min_rating"]) and 0 <= cfg["min_rating"] <= 5):
        errs.append("min_rating: must be a number from 0 to 5")
    if not (_num(cfg["border_tolerance_km"]) and 0 <= cfg["border_tolerance_km"] <= 5):
        errs.append("border_tolerance_km: must be a number from 0 to 5")
    if not (isinstance(cfg["max_listings"], int) and not isinstance(cfg["max_listings"], bool)
            and cfg["max_listings"] > 0):
        errs.append("max_listings: must be a positive whole number")
    sp = cfg["studio_pattern"]
    if sp is not None:
        if not isinstance(sp, str) or not sp:
            errs.append("studio_pattern: must be a regular expression string, or left out")
        else:
            try:
                re.compile(sp)
            except re.error as e:
                errs.append(f"studio_pattern: not a valid regular expression: {e}")
    af = cfg["amenity_filters"]
    if not (isinstance(af, list) and all(isinstance(x, str) and x.strip() for x in af)):
        errs.append("amenity_filters: must be a list of amenity names, e.g. [\"Washer\", \"Dedicated workspace\"]")
    elif len({x.lower() for x in af}) != len(af):
        errs.append("amenity_filters: an amenity is listed twice")
    j = cfg["judgements"]
    if j is not None and not (
            (isinstance(j, str) and j.endswith(".json"))
            or (isinstance(j, list) and j and all(isinstance(x, str) and x.endswith(".json") for x in j))):
        errs.append("judgements: must be the path of a .json file (relative to the config), "
                    "or a non-empty list of such paths")
    try:
        compile_rules(cfg["rules"])
    except ValueError as e:
        errs.append(f"rules: {e}")

    if errs:
        raise ConfigError("config has problems:\n  - " + "\n  - ".join(errs))
    return cfg


def area_label(place):
    """Short name shown on the page: the text before the first comma."""
    return place.split(",")[0].strip()


def nights(cfg):
    return (dt.date.fromisoformat(cfg["checkout"]) - dt.date.fromisoformat(cfg["checkin"])).days


def load(path, today=None, allow_past=False):
    path = Path(path)
    if not path.is_file():
        raise ConfigError(f"config file not found: {path}")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ConfigError(f"{path} is not valid JSON: {e}") from e
    return validate(raw, today, allow_past)


def search_fingerprint(cfg):
    """The settings that decide what Airbnb is asked; a cached run is only reused when they match."""
    return {k: cfg.get(k) for k in SEARCH_KEYS}
