"""Turn the listing records into one self-contained HTML page (template.html + embedded JSON)."""
import html
import json
import math
import re
from pathlib import Path

from . import config

TEMPLATE = Path(__file__).with_name("template.html")
KNOWN_AMENITY_IDS = {4: "wifi", 8: "kitchen"}  # checked against Airbnb's own result counts
MAP_W, MAP_H_MAX, MAP_H_MIN, MAP_PAD = 440, 680, 260, 22


def embed_json(obj):
    """JSON that is safe inside <script>: no '<' at all, so '</script>' and '<!--' cannot occur."""
    return (json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
            .replace("<", "\\u003c").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029"))


def centroid(ring):
    """Area-weighted centroid of a [[lat, lng], ...] ring (flat approximation, fine at city scale)."""
    a = cx = cy = 0.0
    for i in range(len(ring)):
        y1, x1 = ring[i - 1]
        y2, x2 = ring[i]
        c = x1 * y2 - x2 * y1
        a += c
        cx += (x1 + x2) * c
        cy += (y1 + y2) * c
    if abs(a) < 1e-15:  # degenerate ring: use the mean of its points
        return [sum(p[0] for p in ring) / len(ring), sum(p[1] for p in ring) / len(ring)]
    return [cy / (3 * a), cx / (3 * a)]


def ring_area(ring):
    return abs(sum(ring[i - 1][1] * ring[i][0] - ring[i][1] * ring[i - 1][0] for i in range(len(ring)))) / 2


def area_labels(areas):
    """One label per area, at the centroid of its largest outline."""
    out = []
    for name, rings in areas.items():
        la, lo = centroid(max(rings, key=ring_area))
        out.append([la, lo, name])
    return out


def fit_map(points):
    """Projection that fits all points: centre, km per degree, pixels per km, grid step in km."""
    if not points:
        raise ValueError("nothing to draw on the map")
    lats = [p[0] for p in points]
    lngs = [p[1] for p in points]
    c = [(min(lats) + max(lats)) / 2, (min(lngs) + max(lngs)) / 2]
    kx, ky = 111.32 * math.cos(math.radians(c[0])), 110.57
    w_km = max((max(lngs) - min(lngs)) * kx, 0.2)
    h_km = max((max(lats) - min(lats)) * ky, 0.2)
    s = (MAP_W - 2 * MAP_PAD) / w_km
    h = h_km * s + 2 * MAP_PAD
    if h > MAP_H_MAX:
        s = (MAP_H_MAX - 2 * MAP_PAD) / h_km
        h = MAP_H_MAX
    h = max(h, MAP_H_MIN)
    grid = next((g for g in (0.1, 0.2, 0.25, 0.5, 1, 2, 5, 10, 20, 50, 100) if g * s >= 60), 100)
    return {"W": MAP_W, "H": round(h), "c": c, "kx": kx, "ky": ky, "s": s, "grid": grid}


def search_filter_text(cfg):
    out = []
    if cfg["entire_place"]:
        out.append("entire places only")
    for a in cfg["amenity_ids"]:
        out.append(f"with {KNOWN_AMENITY_IDS[a]}" if a in KNOWN_AMENITY_IDS else f"with Airbnb amenity #{a}")
    return out


def controls_html(cfg, area_names, listings):
    e = html.escape
    rules = cfg["rules"]
    flags = [r["label"] for r in rules if r["action"] == "flag"]
    hls = [r["label"] for r in rules if r["action"] == "highlight"]
    lo = min((d["total"] for d in listings), default=0)
    step = max(1, 10 ** max(0, int(math.log10(max(cfg["max_total"], 1))) - 1))
    p_min = int(lo // step * step)
    p_max = int(math.ceil(cfg["max_total"] / step) * step)
    out = [f'''    <div class="ctl">
      <label class="h" for="fPrice">Max price for the stay</label>
      <div class="row"><input type="range" id="fPrice" min="{p_min}" max="{p_max}" step="{step}" value="{p_max}"><output id="oPrice" for="fPrice"></output></div>
    </div>''']
    if area_names:
        boxes = "".join(f'\n      <label class="chk"><input type="checkbox" id="fArea{i}" checked> {e(n)}</label>'
                        for i, n in enumerate(area_names))
        out.append(f'    <div class="ctl">\n      <span class="h">Area</span>{boxes}\n    </div>')
    out.append('''    <div class="ctl">
      <label class="h" for="fRating">Min rating</label>
      <div class="row"><select id="fRating"><option value="0">any</option><option value="4.5">4.5+</option><option value="4.7">4.7+</option><option value="4.85">4.85+</option></select>
        <label class="chk"><input type="checkbox" id="fUnrated" checked> include places with no reviews</label></div>
    </div>''')
    hide = []
    if cfg["studio_pattern"]:
        hide.append('<label class="chk"><input type="checkbox" id="fStudio"> hide studios and lofts</label>')
    hide += [f'<label class="chk"><input type="checkbox" id="fFlag{i}"> hide: {e(l)}</label>' for i, l in enumerate(flags)]
    hide += [f'<label class="chk"><input type="checkbox" id="fHl{i}"> must: {e(l)}</label>' for i, l in enumerate(hls)]
    hide += [f'<label class="chk"><input type="checkbox" id="fAm{i}"> must have: {e(a)}</label>'
             for i, a in enumerate(cfg["amenity_filters"])]
    if hide:
        out.append('    <div class="ctl">\n      <span class="h">Hide / must have</span>\n      '
                   + "\n      ".join(hide) + "\n    </div>")
    out.append('''    <div class="ctl">
      <label class="h" for="fShow">Show</label>
      <select id="fShow"><option value="active">all except deleted</option><option value="fav">favourites only</option><option value="deleted">deleted only</option><option value="excluded">excluded by rules</option></select>
      <div class="sub" id="markInfo"></div>
    </div>
    <div class="ctl">
      <label class="h" for="fSort">Sort</label>
      <select id="fSort"><option value="price">price, low first</option><option value="rating">rating, high first (weighted by review count)</option><option value="reviews">most reviews first</option></select>
    </div>''')
    return "\n".join(out)


def build_page(cfg, listings, areas, bbox, stats, searched_at, storage_key):
    """cfg: checked config; listings: records from pipeline.listing_record; areas: {name: rings};
    bbox: [s, w, n, e] that was searched; storage_key: names this page's localStorage entries."""
    points = [p for rings in areas.values() for ring in rings for p in ring] \
        or [[bbox[0], bbox[1]], [bbox[2], bbox[3]]]
    points += [[d["lat"], d["lng"]] for d in listings]
    area_names = list(areas)
    app = {
        "title": cfg["title"], "checkin": cfg["checkin"], "checkout": cfg["checkout"],
        "nights": config.nights(cfg), "adults": cfg["adults"], "currency": cfg["currency"],
        "max_total": cfg["max_total"], "min_rating": cfg["min_rating"],
        "border_tolerance_km": cfg["border_tolerance_km"], "searched_at": searched_at,
        "search_filters": search_filter_text(cfg),
        "key": "stayfinder:" + storage_key,
        "areas": [{"name": n, "rings": areas[n]} for n in area_names],
        "labels": area_labels(areas), "bbox": bbox, "map": fit_map(points),
        "flags": [r["label"] for r in cfg["rules"] if r["action"] == "flag"],
        "highlights": [r["label"] for r in cfg["rules"] if r["action"] == "highlight"],
        "rule_count": len(cfg["rules"]),
        "amenity_filters": cfg["amenity_filters"], "studio": bool(cfg["studio_pattern"]),
        "stats": stats, "listings": listings,
    }
    tpl = TEMPLATE.read_text(encoding="utf-8")
    for marker in ("{{TITLE}}", "{{CONTROLS}}", "{{APP_JSON}}"):
        if tpl.count(marker) != 1:
            raise RuntimeError(f"template.html must contain {marker} exactly once")
    values = {"TITLE": html.escape(cfg["title"]), "CONTROLS": controls_html(cfg, area_names, listings),
              "APP_JSON": embed_json(app)}
    return re.sub(r"\{\{(TITLE|CONTROLS|APP_JSON)\}\}", lambda m: values[m.group(1)], tpl)
