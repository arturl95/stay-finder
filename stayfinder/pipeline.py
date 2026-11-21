"""run = geocode -> search -> fetch listing pages -> build page. build = page from cache only."""
import datetime as dt
import hashlib
import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import airbnb, areas as areas_mod, config, page, photos as photos_mod
from .rules import apply_rules, compile_rules

AIRBNB_GAP_S = 0.5     # at least this long between two Airbnb requests, across all threads
NOMINATIM_GAP_S = 1.1  # Nominatim allows 1 request per second
THREADS = 4


class Throttle:
    """Lets requests start no closer together than `gap` seconds, shared by all threads."""

    def __init__(self, gap):
        self.gap, self.lock, self.last = gap, threading.Lock(), 0.0

    def wait(self):
        with self.lock:
            delay = self.last + self.gap - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            self.last = time.monotonic()


class Cache:
    """One JSON file per URL under .cache/pages. With refresh=True every URL is fetched again once."""

    def __init__(self, root, refresh=False):
        self.root = Path(root)
        (self.root / "pages").mkdir(parents=True, exist_ok=True)
        (self.root / "runs").mkdir(parents=True, exist_ok=True)
        self.refresh = refresh
        self.fresh = set()
        self.lock = threading.Lock()

    def path(self, url):
        return self.root / "pages" / (hashlib.sha256(url.encode()).hexdigest()[:24] + ".json")

    def get(self, url, fetch):
        p = self.path(url)
        with self.lock:
            use_cached = p.exists() and (not self.refresh or url in self.fresh)
        if use_cached:
            return json.loads(p.read_text(encoding="utf-8"))["data"]
        data = fetch(url)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps({"url": url, "fetched": dt.datetime.now().isoformat(timespec="seconds"),
                                   "data": data}), encoding="utf-8")
        tmp.replace(p)
        with self.lock:
            self.fresh.add(url)
        return data

    def read(self, url):
        p = self.path(url)
        if not p.exists():
            raise FileNotFoundError(f"not in cache: {url}. Run `python -m stayfinder run` first.")
        return json.loads(p.read_text(encoding="utf-8"))["data"]

    def run_file(self, cfg_path):
        return self.root / "runs" / (Path(cfg_path).stem + ".json")


def _paths(cfg_path, cfg):
    base = Path(cfg_path).resolve().parent
    out = Path(cfg["output"])
    return base / ".cache", (out if out.is_absolute() else base / out)


def geocode_areas(cfg, cache, log):
    gate = Throttle(NOMINATIM_GAP_S)

    def fetch(url):
        gate.wait()
        log(f"  OpenStreetMap: {url}")
        return areas_mod.fetch_geocode_url(url)

    out = {}
    for place in cfg["areas"]:
        res = cache.get(areas_mod.geocode_url(place), fetch)
        display, rings = areas_mod.parse_geocode(res, place)
        out[config.area_label(place)] = rings
        log(f"  {place!r} -> {display}")
    return out


def run(cfg_path, refresh=False, log=print):
    cfg = config.load(cfg_path)
    cache_dir, _ = _paths(cfg_path, cfg)
    cache = Cache(cache_dir, refresh)
    nights = config.nights(cfg)
    gate = Throttle(AIRBNB_GAP_S)

    def fetch_airbnb(url):
        gate.wait()
        return airbnb.fetch_state(url)

    def fetch(url):
        return cache.get(url, fetch_airbnb)

    if "areas" in cfg:
        log("Looking up area outlines on OpenStreetMap…")
        area_rings = geocode_areas(cfg, cache, log)
        bbox = areas_mod.pad_bbox(areas_mod.bbox_of(area_rings), cfg["border_tolerance_km"])
    else:
        area_rings, bbox = {}, cfg["bbox"]

    log(f"Searching Airbnb for {cfg['checkin']} – {cfg['checkout']} ({nights} nights), "
        f"at most {cfg['max_total']} {cfg['currency']} in total…")
    found, shortfall = airbnb.search(cfg, nights, bbox, fetch, log)

    candidates, over_budget, outside = [], 0, 0
    for it in found.values():
        if it["total"] > cfg["max_total"]:
            over_budget += 1
            continue
        if area_rings:
            area = areas_mod.area_of(it["lat"], it["lng"], area_rings, cfg["border_tolerance_km"])
            if area is None:
                outside += 1
                continue
        else:
            s, w, n, e = bbox
            if not (s <= it["lat"] <= n and w <= it["lng"] <= e):
                outside += 1
                continue
            area = None
        candidates.append({**it, "area": area})
    if shortfall:
        log(f"Note: Airbnb counted up to {shortfall} more listings than its pages returned.")
    log(f"{len(found)} search results: {over_budget} over budget, {outside} outside the areas, "
        f"{len(candidates)} to open.")
    if len(candidates) > cfg["max_listings"]:
        raise RuntimeError(f"{len(candidates)} listings match, more than max_listings={cfg['max_listings']}. "
                           f"Lower max_total, add amenity_ids, pick smaller areas, or raise max_listings.")

    done = [0]
    done_lock = threading.Lock()

    def open_listing(c):
        fetch(airbnb.listing_url(cfg, c["id"]))
        with done_lock:
            done[0] += 1
            if done[0] % 10 == 0 or done[0] == len(candidates):
                log(f"  listing pages: {done[0]}/{len(candidates)}")

    log(f"Opening {len(candidates)} listing pages ({THREADS} at a time)…")
    with ThreadPoolExecutor(THREADS) as pool:
        list(pool.map(open_listing, candidates))

    run_data = {
        "search": config.search_fingerprint(cfg),
        "searched_at": dt.datetime.now().isoformat(timespec="minutes"),
        "areas": area_rings,
        "bbox": bbox,
        "counts": {"search_results": len(found), "over_budget": over_budget, "outside_areas": outside,
                   "not_returned": shortfall},
        "candidates": candidates,
    }
    cache.run_file(cfg_path).write_text(json.dumps(run_data), encoding="utf-8")
    return build(cfg_path, log=log)


