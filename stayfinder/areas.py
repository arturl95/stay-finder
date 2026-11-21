"""Neighbourhood borders from OpenStreetMap (Nominatim), and point-in-area checks.

Nominatim's usage policy asks for at most 1 request per second and an identifying User-Agent.
"""
import json
import math
import urllib.parse
import urllib.request

NOMINATIM_UA = {"User-Agent": "stay-finder/1.0 (open-source stay search script)"}


def geocode_url(query):
    return "https://nominatim.openstreetmap.org/search?" + urllib.parse.urlencode(
        {"q": query, "format": "json", "polygon_geojson": 1, "limit": 5})


def fetch_geocode_url(url):
    """Raw Nominatim answer for a geocode_url(). The caller keeps to 1 request per second."""
    req = urllib.request.Request(url, headers=NOMINATIM_UA)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def parse_geocode(res, query):
    """(display_name, rings) from a Nominatim answer: the first result that has an area outline.
    rings = list of [[lat, lng], ...] (outer rings only)."""
    for r in res:
        g = r.get("geojson") or {}
        if g.get("type") == "Polygon":
            polys = [g["coordinates"]]
        elif g.get("type") == "MultiPolygon":
            polys = g["coordinates"]
        else:
            continue
        rings = [[[lat, lng] for lng, lat in poly[0]] for poly in polys]
        return r["display_name"], [simplify(r_, 250) for r_ in rings]
    raise RuntimeError(f"OpenStreetMap has no area outline for {query!r}. Try a more specific name, "
                       f"e.g. 'Neighbourhood, City, Country', or give a bbox instead.")


def simplify(ring, max_points):
    if len(ring) <= max_points:
        return ring
    step = len(ring) / max_points
    out = [ring[int(i * step)] for i in range(max_points)]
    return out + [out[0]]


def bbox_of(areas):
    pts = [p for rings in areas.values() for ring in rings for p in ring]
    return [min(p[0] for p in pts), min(p[1] for p in pts), max(p[0] for p in pts), max(p[1] for p in pts)]


def _inside(lat, lng, ring):
    ins = False
    for a in range(len(ring)):
        y1, x1 = ring[a]
        y2, x2 = ring[a - 1]
        if (y1 > lat) != (y2 > lat) and lng < (x2 - x1) * (lat - y1) / (y2 - y1) + x1:
            ins = not ins
    return ins


def _km(lat, lng, p):
    kx = 111.32 * math.cos(math.radians(lat))
    return (p[0] - lat) * 110.57, (p[1] - lng) * kx


def distance_km(lat, lng, ring):
    """Distance from a point to a ring's border, in km (flat-earth approximation, fine at city scale)."""
    best = float("inf")
    for a in range(len(ring)):
        ay, ax = _km(lat, lng, ring[a - 1])
        by, bx = _km(lat, lng, ring[a])
        dy, dx = by - ay, bx - ax
        L = dx * dx + dy * dy
        t = 0 if L == 0 else max(0, min(1, -(ax * dx + ay * dy) / L))
        best = min(best, math.hypot(ax + t * dx, ay + t * dy))
    return best


def area_of(lat, lng, areas, tolerance_km):
    """Name of the area the point is in. Airbnb map pins are approximate, so a point within
    tolerance_km of an area's border also counts; the closest area wins. None if nowhere near."""
    for name, rings in areas.items():
        if any(_inside(lat, lng, r) for r in rings):
            return name
    near = [(min(distance_km(lat, lng, r) for r in rings), name) for name, rings in areas.items()]
    d, name = min(near)
    return name if d <= tolerance_km else None


def pad_bbox(bbox, km):
    """Grow [south, west, north, east] by km on every side."""
    s, w, n, e = bbox
    dlat = km / 110.57
    dlng = km / (111.32 * math.cos(math.radians((s + n) / 2)))
    return [s - dlat, w - dlng, n + dlat, e + dlng]