def studio_hit(rx, name, overview, kind):
    if rx is None:
        return None
    for text in [name, kind, *overview]:
        m = rx.search(text or "")
        if m:
            return text
    return None


def amenity_hits(filters, amenities):
    low = [a.lower() for a in amenities]
    return {f: any(f.lower() in a for a in low) for f in filters}


def listing_record(cfg, cand, info, rules, studio_rx):
    texts = [cand["name"], info["description"], *info["house_rules"], info["original_text"]]
    hits = apply_rules(rules, texts)
    return {
        "id": cand["id"], "name": cand["name"], "url": airbnb.listing_url(cfg, cand["id"]),
        "area": cand["area"], "lat": cand["lat"], "lng": cand["lng"],
        "total": cand["total"], "before": cand["total_before_discount"],
        "kind": info["kind"], "overview": info["overview"],
        "rating": info["rating"], "reviews": info["reviews"],
        "gf": info["guest_favourite"], "sh": info["superhost"], "cats": info["category_ratings"],
        "photos": info["photos"], "rooms": info["rooms"],
        "desc": info["description"], "rules": info["house_rules"],
        "amenities": info["amenities"], "am": amenity_hits(cfg["amenity_filters"], info["amenities"]),
        "studio": studio_hit(studio_rx, cand["name"], info["overview"], info["kind"]),
        "exclude": hits["exclude"], "flag": hits["flag"], "highlight": hits["highlight"],
        "photo": None,  # {"verdict", "note"} from the judgements file, when one is given
    }


def collect(cfg_path, log=print):
    """Read the cached run and turn every cached listing into a page record.
    Returns (cfg, out_path, cache_dir, run_data, records, low_rated)."""
    cfg = config.load(cfg_path, allow_past=True)
    cache_dir, out_path = _paths(cfg_path, cfg)
    cache = Cache(cache_dir)
    rf = cache.run_file(cfg_path)
    if not rf.exists():
        raise FileNotFoundError(f"no cached search for {cfg_path} ({rf}). Run `python -m stayfinder run {cfg_path}` first.")
    run_data = json.loads(rf.read_text(encoding="utf-8"))
    if run_data["search"] != config.search_fingerprint(cfg):
        changed = [k for k in config.SEARCH_KEYS if run_data["search"].get(k) != cfg.get(k)]
        raise RuntimeError(f"search settings changed since the last run ({', '.join(changed)}). "
                           f"Run `python -m stayfinder run {cfg_path}` to search again.")
    rules = compile_rules(cfg["rules"])
    studio_rx = re.compile(cfg["studio_pattern"], re.I) if cfg["studio_pattern"] else None
    records, low_rated = [], 0
    for c in run_data["candidates"]:
        info = airbnb.parse_listing(cache.read(airbnb.listing_url(cfg, c["id"])), c["id"])
        if info["rating"] is not None and info["rating"] < cfg["min_rating"]:
            low_rated += 1
            continue
        records.append(listing_record(cfg, c, info, rules, studio_rx))
    return cfg, out_path, cache_dir, run_data, records, low_rated


def build(cfg_path, log=print):
    cfg, out_path, _, run_data, records, low_rated = collect(cfg_path, log)
    for f in cfg["amenity_filters"]:
        if records and not any(r["am"][f] for r in records):
            log(f"Note: no listing has an amenity containing {f!r}; check the spelling against Airbnb's amenity names.")
    judged = cfg["judgements"] is not None
    if judged:
        base = Path(cfg_path).resolve().parent
        verdicts = photos_mod.load_judgements(cfg["judgements"], base, {r["id"] for r in records})
        for r in records:
            r["photo"] = verdicts.get(r["id"])
        log(f"Photo check: {len(verdicts)} of {len(records)} listings judged.")
    stats = {**run_data["counts"], "below_min_rating": low_rated, "listed": len(records),
             "excluded": sum(1 for r in records if r["exclude"])}
    html = page.build_page(cfg, records, run_data["areas"], run_data["bbox"], stats, run_data["searched_at"],
                           storage_key=out_path.name, judged=judged)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")
    log(f"{len(records)} listings on the page ({stats['excluded']} excluded by rules, "
        f"{low_rated} below min_rating left out).")
    return out_path


def photos(cfg_path, max_n=12, log=print, download=None):
    """Download the first max_n photo-tour photos of every listing on the page (not excluded by rules)
    into <cache>/photos/<id>/ and write <cache>/photos/index.json. Returns photos.download_all's summary."""
    cfg, _, cache_dir, _, records, _ = collect(cfg_path, log)
    shown = [r for r in records if not r["exclude"]]
    return photos_mod.download_all(cfg, shown, Path(cache_dir) / "photos", max_n, log,
                                   download or photos_mod.fetch_image)
